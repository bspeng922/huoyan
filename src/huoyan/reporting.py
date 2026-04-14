from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from huoyan.models import ModelReport, ProbeResult, ProbeStatus, RunReport


STATUS_LABELS = {
    ProbeStatus.PASS.value: "通过",
    ProbeStatus.WARN.value: "警告",
    ProbeStatus.FAIL.value: "失败",
    ProbeStatus.SKIP.value: "跳过",
    ProbeStatus.ERROR.value: "错误",
}

SUITE_LABELS = {
    "authenticity": "模型保真度与掺水（Authenticity & Purity）",
    "performance": "性能与高可用（Performance & Reliability）",
    "agentic": "Agent 与长上下文支持（Context & Agentic Capabilities）",
    "cost_security": "成本核算与网络安全（Cost & Security）",
    "security_audit": "中转安全审计（Relay Security Audit）",
}

PROBE_LABELS = {
    "consistency_score": "综合保真度评分（Authenticity Consistency Score）",
    "identity": "专属身份测试（Identity Probe）",
    "acrostic_constraints": "智力探针：约束跟随（Constraint-Following Probe）",
    "boundary_reasoning": "智力探针：边界推理（Boundary Reasoning Probe）",
    "linguistic_fingerprint": "多语种理解测试（Linguistic Fingerprint Probe）",
    "response_consistency": "输出一致性（Response Consistency）",
    "ttft_tps": "首字延迟与吞吐（TTFT & TPS）",
    "concurrency": "高并发稳定性（Concurrency）",
    "availability": "可用性（Availability）",
    "tool_calling": "工具调用穿透率（Tool Calling Integrity）",
    "long_context_integrity": "超长上下文不丢失（Long Context Integrity）",
    "multimodal_support": "多模态支持（Multimodal Support）",
    "token_alignment": "计费透明度 / Token 对齐（Token Counting Alignment）",
    "tls_baseline": "TLS 加密基线（TLS Baseline）",
    "security_headers": "安全响应头（Security Headers）",
    "rate_limit_transparency": "限流透明度（Rate Limit Transparency）",
    "privacy_policy": "隐私策略记录（Privacy Policy）",
    "dependency_substitution": "依赖替换检测（Dependency Substitution）",
    "conditional_delivery": "条件投递检测（Conditional Delivery）",
    "error_response_leakage": "错误响应泄漏（Error Response Leakage）",
    "stream_integrity": "流完整性（Stream Integrity）",
    "system_prompt_injection": "系统提示注入检测（System Prompt Injection Detection）",
}


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Unsupported type for JSON serialization: {type(value)!r}")


def _render_metrics(metrics: dict[str, Any]) -> str:
    if not metrics:
        return ""
    return json.dumps(metrics, ensure_ascii=False, indent=2, default=_json_default)


def _suite_label(name: str) -> str:
    return SUITE_LABELS.get(name, name)


def _probe_label(name: str) -> str:
    return PROBE_LABELS.get(name, name)


def _status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def _find_result(model: ModelReport, probe: str) -> ProbeResult | None:
    for result in model.results:
        if result.probe == probe:
            return result
    return None


def _fmt_float(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:.1f}%"


def _fmt_ratio(left: Any, right: Any) -> str:
    if left is None or right in {None, 0}:
        return "-"
    return f"{left}/{right}"


def _summary_cn(result: ProbeResult) -> str:
    probe = result.probe
    metrics = result.metrics
    status = result.status

    if probe == "consistency_score":
        return (
            f"综合保真度评分为 {_fmt_float(metrics.get('consistency_score'))}/100，"
            f"等级为 {metrics.get('grade', '-') }，覆盖率 {_fmt_pct(metrics.get('coverage_ratio'))}。"
        )
    if probe == "identity":
        if status == ProbeStatus.PASS:
            return "模型自报身份与预期供应商线索一致。"
        if status == ProbeStatus.WARN:
            return "模型自报身份与预期线索不一致，但这只是弱信号，可能受中转商系统提示词、协议兼容层或安全包装影响，不能单独证明后端掉包。"
        if status == ProbeStatus.FAIL:
            return "身份探针出现强异常。"
    if probe == "acrostic_constraints":
        return f"四行约束中命中 {_fmt_ratio(metrics.get('valid_lines'), 4)}。"
    if probe == "boundary_reasoning":
        return f"代码边界推理命中 {_fmt_ratio(metrics.get('matched_lines'), metrics.get('expected_lines'))}。"
    if probe == "linguistic_fingerprint":
        return f"多语种/异构文本理解信号命中 {_fmt_ratio(metrics.get('signal_hits'), 3)}。"
    if probe == "response_consistency":
        return (
            f"固定 prompt 重复测试的平均相似度约 {_fmt_float(metrics.get('average_similarity'), 4)}，"
            f"语义锚点命中 {_fmt_ratio(metrics.get('anchor_group_hits'), metrics.get('anchor_group_total'))}。"
        )
    if probe == "ttft_tps":
        return (
            f"首字回复按 TTFT 统计，均值约 {_fmt_float(metrics.get('ttft_seconds'))} 秒；"
            f"P90 约 {_fmt_float((metrics.get('ttft_stats_seconds') or {}).get('p90'))} 秒；"
            f"首正文均值约 {_fmt_float(metrics.get('first_content_seconds'))} 秒。"
        )
    if probe == "concurrency":
        levels = metrics.get("levels", [])
        if levels:
            worst = min(levels, key=lambda item: item.get("success_rate", 1.0))
            return f"最差并发档位为 {worst.get('concurrency')}，成功率 {_fmt_pct(worst.get('success_rate'))}。"
    if probe == "availability":
        return f"短周期可用性为 {_fmt_pct(metrics.get('availability_ratio'))}。"
    if probe == "tool_calling":
        return f"工具调用返回 {metrics.get('tool_call_count', 0)} 次，结构兼容性正常。"
    if probe == "long_context_integrity":
        return f"长上下文 canary 命中 {_fmt_ratio(metrics.get('canary_hits'), 3)}。"
    if probe == "multimodal_support":
        if status == ProbeStatus.SKIP:
            return "当前模型或配置未启用多模态测试。"
        if status == ProbeStatus.PASS:
            return "多模态请求已成功完成。"
        if status == ProbeStatus.WARN:
            return "多模态请求已返回，但结果与预期提示不完全一致。"
    if probe == "token_alignment":
        return f"API 计费与本地估算倍率约为 {_fmt_float(metrics.get('ratio'), 4)}，差值 {metrics.get('delta_tokens', '-')} token。"
    if probe == "tls_baseline":
        return f"TLS 版本为 {metrics.get('tls_version', '-') }，证书剩余有效期约 {metrics.get('expires_in_days', '-')} 天。"
    if probe == "security_headers":
        return "检查 HSTS、X-Content-Type-Options、CSP 等 HTTP 安全头是否存在。"
    if probe == "rate_limit_transparency":
        return (
            f"共采样 {metrics.get('sampled_requests', '-')} 次，"
            f"观察到限流头：{'是' if metrics.get('saw_rate_limit_headers') else '否'}，"
            f"观察到 429：{'是' if metrics.get('saw_429') else '否'}。"
        )
    if probe == "privacy_policy":
        return "已记录隐私策略地址，仍需人工审阅条款。" if status == ProbeStatus.PASS else "未提供可记录的隐私策略地址。"
    if probe == "dependency_substitution":
        return f"依赖安装命令逐字命中 {_fmt_ratio(metrics.get('exact_matches'), metrics.get('total_cases'))}。"
    if probe == "conditional_delivery":
        if status == ProbeStatus.PASS:
            return f"经过 {metrics.get('warmup_requests', '-')} 次预热后，命令一致性未见变化。"
        return result.summary
    if probe == "error_response_leakage":
        return f"共测试 {metrics.get('tested_cases', '-')} 个坏请求，secret 泄漏 {metrics.get('secret_hits', 0)} 次，异常放行 {metrics.get('accepted_invalid_cases', 0)} 次。"
    if probe == "stream_integrity":
        total = metrics.get("event_count", metrics.get("chunk_count", "-"))
        return f"流式传输事件/分块数量为 {total}，当前未发现明显事件序列异常。"
    if probe == "system_prompt_injection":
        pattern_hits = metrics.get("disclosure_pattern_hits", 0)
        reported_count = metrics.get("reported_instruction_count")
        denied = metrics.get("denied_receiving_instructions", False)
        parts = []
        if pattern_hits:
            parts.append(f"匹配到 {pattern_hits} 个中转注入模式")
        if reported_count is not None and reported_count > 0:
            parts.append(f"模型报告收到 {reported_count} 条额外指令")
        if denied:
            parts.append("模型否认收到额外指令")
        if not parts:
            return "未检测到中转商注入系统提示词的迹象。"
        return "；".join(parts) + "。"

    return result.summary


def _consistency_value(result: ProbeResult) -> str:
    return (
        f"{_fmt_float(result.metrics.get('consistency_score'))}/100 "
        f"（{result.metrics.get('grade', '-')}，覆盖率 {_fmt_pct(result.metrics.get('coverage_ratio'))}）"
    )


def _focus_rows(model: ModelReport) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []

    consistency = _find_result(model, "consistency_score")
    if consistency:
        rows.append(("综合保真度评分", _consistency_value(consistency), _status_label(consistency.status.value), _summary_cn(consistency)))

    identity = _find_result(model, "identity")
    if identity:
        rows.append(("模型身份一致性", str(identity.evidence.get("response_excerpt", "-")), _status_label(identity.status.value), _summary_cn(identity)))

    acrostic = _find_result(model, "acrostic_constraints")
    if acrostic:
        rows.append(("约束跟随完成度", _fmt_ratio(acrostic.metrics.get("valid_lines"), 4), _status_label(acrostic.status.value), _summary_cn(acrostic)))

    boundary = _find_result(model, "boundary_reasoning")
    if boundary:
        rows.append(("边界推理命中数", _fmt_ratio(boundary.metrics.get("matched_lines"), boundary.metrics.get("expected_lines")), _status_label(boundary.status.value), _summary_cn(boundary)))

    linguistic = _find_result(model, "linguistic_fingerprint")
    if linguistic:
        rows.append(("多语种理解信号", _fmt_ratio(linguistic.metrics.get("signal_hits"), 3), _status_label(linguistic.status.value), _summary_cn(linguistic)))

    response_consistency = _find_result(model, "response_consistency")
    if response_consistency:
        rows.append(("输出一致性", _fmt_float(response_consistency.metrics.get("average_similarity"), 4), _status_label(response_consistency.status.value), _summary_cn(response_consistency)))

    ttft = _find_result(model, "ttft_tps")
    if ttft:
        rows.append(("首字回复延迟（TTFT）", f"{_fmt_float(ttft.metrics.get('ttft_seconds'))} s", _status_label(ttft.status.value), _summary_cn(ttft)))
        rows.append(("首字回复延迟 P90", f"{_fmt_float((ttft.metrics.get('ttft_stats_seconds') or {}).get('p90'))} s", "观测值", "多次流式采样后首次回复延迟的 P90。"))
        rows.append(("首正文延迟（TTFC）", f"{_fmt_float(ttft.metrics.get('first_content_seconds'))} s", "观测值", "从开始请求到首个正文内容 token 出现的时间。"))
        rows.append(("相邻 Token 延迟（ITL）", f"{_fmt_float(ttft.metrics.get('inter_token_latency_ms'))} ms", "观测值", "相邻 token 间平均延迟，近似按生成阶段耗时估算。"))
        rows.append(("请求总时延", f"{_fmt_float(ttft.metrics.get('request_latency_ms'))} ms", "观测值", "单次请求从发起到完整结束的总时延。"))
        rows.append(("流式采样次数", str(ttft.metrics.get("sample_count", "-")), "观测值", "当前性能探针的重复采样次数。"))
        rows.append(("输入序列长度", str(ttft.metrics.get("input_sequence_length", "-")), "观测值", "优先使用 API usage.input_tokens / prompt_tokens。"))
        rows.append(("输出序列长度", str(ttft.metrics.get("output_sequence_length", "-")), "观测值", "优先使用 API usage.output_tokens / completion_tokens。"))
        if ttft.metrics.get("api_reasoning_tokens") is not None:
            rows.append(("推理 Token 数", str(ttft.metrics.get("api_reasoning_tokens", "-")), "观测值", "如果 API 单独返回 reasoning tokens，则在这里展示。"))
            rows.append(("总输出 Token 数", str(ttft.metrics.get("api_output_tokens_total", "-")), "观测值", "包含 reasoning tokens 的总输出 token。"))
        rows.append(("输出 Token 吞吐", f"{_fmt_float(ttft.metrics.get('output_token_throughput_per_second'))} tok/s", "观测值", "生成阶段的输出 token 吞吐。"))
        rows.append(("请求吞吐", f"{_fmt_float(ttft.metrics.get('request_throughput_per_second'), 4)} req/s", "观测值", "基于当前单请求时延得到的近似请求吞吐。"))

    concurrency = _find_result(model, "concurrency")
    if concurrency:
        levels = concurrency.metrics.get("levels", [])
        if levels:
            worst = min(levels, key=lambda item: item.get("success_rate", 1.0))
            rows.append(("高并发稳定性", f"并发 {worst.get('concurrency')}，成功率 {_fmt_pct(worst.get('success_rate'))}", _status_label(concurrency.status.value), _summary_cn(concurrency)))

    availability = _find_result(model, "availability")
    if availability:
        rows.append(("短周期可用性", _fmt_pct(availability.metrics.get("availability_ratio")), _status_label(availability.status.value), _summary_cn(availability)))

    tool_calling = _find_result(model, "tool_calling")
    if tool_calling:
        rows.append(("工具调用穿透", f"{tool_calling.metrics.get('tool_call_count', 0)} 次工具调用", _status_label(tool_calling.status.value), _summary_cn(tool_calling)))

    long_context = _find_result(model, "long_context_integrity")
    if long_context:
        rows.append(("长上下文完整性", _fmt_ratio(long_context.metrics.get("canary_hits"), 3), _status_label(long_context.status.value), _summary_cn(long_context)))

    multimodal = _find_result(model, "multimodal_support")
    if multimodal:
        rows.append(("多模态支持", "-", _status_label(multimodal.status.value), _summary_cn(multimodal)))

    token_alignment = _find_result(model, "token_alignment")
    if token_alignment:
        rows.append(("Token 对齐倍率", _fmt_float(token_alignment.metrics.get("ratio"), 4), _status_label(token_alignment.status.value), _summary_cn(token_alignment)))

    tls = _find_result(model, "tls_baseline")
    if tls:
        rows.append(("TLS 基线", str(tls.metrics.get("tls_version", "-")), _status_label(tls.status.value), _summary_cn(tls)))

    security_headers = _find_result(model, "security_headers")
    if security_headers:
        rows.append(("安全响应头", _status_label(security_headers.status.value), _status_label(security_headers.status.value), _summary_cn(security_headers)))

    rate_limit = _find_result(model, "rate_limit_transparency")
    if rate_limit:
        rows.append(("限流透明度", "有头信息" if rate_limit.metrics.get("saw_rate_limit_headers") else "未观察到", _status_label(rate_limit.status.value), _summary_cn(rate_limit)))

    privacy = _find_result(model, "privacy_policy")
    if privacy:
        rows.append(("隐私策略记录", "已配置" if privacy.status == ProbeStatus.PASS else "未配置", _status_label(privacy.status.value), _summary_cn(privacy)))

    dependency = _find_result(model, "dependency_substitution")
    if dependency:
        rows.append(("依赖替换检测", _fmt_ratio(dependency.metrics.get("exact_matches"), dependency.metrics.get("total_cases")), _status_label(dependency.status.value), _summary_cn(dependency)))

    conditional = _find_result(model, "conditional_delivery")
    if conditional:
        rows.append(("条件投递检测", f"预热 {conditional.metrics.get('warmup_requests', '-')} 次", _status_label(conditional.status.value), _summary_cn(conditional)))

    leakage = _find_result(model, "error_response_leakage")
    if leakage:
        rows.append(("错误响应泄漏", f"泄漏 {leakage.metrics.get('secret_hits', 0)}，异常放行 {leakage.metrics.get('accepted_invalid_cases', 0)}", _status_label(leakage.status.value), _summary_cn(leakage)))

    stream = _find_result(model, "stream_integrity")
    if stream:
        rows.append(("流完整性", f"{stream.metrics.get('event_count', stream.metrics.get('chunk_count', '-'))} 个事件/块", _status_label(stream.status.value), _summary_cn(stream)))

    injection = _find_result(model, "system_prompt_injection")
    if injection:
        hits = injection.metrics.get("disclosure_pattern_hits", 0)
        rows.append(("系统提示注入", f"模式命中 {hits}", _status_label(injection.status.value), _summary_cn(injection)))

    return rows


def _brief_metric_value(result: ProbeResult) -> str:
    metrics = result.metrics
    probe = result.probe
    if probe == "consistency_score":
        return _consistency_value(result)
    if probe == "ttft_tps":
        return (
            f"首字回复均值 {_fmt_float(metrics.get('ttft_seconds'))} s / "
            f"P90 {_fmt_float((metrics.get('ttft_stats_seconds') or {}).get('p90'))} s / "
            f"TPS {_fmt_float(metrics.get('output_token_throughput_per_second'))} tok/s"
        )
    if probe == "concurrency":
        levels = metrics.get("levels", [])
        if not levels:
            return "-"
        worst = min(levels, key=lambda item: item.get("success_rate", 1.0))
        return f"并发 {worst.get('concurrency')} 成功率 {_fmt_pct(worst.get('success_rate'))}"
    if probe == "availability":
        return _fmt_pct(metrics.get("availability_ratio"))
    if probe == "tool_calling":
        return f"{metrics.get('tool_call_count', 0)} 次"
    if probe == "long_context_integrity":
        return _fmt_ratio(metrics.get("canary_hits"), 3)
    if probe == "token_alignment":
        return f"倍率 {_fmt_float(metrics.get('ratio'), 4)}"
    if probe == "tls_baseline":
        return str(metrics.get("tls_version", "-"))
    if probe == "security_headers":
        return str(metrics.get("http_status", "-"))
    if probe == "rate_limit_transparency":
        return f"{metrics.get('sampled_requests', '-')} 次"
    if probe == "dependency_substitution":
        return _fmt_ratio(metrics.get("exact_matches"), metrics.get("total_cases"))
    if probe == "conditional_delivery":
        return f"预热 {metrics.get('warmup_requests', '-')} 次"
    if probe == "error_response_leakage":
        return f"secret {metrics.get('secret_hits', 0)} / accepted {metrics.get('accepted_invalid_cases', 0)}"
    if probe == "stream_integrity":
        return str(metrics.get("event_count", metrics.get("chunk_count", "-")))
    if probe == "system_prompt_injection":
        return f"模式 {metrics.get('disclosure_pattern_hits', 0)}"
    if probe == "identity":
        return str(result.evidence.get("response_excerpt", "-"))
    if probe == "acrostic_constraints":
        return _fmt_ratio(metrics.get("valid_lines"), 4)
    if probe == "boundary_reasoning":
        return _fmt_ratio(metrics.get("matched_lines"), metrics.get("expected_lines"))
    if probe == "linguistic_fingerprint":
        return _fmt_ratio(metrics.get("signal_hits"), 3)
    if probe == "response_consistency":
        return f"相似度 {_fmt_float(metrics.get('average_similarity'), 4)}"
    return "-"


def _suite_summary_lines(model: ModelReport) -> list[str]:
    lines: list[str] = []
    for suite_name in SUITE_LABELS:
        suite_results = [result for result in model.results if result.suite == suite_name]
        if not suite_results:
            continue
        lines.append(f"#### {_suite_label(suite_name)}")
        lines.append("")
        if suite_name == "performance":
            lines.extend(_performance_breakdown_lines(model))
            continue
        lines.append("| 指标 | 结果 | 状态 | 说明 |")
        lines.append("| --- | --- | --- | --- |")
        for result in suite_results:
            lines.append(f"| {_probe_label(result.probe)} | {_brief_metric_value(result)} | {_status_label(result.status.value)} | {_summary_cn(result)} |")
        lines.append("")
    return lines


def _performance_breakdown_lines(model: ModelReport) -> list[str]:
    lines: list[str] = []
    ttft = _find_result(model, "ttft_tps")
    concurrency = _find_result(model, "concurrency")
    availability = _find_result(model, "availability")

    if ttft is not None:
        lines.append("响应启动指标：")
        lines.append("")
        lines.append("| 指标 | 结果 | 状态 | 说明 |")
        lines.append("| --- | --- | --- | --- |")
        lines.append(
            f"| 首字回复延迟（TTFT） | {_fmt_float(ttft.metrics.get('ttft_seconds'))} s | {_status_label(ttft.status.value)} | 按首次回复事件统计。 |"
        )
        lines.append(
            f"| 首字回复延迟 P90 | {_fmt_float((ttft.metrics.get('ttft_stats_seconds') or {}).get('p90'))} s | 观测值 | 多次采样后的首次回复延迟 P90。 |"
        )
        lines.append(
            f"| 首正文延迟（TTFC） | {_fmt_float(ttft.metrics.get('first_content_seconds'))} s | 观测值 | 从请求开始到首个正文 token 出现。 |"
        )
        lines.append(
            f"| 请求总时延 | {_fmt_float(ttft.metrics.get('request_latency_ms'))} ms | 观测值 | 单次请求从发起到完整结束。 |"
        )
        lines.append("")

        lines.append("正文生成指标：")
        lines.append("")
        lines.append("| 指标 | 结果 | 状态 | 说明 |")
        lines.append("| --- | --- | --- | --- |")
        lines.append(
            f"| 相邻 Token 延迟（ITL） | {_fmt_float(ttft.metrics.get('inter_token_latency_ms'))} ms | 观测值 | 近似按生成阶段耗时估算。 |"
        )
        lines.append(
            f"| 输出 Token 吞吐 | {_fmt_float(ttft.metrics.get('output_token_throughput_per_second'))} tok/s | 观测值 | 生成阶段的输出 token 吞吐。 |"
        )
        lines.append(
            f"| 输出序列长度 | {ttft.metrics.get('output_sequence_length', '-')} | 观测值 | 优先使用 API usage.output_tokens / completion_tokens。 |"
        )
        if ttft.metrics.get("api_reasoning_tokens") is not None:
            lines.append(
                f"| 推理 Token 数 | {ttft.metrics.get('api_reasoning_tokens', '-')} | 观测值 | API 单独返回的 reasoning tokens。 |"
            )
            lines.append(
                f"| 总输出 Token 数 | {ttft.metrics.get('api_output_tokens_total', '-')} | 观测值 | 包含 reasoning tokens 的总输出 token。 |"
            )
        lines.append(
            f"| 输入序列长度 | {ttft.metrics.get('input_sequence_length', '-')} | 观测值 | 优先使用 API usage.input_tokens / prompt_tokens。 |"
        )
        lines.append("")

    lines.append("负载与稳定性指标：")
    lines.append("")
    lines.append("| 指标 | 结果 | 状态 | 说明 |")
    lines.append("| --- | --- | --- | --- |")
    if concurrency is not None:
        levels = concurrency.metrics.get("levels", [])
        if levels:
            worst = min(levels, key=lambda item: item.get("success_rate", 1.0))
            lines.append(
                f"| 高并发稳定性 | 并发 {worst.get('concurrency')} 成功率 {_fmt_pct(worst.get('success_rate'))} | {_status_label(concurrency.status.value)} | {_summary_cn(concurrency)} |"
            )
            lines.append(
                f"| 并发批次请求吞吐 | {_fmt_float(worst.get('request_throughput_per_second'), 4)} req/s | 观测值 | 取最差并发档位的批次吞吐。 |"
            )
            lines.append(
                f"| 并发 P50 时延 | {_fmt_float(worst.get('p50_latency_seconds'))} s | 观测值 | 取最差并发档位。 |"
            )
            lines.append(
                f"| 并发 P95 时延 | {_fmt_float(worst.get('p95_latency_seconds'))} s | 观测值 | 取最差并发档位。 |"
            )
    if availability is not None:
        lines.append(
            f"| 短周期可用性 | {_fmt_pct(availability.metrics.get('availability_ratio'))} | {_status_label(availability.status.value)} | {_summary_cn(availability)} |"
        )
    if ttft is not None:
        lines.append(
            f"| 单请求近似吞吐 | {_fmt_float(ttft.metrics.get('request_throughput_per_second'), 4)} req/s | 观测值 | 基于单请求总时延估算，仅供参考。 |"
        )
    lines.append("")
    return lines


def _key_findings(model: ModelReport) -> list[str]:
    findings: list[str] = []
    consistency = _find_result(model, "consistency_score")
    if consistency is not None:
        findings.append(f"{_probe_label(consistency.probe)}：{_status_label(consistency.status.value)}，{_summary_cn(consistency)}")

    flagged = [result for result in model.results if result.status in {ProbeStatus.FAIL, ProbeStatus.WARN} and result.probe != "consistency_score"]
    flagged.sort(key=lambda item: (0 if item.status == ProbeStatus.FAIL else 1, item.suite, item.probe))
    for result in flagged[:4]:
        findings.append(f"{_probe_label(result.probe)}：{_status_label(result.status.value)}，{_summary_cn(result)}")
    if not findings:
        findings.append("本轮已执行指标均为通过，没有出现告警或失败。")
    return findings


def render_markdown(report: RunReport) -> str:
    lines: list[str] = []
    lines.append("# 火眼测试报告")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append(f"- 生成时间：`{report.generated_at.isoformat()}`")
    lines.append(f"- 总体状态：`{_status_label(report.overall_status.value)}`")
    lines.append(f"- 汇总：`{json.dumps(report.summary, ensure_ascii=False)}`")
    lines.append("")

    for provider in report.providers:
        lines.append(f"## 服务商：{provider.name}")
        lines.append("")
        lines.append(f"- Base URL：`{provider.base_url}`")
        lines.append(f"- 总体状态：`{_status_label(provider.overall_status.value)}`")
        lines.append(f"- 汇总：`{json.dumps(provider.summary, ensure_ascii=False)}`")
        lines.append("")

        for model in provider.models:
            lines.append(f"### 模型：{model.model}")
            lines.append("")
            lines.append(f"- 标称家族：`{model.claimed_family}`")
            lines.append(f"- 模型状态：`{_status_label(model.overall_status.value)}`")
            lines.append(f"- 结果汇总：`{json.dumps(model.summary, ensure_ascii=False)}`")
            lines.append("")

            lines.append("#### 重点结论")
            lines.append("")
            for finding in _key_findings(model):
                lines.append(f"- {finding}")
            lines.append("")

            lines.append("#### 关键指标总览")
            lines.append("")
            lines.append("| 关注指标 | 结果 | 状态 | 说明 |")
            lines.append("| --- | --- | --- | --- |")
            for label, value, status, note in _focus_rows(model):
                lines.append(f"| {label} | {value} | {status} | {note} |")
            lines.append("")

            lines.extend(_suite_summary_lines(model))

            lines.append("#### 原始明细")
            lines.append("")
            for result in model.results:
                if not result.metrics and not result.evidence:
                    continue
                lines.append(f"##### {_probe_label(result.probe)}")
                lines.append("")
                lines.append(f"- 所属维度：{_suite_label(result.suite)}")
                lines.append(f"- 状态：`{_status_label(result.status.value)}`")
                lines.append(f"- 结论：{_summary_cn(result)}")
                if result.summary != _summary_cn(result):
                    lines.append(f"- 英文原文：{result.summary}")
                lines.append("")
                if result.metrics:
                    lines.append("原始指标：")
                    lines.append("")
                    lines.append("```json")
                    lines.append(_render_metrics(result.metrics))
                    lines.append("```")
                    lines.append("")
                if result.evidence:
                    lines.append("原始证据：")
                    lines.append("")
                    lines.append("```json")
                    lines.append(_render_metrics(result.evidence))
                    lines.append("```")
                    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _report_slug(report: RunReport) -> str:
    model_names = sorted({model.model for provider in report.providers for model in provider.models})
    source = model_names[0] if len(model_names) == 1 else "multi-model"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", source).strip("-")
    return slug or "unknown-model"


def write_report(
    report: RunReport,
    output_dir: str,
    formats: list[str],
    *,
    write_transparency_log: bool = True,
) -> dict[str, Path]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.strftime("%Y%m%d-%H%M%S")
    base_name = f"huoyan-{_report_slug(report)}-{stamp}"
    written: dict[str, Path] = {}

    if "json" in formats:
        json_path = target_dir / f"{base_name}.json"
        json_path.write_text(
            report.model_dump_json(
                indent=2,
                exclude={
                    "audit_log_entries": True,
                    "providers": {"__all__": {"audit_log_entries"}},
                },
            ),
            encoding="utf-8",
        )
        written["json"] = json_path

    if "md" in formats:
        md_path = target_dir / f"{base_name}.md"
        md_path.write_text(render_markdown(report), encoding="utf-8")
        written["md"] = md_path

    if write_transparency_log and report.audit_log_entries:
        log_path = target_dir / f"{base_name}-transparency.ndjson"
        with log_path.open("w", encoding="utf-8") as handle:
            for entry in report.audit_log_entries:
                handle.write(json.dumps(entry, ensure_ascii=False, default=_json_default))
                handle.write("\n")
        written["ndjson"] = log_path

    return written
