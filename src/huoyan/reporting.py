from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from huoyan.logging_utils import get_logger
from huoyan.models import ModelReport, ProbeResult, ProbeStatus, RunReport

logger = get_logger(__name__)


STATUS_LABELS = {
    ProbeStatus.PASS.value: "通过",
    ProbeStatus.WARN.value: "警告",
    ProbeStatus.FAIL.value: "失败",
    ProbeStatus.SKIP.value: "跳过",
    ProbeStatus.ERROR.value: "错误",
}

SUITE_LABELS = {
    "scorecard": "评分卡（Scorecards）",
    "authenticity": "模型保真与路由侧写（Authenticity & Routing Signals）",
    "performance": "性能与稳定性（Performance & Reliability）",
    "agentic": "工具链路与上下文能力（Agentic & Context）",
    "cost_security": "计量与入口安全（Cost & Security）",
    "security_audit": "中转安全审计（Relay Security Audit）",
}

PROBE_LABELS = {
    "capability_score": "能力评分卡（Capability Scorecard）",
    "protocol_score": "协议评分卡（Protocol Scorecard）",
    "security_score": "安全评分卡（Security Scorecard）",
    "identity": "身份自报探针（Identity Probe）",
    "capability_fingerprint": "能力侧写探针（Capability Fingerprint）",
    "acrostic_constraints": "约束跟随探针（Constraint-Following Probe）",
    "boundary_reasoning": "边界推理探针（Boundary Reasoning Probe）",
    "linguistic_fingerprint": "多语种理解探针（Linguistic Fingerprint Probe）",
    "response_consistency": "短窗口输出一致性抽样（Response Consistency Spot Check）",
    "ttft_tps": "首字延迟与生成吞吐（TTFT & Generation Throughput）",
    "concurrency": "并发稳定性（Concurrency）",
    "availability": "短窗口可用性采样（Short-Window Availability Sampling）",
    "tool_calling": "单轮工具调用完整性（Tool Calling Integrity）",
    "multi_turn_tool": "多轮工具链路完整性（Multi-Turn Tool Integrity）",
    "long_context_integrity": "长上下文完整性（Long Context Integrity）",
    "multimodal_support": "多模态支持（Multimodal Support）",
    "token_alignment": "Usage Token 口径对齐（Usage Token Alignment）",
    "tls_baseline": "TLS 基线（TLS Baseline）",
    "security_headers": "API 入口安全响应头快照（Security Headers Snapshot）",
    "rate_limit_transparency": "限流元数据透明度（Rate-Limit Metadata Transparency）",
    "privacy_policy": "隐私策略链接记录（Privacy Policy Link Record）",
    "dependency_substitution": "安装命令工具参数完整性（Install Command Tool-Path Integrity）",
    "conditional_delivery": "条件投递抽样（Conditional Delivery Spot Check）",
    "error_response_leakage": "错误响应泄漏（Error Response Leakage）",
    "stream_integrity": "流式协议完整性（Stream Integrity）",
    "system_prompt_injection": "系统提示注入披露探针（System Prompt Injection Disclosure Probe）",
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


def _escape_table_cell(value: Any) -> str:
    text = str(value or "-").replace("\r", " ").replace("\n", "<br>")
    return text.replace("|", "\\|")


def _capability_challenge_lines(result: ProbeResult) -> list[str]:
    challenge_results = result.evidence.get("challenge_results")
    if not isinstance(challenge_results, list) or not challenge_results:
        return []
    lines = ["逐题结果：", "", "| 题目 | 标准答案 | 模型回复 | 结果 |", "| --- | --- | --- | --- |"]
    for item in challenge_results:
        if not isinstance(item, dict):
            continue
        lines.append(
            "| "
            + _escape_table_cell(item.get("question"))
            + " | "
            + _escape_table_cell(item.get("expected_answer"))
            + " | "
            + _escape_table_cell(item.get("response_excerpt"))
            + " | "
            + ("通过" if item.get("passed") else "未命中")
            + " |"
        )
    lines.append("")
    return lines


def _summary_cn(result: ProbeResult) -> str:
    probe = result.probe
    metrics = result.metrics
    status = result.status

    if probe in {"capability_score", "protocol_score", "security_score"}:
        return (
            f"评分 {_fmt_float(metrics.get('score'))}/100，"
            f"等级 {metrics.get('grade', '-')}，覆盖率 {_fmt_pct(metrics.get('coverage_ratio'))}。"
        )
    if probe == "identity":
        if status == ProbeStatus.PASS:
            return "模型自报身份与预期家族线索一致，但这仍只是弱信号。"
        if status == ProbeStatus.WARN:
            return "模型自报身份与预期线索不一致；兼容协议、系统提示和安全包装都可能影响结果。"
        if status == ProbeStatus.FAIL:
            return "身份自报出现强异常。"
    if probe == "capability_fingerprint":
        return (
            f"共答对 {_fmt_ratio(metrics.get('correct_count'), metrics.get('total_challenges'))}，"
            f"当前家族阈值为 {metrics.get('family_threshold', '-')}。"
        )
    if probe == "acrostic_constraints":
        return f"四行约束命中 {_fmt_ratio(metrics.get('valid_lines'), 4)}。"
    if probe == "boundary_reasoning":
        return f"代码边界推理命中 {_fmt_ratio(metrics.get('matched_lines'), metrics.get('expected_lines'))}。"
    if probe == "linguistic_fingerprint":
        return f"多语种/异构文本理解信号命中 {_fmt_ratio(metrics.get('signal_hits'), 3)}。"
    if probe == "response_consistency":
        return (
            f"短窗口重复请求平均相似度约 {_fmt_float(metrics.get('average_similarity'), 4)}，"
            f"平均锚点覆盖 {_fmt_pct(metrics.get('average_anchor_coverage'))}，"
            f"完整覆盖 {_fmt_ratio(metrics.get('complete_response_count'), metrics.get('run_count'))}。"
        )
    if probe == "ttft_tps":
        ttft_basis = metrics.get("ttft_observed_basis") or "avg"
        return (
            f"TTFT 均值约 {_fmt_float(metrics.get('ttft_seconds'))} 秒，"
            f"本次判定依据 {ttft_basis}={_fmt_float(metrics.get('ttft_observed_seconds'))} 秒，"
            f"首正文均值约 {_fmt_float(metrics.get('first_content_seconds'))} 秒。"
        )
    if probe == "concurrency":
        levels = metrics.get("levels", [])
        if levels:
            worst = min(levels, key=lambda item: item.get("success_rate", 1.0))
            return f"最差并发档位为 {worst.get('concurrency')}，成功率 {_fmt_pct(worst.get('success_rate'))}。"
    if probe == "availability":
        return (
            f"短窗口采样可用率 {_fmt_pct(metrics.get('availability_ratio'))}，"
            f"失败 {metrics.get('failure_count', '-')} 次，共 {metrics.get('sample_count', '-')} 次，窗口约 {_fmt_float(metrics.get('sample_window_seconds'))} 秒。"
            " 这只是健康快照，不代表长期 SLA。"
        )
    if probe == "tool_calling":
        return f"单轮工具调用返回 {metrics.get('tool_call_count', 0)} 次，JSON 结构兼容。"
    if probe == "multi_turn_tool":
        return (
            f"结构化字段命中 {_fmt_ratio(metrics.get('matched_fields'), metrics.get('required_fields'))}；"
            f"温度字段：{'是' if metrics.get('temperature_ok') else '否'}，"
            f"建议字段：{'是' if metrics.get('clothing_advice_ok') else '否'}。"
        )
    if probe == "long_context_integrity":
        return (
            f"最长通过长度 {metrics.get('max_preserved_target_chars', '-')} 字符，"
            f"首个失效点 {metrics.get('first_failed_target_chars', '-')}。"
        )
    if probe == "multimodal_support":
        if status == ProbeStatus.SKIP:
            return "当前模型或配置未启用多模态测试。"
        if status == ProbeStatus.PASS:
            return "多模态请求已成功返回。"
        if status == ProbeStatus.WARN:
            return "多模态请求已返回，但结果与预期提示不完全一致。"
    if probe == "token_alignment":
        prompt_ratio = _fmt_float(metrics.get("prompt_ratio", metrics.get("ratio")), 4)
        output_ratio = _fmt_float(metrics.get("output_ratio"), 4)
        if metrics.get("approximate"):
            return (
                f"本地 tokenizer 仅能近似估算；输入 ratio 约 {prompt_ratio}，输出 ratio 约 {output_ratio}。"
                " 结果仅供 usage token 口径参考，不是价格倍率审计。"
            )
        if metrics.get("output_exact_match") is False:
            return (
                f"输入 usage-token ratio 约 {prompt_ratio}；模型未精确回显预期输出，"
                f"输出 ratio {output_ratio} 只能弱参考。"
            )
        return (
            f"输入 usage-token ratio 约 {prompt_ratio}，输出 ratio 约 {output_ratio}。"
            " 这反映 usage 计量口径，不直接代表计费倍率。"
        )
    if probe == "tls_baseline":
        return f"TLS 版本 {metrics.get('tls_version', '-')}，证书剩余有效期约 {metrics.get('expires_in_days', '-')} 天。"
    if probe == "security_headers":
        return "仅对 sampled API 入口检查 HSTS 与 X-Content-Type-Options，不代表全站安全审计。"
    if probe == "rate_limit_transparency":
        return (
            f"共采样 {metrics.get('sampled_requests', '-')} 次，"
            f"主动突发 {metrics.get('active_burst_size', '-')} 次，"
            f"观察到限流元数据：{'是' if metrics.get('saw_rate_limit_headers') else '否'}，"
            f"观察到 429：{'是' if metrics.get('saw_429') else '否'}。"
        )
    if probe == "privacy_policy":
        if status == ProbeStatus.PASS:
            return "已记录隐私策略链接，但未对条款内容做自动审计。"
        return "未提供可记录的隐私策略链接。"
    if probe == "dependency_substitution":
        return (
            f"固定安装命令在 tool 路径上逐字命中 {_fmt_ratio(metrics.get('exact_matches'), metrics.get('total_cases'))}。"
            " 这不是对通用 dependency poisoning 的全面证伪。"
        )
    if probe == "conditional_delivery":
        if status == ProbeStatus.PASS:
            return (
                f"经过 {metrics.get('warmup_requests', '-')} 次预热后，命令一致性未见变化。"
                " 这仍只是短窗口抽样。"
            )
        return result.summary
    if probe == "error_response_leakage":
        return (
            f"共测试 {metrics.get('tested_cases', '-')} 个坏请求，"
            f"secret 泄漏 {metrics.get('secret_hits', 0)} 次，"
            f"异常放行 {metrics.get('accepted_invalid_cases', 0)} 次。"
        )
    if probe == "stream_integrity":
        total = metrics.get("event_count", metrics.get("chunk_count", "-"))
        return f"流式事件/块数为 {total}，当前未见明显事件序列异常。"
    if probe == "system_prompt_injection":
        if status == ProbeStatus.SKIP:
            return "模型没有可靠披露前置指令；这不能证明不存在中转注入。"
        pattern_hits = metrics.get("disclosure_pattern_hits", 0)
        reported_count = metrics.get("reported_instruction_count")
        parts: list[str] = []
        if pattern_hits:
            parts.append(f"命中 {pattern_hits} 个注入模式")
        if reported_count is not None and reported_count > 0:
            parts.append(f"模型声称收到了 {reported_count} 条前置指令")
        if not parts:
            return "未观察到可靠披露。"
        return "，".join(parts) + "。"

    return result.summary


def _scorecard_value(result: ProbeResult) -> str:
    return (
        f"{_fmt_float(result.metrics.get('score'))}/100 "
        f"({result.metrics.get('grade', '-')}, 覆盖率 {_fmt_pct(result.metrics.get('coverage_ratio'))})"
    )


def _focus_rows(model: ModelReport) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []

    for probe, label in [
        ("capability_score", "能力评分卡"),
        ("protocol_score", "协议评分卡"),
        ("security_score", "安全评分卡"),
    ]:
        result = _find_result(model, probe)
        if result:
            rows.append((label, _scorecard_value(result), _status_label(result.status.value), _summary_cn(result)))

    identity = _find_result(model, "identity")
    if identity:
        rows.append(("身份自报", str(identity.evidence.get("response_excerpt", "-")), _status_label(identity.status.value), _summary_cn(identity)))

    capability = _find_result(model, "capability_fingerprint")
    if capability:
        rows.append(("能力侧写", _fmt_ratio(capability.metrics.get("correct_count"), capability.metrics.get("total_challenges")), _status_label(capability.status.value), _summary_cn(capability)))

    acrostic = _find_result(model, "acrostic_constraints")
    if acrostic:
        rows.append(("约束跟随", _fmt_ratio(acrostic.metrics.get("valid_lines"), 4), _status_label(acrostic.status.value), _summary_cn(acrostic)))

    boundary = _find_result(model, "boundary_reasoning")
    if boundary:
        rows.append(("边界推理", _fmt_ratio(boundary.metrics.get("matched_lines"), boundary.metrics.get("expected_lines")), _status_label(boundary.status.value), _summary_cn(boundary)))

    linguistic = _find_result(model, "linguistic_fingerprint")
    if linguistic:
        rows.append(("多语种理解", _fmt_ratio(linguistic.metrics.get("signal_hits"), 3), _status_label(linguistic.status.value), _summary_cn(linguistic)))

    response_consistency = _find_result(model, "response_consistency")
    if response_consistency:
        rows.append(("短窗口输出一致性", _fmt_pct(response_consistency.metrics.get("average_anchor_coverage")), _status_label(response_consistency.status.value), _summary_cn(response_consistency)))

    ttft = _find_result(model, "ttft_tps")
    if ttft:
        rows.append(("首字回复延迟（TTFT）", f"{_fmt_float(ttft.metrics.get('ttft_seconds'))} s", _status_label(ttft.status.value), _summary_cn(ttft)))
        rows.append(("TTFT 判定口径", str(ttft.metrics.get("ttft_observed_basis", "-")), "观测值", "本轮 TTFT 判定实际采用的统计口径。"))
        rows.append(("首正文延迟（TTFC）", f"{_fmt_float(ttft.metrics.get('first_content_seconds'))} s", "观测值", "从开始请求到首个正文 token 出现。"))
        rows.append(("相邻内容事件延迟", f"{_fmt_float(ttft.metrics.get('inter_event_latency_ms'))} ms", "观测值", "按实际流式正文事件到达时间差统计。"))
        rows.append(("估算相邻 Token 延迟（ITL）", f"{_fmt_float(ttft.metrics.get('inter_token_latency_ms'))} ms", "观测值", "按生成阶段总耗时估算，仅供参考。"))
        rows.append(("请求总时延", f"{_fmt_float(ttft.metrics.get('request_latency_ms'))} ms", "观测值", "单次请求从发起到完整结束的总时延。"))
        rows.append(("流式采样次数", str(ttft.metrics.get("sample_count", "-")), "观测值", "当前性能探针的重复采样次数。"))
        rows.append(("输入序列长度", str(ttft.metrics.get("input_sequence_length", "-")), "观测值", "优先使用 API usage.input_tokens / prompt_tokens。"))
        rows.append(("输出序列长度", str(ttft.metrics.get("output_sequence_length", "-")), "观测值", "优先使用 API usage.output_tokens / completion_tokens。"))
        if ttft.metrics.get("api_reasoning_tokens") is not None:
            rows.append(("推理 Token 数", str(ttft.metrics.get("api_reasoning_tokens", "-")), "观测值", "如果 API 单独返回 reasoning tokens，则在这里展示。"))
            rows.append(("总输出 Token 数", str(ttft.metrics.get("api_output_tokens_total", "-")), "观测值", "包含 reasoning tokens 的总输出 token。"))
        rows.append(("输出 Token 吞吐", f"{_fmt_float(ttft.metrics.get('output_token_throughput_per_second'))} tok/s", "观测值", "生成阶段的输出 token 吞吐。"))
        rows.append(("单请求倒数吞吐（参考）", f"{_fmt_float(ttft.metrics.get('request_throughput_per_second'), 4)} req/s", "观测值", "基于单请求总时延的倒数估算，不代表稳态吞吐能力。"))

    concurrency = _find_result(model, "concurrency")
    if concurrency:
        levels = concurrency.metrics.get("levels", [])
        if levels:
            worst = min(levels, key=lambda item: item.get("success_rate", 1.0))
            rows.append(("并发稳定性", f"并发 {worst.get('concurrency')}，成功率 {_fmt_pct(worst.get('success_rate'))}", _status_label(concurrency.status.value), _summary_cn(concurrency)))

    availability = _find_result(model, "availability")
    if availability:
        rows.append(("短窗口可用性采样", _fmt_pct(availability.metrics.get("availability_ratio")), _status_label(availability.status.value), _summary_cn(availability)))

    tool_calling = _find_result(model, "tool_calling")
    if tool_calling:
        rows.append(("单轮工具调用完整性", f"{tool_calling.metrics.get('tool_call_count', 0)} 次", _status_label(tool_calling.status.value), _summary_cn(tool_calling)))

    multi_turn_tool = _find_result(model, "multi_turn_tool")
    if multi_turn_tool:
        rows.append(("多轮工具链路完整性", "-", _status_label(multi_turn_tool.status.value), _summary_cn(multi_turn_tool)))

    long_context = _find_result(model, "long_context_integrity")
    if long_context:
        rows.append(("长上下文完整性", str(long_context.metrics.get("max_preserved_target_chars", "-")), _status_label(long_context.status.value), _summary_cn(long_context)))

    multimodal = _find_result(model, "multimodal_support")
    if multimodal:
        rows.append(("多模态支持", "-", _status_label(multimodal.status.value), _summary_cn(multimodal)))

    token_alignment = _find_result(model, "token_alignment")
    if token_alignment:
        prompt_ratio = _fmt_float(token_alignment.metrics.get("prompt_ratio", token_alignment.metrics.get("ratio")), 4)
        output_ratio = _fmt_float(token_alignment.metrics.get("output_ratio"), 4)
        rows.append(("Usage Token 口径比值", f"输入 {prompt_ratio} / 输出 {output_ratio}", _status_label(token_alignment.status.value), _summary_cn(token_alignment)))

    tls = _find_result(model, "tls_baseline")
    if tls:
        rows.append(("TLS 基线", str(tls.metrics.get("tls_version", "-")), _status_label(tls.status.value), _summary_cn(tls)))

    security_headers = _find_result(model, "security_headers")
    if security_headers:
        rows.append(("API 入口安全头", _status_label(security_headers.status.value), _status_label(security_headers.status.value), _summary_cn(security_headers)))

    rate_limit = _find_result(model, "rate_limit_transparency")
    if rate_limit:
        rows.append(("限流元数据", "有头信息" if rate_limit.metrics.get("saw_rate_limit_headers") else "未观察到", _status_label(rate_limit.status.value), _summary_cn(rate_limit)))

    privacy = _find_result(model, "privacy_policy")
    if privacy:
        rows.append(("隐私策略链接", "已配置" if privacy.status == ProbeStatus.PASS else "未配置", _status_label(privacy.status.value), _summary_cn(privacy)))

    dependency = _find_result(model, "dependency_substitution")
    if dependency:
        rows.append(("安装命令完整性", _fmt_ratio(dependency.metrics.get("exact_matches"), dependency.metrics.get("total_cases")), _status_label(dependency.status.value), _summary_cn(dependency)))

    conditional = _find_result(model, "conditional_delivery")
    if conditional:
        rows.append(("条件投递抽样", f"预热 {conditional.metrics.get('warmup_requests', '-')} 次", _status_label(conditional.status.value), _summary_cn(conditional)))

    leakage = _find_result(model, "error_response_leakage")
    if leakage:
        rows.append(("错误响应泄漏", f"泄漏 {leakage.metrics.get('secret_hits', 0)}，异常放行 {leakage.metrics.get('accepted_invalid_cases', 0)}", _status_label(leakage.status.value), _summary_cn(leakage)))

    stream = _find_result(model, "stream_integrity")
    if stream:
        rows.append(("流式协议完整性", f"{stream.metrics.get('event_count', stream.metrics.get('chunk_count', '-'))} 个事件/块", _status_label(stream.status.value), _summary_cn(stream)))

    injection = _find_result(model, "system_prompt_injection")
    if injection:
        rows.append(("系统提示注入披露", f"模式命中 {injection.metrics.get('disclosure_pattern_hits', 0)}", _status_label(injection.status.value), _summary_cn(injection)))

    return rows


def _brief_metric_value(result: ProbeResult) -> str:
    metrics = result.metrics
    probe = result.probe
    if probe in {"capability_score", "protocol_score", "security_score"}:
        return _scorecard_value(result)
    if probe == "ttft_tps":
        return (
            f"TTFT 均值 {_fmt_float(metrics.get('ttft_seconds'))} s / "
            f"{metrics.get('ttft_observed_basis', 'avg')} {_fmt_float(metrics.get('ttft_observed_seconds'))} s / "
            f"TPS {_fmt_float(metrics.get('output_token_throughput_per_second'))} tok/s"
        )
    if probe == "concurrency":
        levels = metrics.get("levels", [])
        if not levels:
            return "-"
        worst = min(levels, key=lambda item: item.get("success_rate", 1.0))
        return f"并发 {worst.get('concurrency')} 成功率 {_fmt_pct(worst.get('success_rate'))}"
    if probe == "availability":
        return f"{_fmt_pct(metrics.get('availability_ratio'))}（快照）"
    if probe == "tool_calling":
        return f"{metrics.get('tool_call_count', 0)} 次"
    if probe == "multi_turn_tool":
        return _fmt_ratio(metrics.get("matched_fields"), metrics.get("required_fields"))
    if probe == "long_context_integrity":
        return f"最长通过 {metrics.get('max_preserved_target_chars', '-')}"
    if probe == "token_alignment":
        return f"输入 {_fmt_float(metrics.get('prompt_ratio', metrics.get('ratio')), 4)} / 输出 {_fmt_float(metrics.get('output_ratio'), 4)}"
    if probe == "tls_baseline":
        return str(metrics.get("tls_version", "-"))
    if probe == "security_headers":
        return str(metrics.get("http_status", "-"))
    if probe == "rate_limit_transparency":
        return f"{metrics.get('sampled_requests', '-')} 次"
    if probe == "dependency_substitution":
        return f"{_fmt_ratio(metrics.get('exact_matches'), metrics.get('total_cases'))}（tool）"
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
    if probe == "capability_fingerprint":
        return _fmt_ratio(metrics.get("correct_count"), metrics.get("total_challenges"))
    if probe == "acrostic_constraints":
        return _fmt_ratio(metrics.get("valid_lines"), 4)
    if probe == "boundary_reasoning":
        return _fmt_ratio(metrics.get("matched_lines"), metrics.get("expected_lines"))
    if probe == "linguistic_fingerprint":
        return _fmt_ratio(metrics.get("signal_hits"), 3)
    if probe == "response_consistency":
        return f"锚点覆盖 {_fmt_pct(metrics.get('average_anchor_coverage'))}"
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
        lines.append(f"| 首字回复延迟（TTFT） | {_fmt_float(ttft.metrics.get('ttft_seconds'))} s | {_status_label(ttft.status.value)} | 按首次回复事件统计。 |")
        lines.append(f"| TTFT 判定口径 | {ttft.metrics.get('ttft_observed_basis', '-')} | 观测值 | 本轮状态判定实际使用的统计口径。 |")
        lines.append(f"| TTFT 判定值 | {_fmt_float(ttft.metrics.get('ttft_observed_seconds'))} s | 观测值 | 与阈值比较时实际使用的 TTFT 数值。 |")
        lines.append(f"| 首正文延迟（TTFC） | {_fmt_float(ttft.metrics.get('first_content_seconds'))} s | 观测值 | 从请求开始到首个正文 token 出现。 |")
        lines.append(f"| 请求总时延 | {_fmt_float(ttft.metrics.get('request_latency_ms'))} ms | 观测值 | 单次请求从发起到完整结束。 |")
        lines.append("")

        lines.append("正文生成指标：")
        lines.append("")
        lines.append("| 指标 | 结果 | 状态 | 说明 |")
        lines.append("| --- | --- | --- | --- |")
        lines.append(f"| 相邻内容事件延迟 | {_fmt_float(ttft.metrics.get('inter_event_latency_ms'))} ms | 观测值 | 按实际流式正文事件到达时间差统计。 |")
        lines.append(f"| 估算相邻 Token 延迟（ITL） | {_fmt_float(ttft.metrics.get('inter_token_latency_ms'))} ms | 观测值 | 按生成阶段总耗时估算，仅供参考。 |")
        lines.append(f"| 输出 Token 吞吐 | {_fmt_float(ttft.metrics.get('output_token_throughput_per_second'))} tok/s | 观测值 | 生成阶段的输出 token 吞吐。 |")
        lines.append(f"| 输出序列长度 | {ttft.metrics.get('output_sequence_length', '-')} | 观测值 | 优先使用 API usage.output_tokens / completion_tokens。 |")
        if ttft.metrics.get("api_reasoning_tokens") is not None:
            lines.append(f"| 推理 Token 数 | {ttft.metrics.get('api_reasoning_tokens', '-')} | 观测值 | API 单独返回的 reasoning tokens。 |")
            lines.append(f"| 总输出 Token 数 | {ttft.metrics.get('api_output_tokens_total', '-')} | 观测值 | 包含 reasoning tokens 的总输出 token。 |")
        lines.append(f"| 输入序列长度 | {ttft.metrics.get('input_sequence_length', '-')} | 观测值 | 优先使用 API usage.input_tokens / prompt_tokens。 |")
        lines.append("")

    lines.append("负载与稳定性指标：")
    lines.append("")
    lines.append("| 指标 | 结果 | 状态 | 说明 |")
    lines.append("| --- | --- | --- | --- |")
    if concurrency is not None:
        levels = concurrency.metrics.get("levels", [])
        if levels:
            worst = min(levels, key=lambda item: item.get("success_rate", 1.0))
            lines.append(f"| 并发稳定性 | 并发 {worst.get('concurrency')} 成功率 {_fmt_pct(worst.get('success_rate'))} | {_status_label(concurrency.status.value)} | {_summary_cn(concurrency)} |")
            lines.append(f"| 并发批次请求吞吐 | {_fmt_float(worst.get('request_throughput_per_second'), 4)} req/s | 观测值 | 取最差并发档位的批次吞吐。 |")
            lines.append(f"| 并发 P50 时延 | {_fmt_float(worst.get('p50_latency_seconds'))} s | 观测值 | 取最差并发档位。 |")
            lines.append(f"| 并发 P95 时延 | {_fmt_float(worst.get('p95_latency_seconds'))} s | 观测值 | 取最差并发档位。 |")
    if availability is not None:
        lines.append(f"| 短窗口可用性采样 | {_fmt_pct(availability.metrics.get('availability_ratio'))} | {_status_label(availability.status.value)} | {_summary_cn(availability)} |")
    if ttft is not None:
        lines.append(f"| 单请求倒数吞吐（参考） | {_fmt_float(ttft.metrics.get('request_throughput_per_second'), 4)} req/s | 观测值 | 基于单请求总时延倒数估算，仅供参考。 |")
    lines.append("")
    return lines


def _key_findings(model: ModelReport) -> list[str]:
    findings: list[str] = []
    for probe in ["capability_score", "protocol_score", "security_score"]:
        result = _find_result(model, probe)
        if result is not None:
            findings.append(f"{_probe_label(result.probe)}：{_status_label(result.status.value)}，{_summary_cn(result)}")

    flagged = [
        result
        for result in model.results
        if result.status in {ProbeStatus.FAIL, ProbeStatus.WARN}
        and result.probe not in {"capability_score", "protocol_score", "security_score"}
    ]
    flagged.sort(key=lambda item: (0 if item.status == ProbeStatus.FAIL else 1, item.suite, item.probe))
    for result in flagged[:4]:
        findings.append(f"{_probe_label(result.probe)}：{_status_label(result.status.value)}，{_summary_cn(result)}")
    if not findings:
        findings.append("本轮已执行指标均为通过，未出现告警或失败。")
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
    if report.metadata:
        lines.append(f"- 应用版本：`{report.metadata.get('app_version', '-')}`")
        lines.append(f"- 报告格式版本：`{report.metadata.get('report_schema_version', '-')}`")
        lines.append(f"- 评分版本：`{report.metadata.get('score_version', '-')}`")
        lines.append(f"- Git Commit：`{report.metadata.get('git_commit', '-')}`")
        lines.append(f"- 工作区脏状态：`{report.metadata.get('git_dirty', '-')}`")
    lines.append("")

    for provider in report.providers:
        lines.append(f"## 服务商：{provider.name}")
        lines.append("")
        lines.append(f"- Base URL：`{provider.base_url}`")
        lines.append(f"- 总体状态：`{_status_label(provider.overall_status.value)}`")
        lines.append(f"- 汇总：`{json.dumps(provider.summary, ensure_ascii=False)}`")
        lines.append("")

        for model in provider.models:
            lines.append(f"### 模型：`{model.model}`")
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
                    lines.append(f"- 原始摘要：{result.summary}")
                lines.append("")
                if result.probe == "capability_fingerprint":
                    lines.extend(_capability_challenge_lines(result))
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
    logger.info(
        "Writing report output_dir=%s formats=%s write_transparency_log=%s",
        target_dir.resolve(),
        ",".join(formats),
        write_transparency_log,
    )

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
        logger.info("JSON report written path=%s", json_path.resolve())

    if "md" in formats:
        md_path = target_dir / f"{base_name}.md"
        md_path.write_text(render_markdown(report), encoding="utf-8")
        written["md"] = md_path
        logger.info("Markdown report written path=%s", md_path.resolve())

    if write_transparency_log and report.audit_log_entries:
        log_path = target_dir / f"{base_name}-transparency.ndjson"
        with log_path.open("w", encoding="utf-8") as handle:
            for entry in report.audit_log_entries:
                handle.write(json.dumps(entry, ensure_ascii=False, default=_json_default))
                handle.write("\n")
        written["ndjson"] = log_path
        logger.info("Transparency log written path=%s", log_path.resolve())

    return written
