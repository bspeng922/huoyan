from __future__ import annotations

from collections import Counter
from typing import Callable

from huoyan.client import OpenAICompatClient
from huoyan.config import AppConfig, ModelTarget, ProviderTarget, merge_settings
from huoyan.logging_utils import get_logger
from huoyan.models import ModelReport, ProbeResult, ProbeStatus, ProviderReport, RunReport
from huoyan.progress import ProgressCallback, emit_progress
from huoyan.suites import (
    run_agentic_suite,
    run_authenticity_suite,
    build_scorecard_results,
    run_cost_security_suite,
    run_performance_suite,
    run_security_audit_suite,
)
from huoyan.utils import infer_family
from huoyan.versioning import build_runtime_metadata


logger = get_logger(__name__)

SuiteRunner = Callable[..., object]

SUITE_RUNNERS: dict[str, SuiteRunner] = {
    "authenticity": run_authenticity_suite,
    "performance": run_performance_suite,
    "agentic": run_agentic_suite,
    "cost_security": run_cost_security_suite,
    "security_audit": run_security_audit_suite,
}

STATUS_PRIORITY = {
    ProbeStatus.PASS: 0,
    ProbeStatus.SKIP: 1,
    ProbeStatus.WARN: 2,
    ProbeStatus.FAIL: 3,
    ProbeStatus.ERROR: 4,
}

SUITE_PROBE_COUNTS: dict[str, int] = {
    "authenticity": 6,
    "performance": 3,
    "agentic": 4,
    "cost_security": 5,
    "security_audit": 5,
}
SCORECARD_PROBE_COUNT = 3


def summarize_results(results: list[ProbeResult]) -> dict[str, int]:
    counts = Counter(result.status.value for result in results)
    return dict(counts)


def collapse_status(results: list[ProbeResult]) -> ProbeStatus:
    if not results:
        return ProbeStatus.SKIP
    return max(results, key=lambda item: STATUS_PRIORITY[item.status]).status


def expected_model_result_count(enabled_suites: list[str]) -> int:
    return sum(SUITE_PROBE_COUNTS[suite_name] for suite_name in enabled_suites) + SCORECARD_PROBE_COUNT


def expected_run_result_count(config: AppConfig) -> int:
    total = 0
    for provider in config.providers:
        for model in provider.models:
            settings = merge_settings(provider.defaults, model.settings)
            total += expected_model_result_count(settings.enabled_suites)
    return total


async def run_model(
    client: OpenAICompatClient,
    provider: ProviderTarget,
    model: ModelTarget,
    progress_callback: ProgressCallback | None = None,
) -> ModelReport:
    settings = merge_settings(provider.defaults, model.settings)
    results: list[ProbeResult] = []
    logger.info(
        "Model run started provider=%s model=%s suites=%s",
        provider.name,
        model.model,
        ",".join(settings.enabled_suites),
    )

    async def model_progress(event: dict[str, object]) -> None:
        event_type = str(event.get("type", ""))
        probe = str(event.get("probe", ""))
        suite = str(event.get("suite", ""))
        if event_type == "probe_started":
            logger.info(
                "Probe started provider=%s model=%s suite=%s probe=%s",
                provider.name,
                model.model,
                suite,
                probe,
            )
        elif event_type == "probe_finished":
            logger.info(
                "Probe finished provider=%s model=%s suite=%s probe=%s",
                provider.name,
                model.model,
                suite,
                probe,
            )
        await emit_progress(
            progress_callback,
            {
                **event,
                "provider_name": provider.name,
                "model": model.model,
            },
        )

    for suite_name in settings.enabled_suites:
        logger.info(
            "Suite started provider=%s model=%s suite=%s",
            provider.name,
            model.model,
            suite_name,
        )
        runner = SUITE_RUNNERS[suite_name]
        if suite_name == "authenticity":
            suite_results = await runner(
                client,
                provider,
                model,
                settings,
                progress_callback=model_progress,
            )
        elif suite_name == "performance":
            suite_results = await runner(
                client,
                model,
                settings,
                progress_callback=model_progress,
            )
        elif suite_name == "agentic":
            suite_results = await runner(
                client,
                provider,
                model,
                settings,
                progress_callback=model_progress,
            )
        elif suite_name == "cost_security":
            suite_results = await runner(
                client,
                provider,
                model,
                settings,
                progress_callback=model_progress,
            )
        elif suite_name == "security_audit":
            suite_results = await runner(
                client,
                provider,
                model,
                settings,
                progress_callback=model_progress,
            )
        else:
            continue
        results.extend(suite_results)
        logger.info(
            "Suite finished provider=%s model=%s suite=%s results=%s",
            provider.name,
            model.model,
            suite_name,
            len(suite_results),
        )

    scorecard_results = build_scorecard_results(results)
    logger.info(
        "Building scorecards provider=%s model=%s count=%s",
        provider.name,
        model.model,
        len(scorecard_results),
    )
    for scorecard in scorecard_results:
        await model_progress(
            {
                "type": "probe_started",
                "suite": scorecard.suite,
                "probe": scorecard.probe,
            }
        )
        await model_progress(
            {
                "type": "probe_finished",
                "suite": scorecard.suite,
                "probe": scorecard.probe,
                "result": scorecard,
            }
        )
        results.append(scorecard)

    family = infer_family(model.model, model.claimed_family)
    report = ModelReport(
        provider_name=provider.name,
        provider_base_url=provider.base_url,
        model=model.model,
        claimed_family=family,
        overall_status=collapse_status(results),
        summary=summarize_results(results),
        settings=settings.model_dump(),
        results=results,
    )
    logger.info(
        "Model run finished provider=%s model=%s overall_status=%s result_count=%s",
        provider.name,
        model.model,
        report.overall_status.value,
        len(results),
    )
    return report


async def run_provider(
    provider: ProviderTarget,
    progress_callback: ProgressCallback | None = None,
) -> ProviderReport:
    logger.info(
        "Provider run started provider=%s models=%s base_url=%s",
        provider.name,
        len(provider.models),
        provider.base_url,
    )
    async with OpenAICompatClient(provider) as client:
        model_reports = [
            await run_model(
                client,
                provider,
                model,
                progress_callback=progress_callback,
            )
            for model in provider.models
        ]
        provider_audit_logs = list(client.audit_log_entries)
    flattened = [result for report in model_reports for result in report.results]
    report = ProviderReport(
        name=provider.name,
        base_url=provider.base_url,
        overall_status=collapse_status(flattened),
        summary=summarize_results(flattened),
        models=model_reports,
        audit_log_entries=provider_audit_logs,
    )
    logger.info(
        "Provider run finished provider=%s overall_status=%s result_count=%s",
        provider.name,
        report.overall_status.value,
        len(flattened),
    )
    return report


async def run_app(
    config: AppConfig,
    progress_callback: ProgressCallback | None = None,
) -> RunReport:
    logger.info(
        "Application run started providers=%s expected_results=%s",
        len(config.providers),
        expected_run_result_count(config),
    )
    providers = [
        await run_provider(provider, progress_callback=progress_callback)
        for provider in config.providers
    ]
    flattened = [result for provider in providers for model in provider.models for result in model.results]
    audit_log_entries = []
    for provider in providers:
        audit_log_entries.extend(getattr(provider, "audit_log_entries", []))
    report = RunReport(
        overall_status=collapse_status(flattened),
        summary=summarize_results(flattened),
        providers=providers,
        metadata=build_runtime_metadata(),
        audit_log_entries=audit_log_entries,
    )
    logger.info(
        "Application run finished overall_status=%s provider_count=%s result_count=%s",
        report.overall_status.value,
        len(providers),
        len(flattened),
    )
    return report
