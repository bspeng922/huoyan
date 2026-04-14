from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from huoyan.config import AppConfig, load_config
from huoyan.reporting import write_report
from huoyan.runner import run_app


def _filter_config(
    config: AppConfig,
    *,
    only_provider: str | None,
    only_model: str | None,
    suite: list[str] | None,
) -> AppConfig:
    data = config.model_dump(mode="python")
    providers = data["providers"]
    if only_provider:
        providers = [provider for provider in providers if provider["name"] == only_provider]
    if only_model:
        for provider in providers:
            provider["models"] = [
                model for model in provider["models"] if model["model"] == only_model
            ]
        providers = [provider for provider in providers if provider["models"]]
    if suite:
        for provider in providers:
            provider["defaults"]["enabled_suites"] = suite
            for model in provider["models"]:
                model["settings"]["enabled_suites"] = suite
    data["providers"] = providers
    return AppConfig.model_validate(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Huoyan relay benchmark toolkit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a benchmark against configured providers")
    run_parser.add_argument("config", help="Path to YAML config")
    run_parser.add_argument(
        "--output",
        help="Output directory for generated reports",
        default=None,
    )
    run_parser.add_argument("--only-provider", help="Only run a single provider by name")
    run_parser.add_argument("--only-model", help="Only run a single model by name")
    run_parser.add_argument(
        "--suite",
        action="append",
        choices=["authenticity", "performance", "agentic", "cost_security", "security_audit"],
        help="Limit execution to one or more suites",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command != "run":
        parser.error(f"Unsupported command: {args.command}")

    config = load_config(args.config)
    filtered = _filter_config(
        config,
        only_provider=args.only_provider,
        only_model=args.only_model,
        suite=args.suite,
    )
    report = asyncio.run(run_app(filtered))
    output_dir = args.output or filtered.report.output_dir
    written = write_report(
        report,
        output_dir,
        filtered.report.formats,
        write_transparency_log=filtered.report.write_transparency_log,
    )

    print(f"Overall status: {report.overall_status.value}")
    print(f"Summary: {report.summary}")
    for fmt, path in written.items():
        print(f"{fmt.upper()} report: {Path(path).resolve()}")
