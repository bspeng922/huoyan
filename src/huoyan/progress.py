from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from inspect import isawaitable
from typing import Any

from huoyan.models import ProbeResult


ProgressEvent = dict[str, Any]
ProgressCallback = Callable[[ProgressEvent], None | Awaitable[None]]
ProbeFactory = Callable[[], Awaitable[ProbeResult]]


async def emit_progress(
    progress_callback: ProgressCallback | None,
    event: ProgressEvent,
) -> None:
    if progress_callback is None:
        return
    maybe_awaitable = progress_callback(event)
    if isawaitable(maybe_awaitable):
        await maybe_awaitable


async def run_probe_sequence(
    *,
    suite: str,
    steps: Sequence[tuple[str, ProbeFactory]],
    progress_callback: ProgressCallback | None = None,
) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    for probe, factory in steps:
        await emit_progress(
            progress_callback,
            {
                "type": "probe_started",
                "suite": suite,
                "probe": probe,
            },
        )
        result = await factory()
        results.append(result)
        await emit_progress(
            progress_callback,
            {
                "type": "probe_finished",
                "suite": suite,
                "probe": result.probe,
                "result": result,
            },
        )
    return results
