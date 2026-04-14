from __future__ import annotations

from collections import Counter
from typing import Callable

from huoyan.client import OpenAICompatClient
from huoyan.config import AppConfig, ModelTarget, ProviderTarget, merge_settings
from huoyan.models import ModelReport, ProbeResult, ProbeStatus, ProviderReport, RunReport
from huoyan.suites import (
    run_agentic_suite,
    run_authenticity_suite,
    build_consistency_score_result,
    run_cost_security_suite,
    run_performance_suite,
    run_security_audit_suite,
)
from huoyan.utils import infer_family


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


def summarize_results(results: list[ProbeResult]) -> dict[str, int]:
    counts = Counter(result.status.value for result in results)
    return dict(counts)


def collapse_status(results: list[ProbeResult]) -> ProbeStatus:
    if not results:
        return ProbeStatus.SKIP
    return max(results, key=lambda item: STATUS_PRIORITY[item.status]).status


async def run_model(
    client: OpenAICompatClient,
    provider: ProviderTarget,
    model: ModelTarget,
) -> ModelReport:
    settings = merge_settings(provider.defaults, model.settings)
    results: list[ProbeResult] = []

    for suite_name in settings.enabled_suites:
        runner = SUITE_RUNNERS[suite_name]
        if suite_name == "authenticity":
            suite_results = await runner(client, provider, model, settings)
        elif suite_name == "performance":
            suite_results = await runner(client, model, settings)
        elif suite_name == "agentic":
            suite_results = await runner(client, model, settings)
        elif suite_name == "cost_security":
            suite_results = await runner(client, provider, model, settings)
        elif suite_name == "security_audit":
            suite_results = await runner(client, provider, model, settings)
        else:
            continue
        results.extend(suite_results)

    results.append(build_consistency_score_result(results))

    family = infer_family(model.model, model.claimed_family)
    return ModelReport(
        provider_name=provider.name,
        provider_base_url=provider.base_url,
        model=model.model,
        claimed_family=family,
        overall_status=collapse_status(results),
        summary=summarize_results(results),
        settings=settings.model_dump(),
        results=results,
    )


async def run_provider(provider: ProviderTarget) -> ProviderReport:
    async with OpenAICompatClient(provider) as client:
        model_reports = [await run_model(client, provider, model) for model in provider.models]
        provider_audit_logs = list(client.audit_log_entries)
    flattened = [result for report in model_reports for result in report.results]
    return ProviderReport(
        name=provider.name,
        base_url=provider.base_url,
        overall_status=collapse_status(flattened),
        summary=summarize_results(flattened),
        models=model_reports,
        audit_log_entries=provider_audit_logs,
    )


async def run_app(config: AppConfig) -> RunReport:
    providers = [await run_provider(provider) for provider in config.providers]
    flattened = [result for provider in providers for model in provider.models for result in model.results]
    audit_log_entries = []
    for provider in providers:
        audit_log_entries.extend(getattr(provider, "audit_log_entries", []))
    return RunReport(
        overall_status=collapse_status(flattened),
        summary=summarize_results(flattened),
        providers=providers,
        audit_log_entries=audit_log_entries,
    )
