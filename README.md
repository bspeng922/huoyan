# Huoyan

## Terminology Note

- `capability_score` / `protocol_score` / `security_score` are scorecards built from grouped probe results. They are operational reference scores, not proof of backend model identity.
- `availability` is a short-window sampling result, not a long-term SLA or uptime guarantee.
- `token_alignment` compares API `usage` token counts with local tokenizer estimates. It is not a billing multiplier audit.
- `dependency_substitution` currently checks whether fixed install commands survive the tool-call path unchanged. It does not fully rule out generic dependency poisoning.
- `privacy_policy` is a configuration record only. It is not an automated legal or compliance audit.
- Report timestamps and filenames use the local timezone of the machine that ran Huoyan.

Huoyan 是一个面向大模型中转商的测试与审计框架。

它的目标不是只做压测，也不是只做真假识别，而是把下面几类问题放到一份统一报告里：

- 这个中转商给你的模型，和它声称的模型是否大体一致
- 性能是否真实，尤其是流式首包、正文可见时间、吞吐和并发
- Tool Calling、长上下文、多模态这些高级能力是否能真正穿透
- Token 计费、TLS、错误边界、内部信息泄漏这些基础设施问题是否存在

当前版本同时提供 CLI 与 Web 控制台，已经支持多协议、多模型、多维度报告和透明日志。

## 支持范围

当前支持 3 类接口风格：

- `openai-chat`
  - 典型路径：`/v1/chat/completions`
- `openai-responses`
  - 典型路径：`/v1/responses`
- `anthropic-messages`
  - 典型路径：`/v1/messages`

适用场景：

- OpenAI 兼容中转
- Anthropic Messages 兼容中转
- 同一个中转下同时测试多个模型
- 浏览器中快速验证单个中转 / 单个模型

## 快速开始

安装：

```bash
pip install -e .
```

运行：

```bash
huoyan run examples/openai-compatible.yaml
```

或者：

```bash
python -m huoyan run examples/openai-compatible.yaml --output reports
```

只测单个 provider / 单个模型：

```bash
python -m huoyan run your-config.yaml --only-provider relay-a --only-model glm-5
```

只跑某几个 suite：

```bash
python -m huoyan run your-config.yaml --suite authenticity --suite performance
```

启动 Web 控制台：

```bash
python -m huoyan web --host 127.0.0.1 --port 8001
```

然后在浏览器打开 `http://127.0.0.1:8001`。

Web 控制台当前面向「单个中转 / 单个模型」的快速验证，提供以下能力：

- 输入中转站 `baseUrl`、模型与 `api_key` 后直接发起测试
- 根据 `baseUrl` 自动推断接口风格：
  - `/responses` -> `openai-responses`
  - `/messages` -> `anthropic-messages`
  - 其他路径默认按 `openai-chat` 处理
- 首页查看当前测试结果
- `测试记录` 页面查看历史记录、导出文件并勾选多条记录做结果对比

## 输出文件

默认输出到 `reports/`，文件名格式为：

- `huoyan-模型名-YYYYMMDD-HHMMSS.json`
- `huoyan-模型名-YYYYMMDD-HHMMSS.md`
- `huoyan-模型名-YYYYMMDD-HHMMSS-transparency.ndjson`

如果一次运行包含多个模型，文件名会退化为：

- `huoyan-multi-model-YYYYMMDD-HHMMSS.*`

其中：

- `json`
  - 适合程序消费
- `md`
  - 适合人工阅读
- `ndjson`
  - 透明日志，记录脱敏后的请求/响应摘要与响应 hash

时间说明：

- 报告中的 `generated_at`
- 每个 probe 的 `started_at / finished_at`
- 输出文件名中的 `YYYYMMDD-HHMMSS`

都使用运行 Huoyan 的本机本地时区。例如在上海机器上会写成 `+08:00`。

Web 控制台额外会在 `reports/web/` 下维护历史索引与每次运行的导出文件：

- `reports/web/history.json`
  - 历史记录索引，保存脱敏后的运行摘要与导出文件路径
- `reports/web/runs/<run_id>/huoyan-*.json`
- `reports/web/runs/<run_id>/huoyan-*.md`
- `reports/web/runs/<run_id>/huoyan-*-transparency.ndjson`

其中 `run_id` 是 Web 控制台为单次测试生成的记录 ID。

## 如何一次测试同一中转的多个模型

一个 provider 下直接写多个 `models` 即可：

```yaml
providers:
  - name: relay-a
    base_url: https://example.com/v1
    api_key: sk-xxx
    api_style: openai-chat
    defaults:
      enabled_suites:
        - authenticity
        - performance
        - agentic
        - cost_security
        - security_audit
    models:
      - model: glm-5
        claimed_family: glm
      - model: kimi-k2.5
        claimed_family: kimi
      - model: MiniMax-M2.5
        claimed_family: minimax
```

不加 `--only-model` 时，Huoyan 会顺序测试全部模型，并分别在报告中展开。

## 配置说明

最小结构：

```yaml
providers:
  - name: relay-demo
    base_url: https://your-relay.example.com/v1
    api_key: sk-your-key
    api_style: openai-chat
    defaults:
      enabled_suites:
        - authenticity
        - performance
        - agentic
        - cost_security
        - security_audit
    models:
      - model: glm-5
        claimed_family: glm
        supports_stream: true
        supports_tools: true
        supports_vision: false
report:
  output_dir: reports
  formats: [json, md]
  write_transparency_log: true
```

当前默认值以代码为准，关键默认参数如下：

- `performance_stream_samples: 10`
  - 性能流式探针默认重复采样 10 次
- `performance_stream_sample_interval_seconds: 1.0`
  - 默认流式采样间隔为 1 秒
- `concurrency_levels: [5, 10]`
  - 默认并发档位为 5 和 10
- `uptime_samples: 5`
  - 默认短周期可用性采样 5 次
- `security_warmup_requests: 10`
  - 条件投递探针默认预热 10 次
- `security_retry_attempts: 3`
  - 安全审计探针默认最多重试 3 次
- `security_retry_backoff_seconds: 3.0`
  - 安全审计探针默认指数退避起始秒数

配置建议：

- `claimed_family` 请使用规范值：
  - `openai`
  - `claude`
  - `gemini`
  - `glm`
  - `kimi`
  - `qwen`
  - `deepseek`
  - `minimax`
- 不建议把真实 `api_key` 提交到仓库；示例文件应使用占位值。

## 预估消耗

单模型完整测评（默认配置，全部 suite 开启）大约产生以下模型侧请求与辅助探针请求：

| 探针 | 请求数 | 流式 | 输入 token/次 | 输出 token/次 |
| --- | --- | --- | --- | --- |
| identity | 1 | 否 | ~70 | ~20 |
| capability_fingerprint | 6 | 否 | ~20-120 | ~5-20 |
| acrostic_constraints | 1 | 否 | ~80 | ~50 |
| boundary_reasoning | 1 | 否 | ~120 | ~30 |
| linguistic_fingerprint | 1 | 否 | ~110 | ~70 |
| response_consistency | 3 | 否 | ~80 | ~200 |
| ttft_tps | 10 | 是 | ~45 | ~750 |
| concurrency | 15 | 否 | ~15 | ~5 |
| availability | 5 | 否 | ~15 | ~5 |
| tool_calling | 1 | 否 | ~150 | ~30 |
| multi_turn_tool | 2 | 否 | ~40 / ~60 | ~10 / ~40 |
| long_context_integrity | 1-3 | 否 | ~8000-32000 | ~60 |
| token_alignment | 1 | 否 | ~60 | ~5 |
| rate_limit_transparency | 2 + active burst | 否 | ~10 | ~10 |
| dependency_substitution | 3 | 否 | ~90 | ~30 |
| conditional_delivery | 1+10 预热 | 否 | ~90 / ~15 | ~30 / ~5 |
| error_response_leakage | 3 | 否（坏请求） | ~10 | ~0 |
| stream_integrity | 1 | 是 | ~40 | ~200 |
| system_prompt_injection | 2 | 否 | ~60 | ~80 |

**默认配置下，单模型完整测评预估消耗：**

- 模型侧请求数：约 `80-84` 次
- 额外辅助请求：`TLS` 与 `security_headers` 各 1 次，不计入模型 token
- 输入 token：波动较大，主要受 `long_context_integrity` 的扫点数量影响
- 输出 token：主要受 `ttft_tps` 采样次数和 `response_consistency` 重复请求影响

**调整建议：**

- 如果想快速测试，可以只开 `authenticity` + `security_audit` 两个 suite，消耗约 3,000 token
- `long_context_integrity` 单项消耗最大，会按 `8k / 16k / 32k / target` 里的可达检查点做扫点；可通过 `long_context_target_chars` 调整文档长度或关闭 `agentic` suite 来跳过
- `performance_stream_samples` 控制流式采样次数，默认 10 次；减少到 5 次可明显降低输出 token 消耗
- `rate_limit_transparency` 会做 `2` 次被动采样加一次主动突发，请在高限流环境下按需降低 `concurrency_levels`

## 报告里的状态是什么意思

Huoyan 的单项状态有 5 种：

- `通过`
  - 当前指标看起来正常
- `警告`
  - 存在偏差，但还不足以直接判定为异常
- `失败`
  - 当前指标明确不符合预期
- `错误`
  - 探针本身没有成功跑完，例如 429、503、超时、协议不兼容
- `跳过`
  - 当前模型或当前配置不支持该项测试

注意：

- 报告中的很多性能子项是 `观测值`
- `观测值` 不表示好坏，只表示本次测到的数
- 真正的好坏判断主要看该探针或评分卡的主状态，例如 `capability_score`、`TTFT`、`并发稳定性`

## 指标说明

下面是当前报告中各指标的测试原理和数值含义。

### 1. 评分卡与模型侧写（Scorecards & Routing Signals）

当前版本不再输出单一的 `consistency_score`，而是拆成 3 张评分卡：

- `capability_score`
  - 汇总能力相关探针，例如 `capability_fingerprint`、`response_consistency`、`tool_calling`、`multi_turn_tool`、`long_context_integrity`
- `protocol_score`
  - 汇总协议与 usage 口径相关探针，例如 `stream_integrity`、`token_alignment`
- `security_score`
  - 汇总安全卫生相关探针，例如 `dependency_substitution`、`conditional_delivery`、`error_response_leakage`、`rate_limit_transparency`

每张评分卡都会输出：

- `score`
  - 0 到 100 的参考分
- `grade`
  - `high / moderate / low / insufficient_evidence / not_scored`
- `coverage_ratio`
  - 当前评分卡中可计分探针到底覆盖了多少

解读建议：

- 评分卡是工程参考，不是“后端模型身份证明”
- `ERROR` 和 `SKIP` 不按 0 分硬扣，而是从分母中排除
- 如果 `coverage_ratio` 很低，应优先看原始 probe，而不是看分数
- `identity` 仍保留，但它是独立弱信号，不参与当前评分卡

#### 专属身份测试（Identity Probe）

测试原理：

- 直接要求模型只输出：
  - `MODEL=...`
  - `COMPANY=...`
- 再和 `claimed_family` 的期望关键词做弱匹配

值的意义：

- `self_report_keyword_hits`
  - 命中了几个预期家族关键词
- `response_excerpt`
  - 模型自报身份摘要
- `provider_api_style`
  - 当前走的是哪种协议兼容层

解读建议：

- 这是弱信号
- 不应把 `openai` 兼容协议直接当成 `ChatGPT` 指纹
- 不应把 `anthropic` 兼容协议直接当成 `Claude` 指纹
- 中转商 system prompt、安全包装、协议兼容层都会影响它

#### 能力侧写探针（Capability Fingerprint）

测试原理：

- 用固定小题库逐题发请求
- 当前题库共 `6` 题，覆盖：
  - 逻辑推断
  - 算术跟踪
  - 代码引用理解
  - 多步数学
  - 模式补全
  - 目标导向常识判断
- 每题只看最终答案是否命中，不看推理过程

值的意义：

- `correct_count`
  - 答对题数
- `total_challenges`
  - 当前题库总题数
- `family_threshold`
  - 当前家族的通过阈值
- `family_threshold_ratio`
  - 当前家族阈值比例

阈值规则：

- `openai / claude / gemini`
  - 使用 `80%` 阈值
- `glm / kimi / qwen / deepseek / unknown`
  - 使用 `60%` 阈值
- 阈值按题量自动换算，因此后续题库扩容时门槛不会自动变松

报告展示：

- Markdown 报告会直接展开逐题结果
- 每题可看到：
  - 原始问题
  - 标准答案
  - 模型回复
  - 是否命中

解读建议：

- 这是低成本能力 smoke test，不是严格模型指纹
- 题量仍然较少，更适合看“基础能力画像”，不适合单独做强归因

#### 智力探针：约束跟随（Constraint-Following Probe）

测试原理：

- 要求模型生成四行七字藏头诗
- 逐行检查：
  - 首字是否正确
  - 每行是否正好 7 个汉字

值的意义：

- `valid_lines`
  - 满足条件的行数
- `line_lengths`
  - 每行实际长度

解读建议：

- 更偏向格式控制与细粒度约束能力
- 对“高端模型被低端模型冒充”的场景有一定筛分价值

#### 智力探针：边界推理（Boundary Reasoning Probe）

测试原理：

- 使用 Python 可变默认参数边界题
- 让模型只输出最终打印结果

值的意义：

- `matched_lines`
  - 命中的行数
- `expected_lines`
  - 预期总行数

解读建议：

- 主要看边界条件理解能力
- 如果结果被代码块、额外解释污染，当前实现可能判成低分或失败

#### 多语种理解测试（Linguistic Fingerprint Probe）

测试原理：

- 混合投喂：
  - 文言文
  - Rust 函数签名
  - SQL / JSON Path
- 检查返回里是否能正确识别这些类型

值的意义：

- `signal_hits`
  - 命中了几个语言/表达体系信号

解读建议：

- 这是能力侧中信号
- 对中外模型、通用模型与偏中文模型有一定区分度

#### 输出一致性测试（Response Consistency Probe）

测试原理：

- 用固定 prompt 在短时间窗口内重复请求 3 次
- 当前使用 `temperature=0.2`
- 同时记录文本相似度和每次响应的语义锚点覆盖率

值的意义：

- `average_similarity`
  - 重复输出间的平均文本相似度
- `min_similarity`
  - 最低一对的相似度
- `average_anchor_coverage`
  - 每次响应平均覆盖了多少语义锚点
- `complete_response_count`
  - 完整覆盖全部语义锚点的响应次数
- `similarity_floor_pass / similarity_floor_warn`
  - 当前 PASS / WARN 使用的相似度下限
- `min_similarity_floor_pass / min_similarity_floor_warn`
  - 当前最低 pair 相似度下限

解读建议：

- 当前实现同时看：
  - 语义锚点覆盖
  - 平均文本相似度
  - 最低 pair 相似度
- 这仍是短周期 spot check，不是跨时段路由证明
- 3 次采样的统计意义有限，主要用于抓极端异常

### 2. 性能与高可用（Performance & Reliability）

性能部分在报告里分成 3 组：

- 响应启动指标
- 正文生成指标
- 负载与稳定性指标

#### 首次回复延迟（TTFT）

测试原理：

- 对流式请求做重复采样
- 当前 TTFT 定义为“首次回复事件”的时间
- 对 `openai-chat`
  - `reasoning_content` 或 `content` 任一先到都算
- 报告状态判定优先使用：
  - `p90`
  - 若样本不足 10，则退回 `p75`
  - 若样本仍不足 4，则退回 `avg`

值的意义：

- `ttft_seconds`
  - 多次采样后的均值
- `ttft_stats_seconds`
  - 包含 `avg / min / max / p99 / p90 / p75`
- `ttft_observed_basis`
  - 本轮状态判定实际使用的统计口径
- `ttft_observed_seconds`
  - 本轮状态判定实际使用的数值

解读建议：

- 这是“系统开始回复有多快”
- 不是“正文开始可见有多快”
- 报告里的状态摘要会明确写出本轮实际使用的是 `p90 / p75 / avg`

#### 首正文延迟

测试原理：

- 单独记录首个正文 `content` token 的时间

值的意义：

- `first_content_seconds`
  - 首正文时间均值
- `first_content_stats_seconds`
  - 首正文时间分布

解读建议：

- 如果 `TTFT` 很低但 `首正文延迟` 很高
- 通常意味着模型/中转先输出了大量 `reasoning_content` 或隐藏推理

#### 相邻内容事件延迟 / 估算 ITL

测试原理：

- 优先统计实际流式正文事件之间的到达时间差
- 同时保留一个基于生成阶段总耗时推算的 ITL 近似值

值的意义：

- `inter_event_latency_ms`
  - 相邻正文事件的平均延迟
- `inter_token_latency_ms`
  - 估算的平均相邻 token 延迟
- `inter_token_latency_stats_ms`
  - 估算 ITL 分布

解读建议：

- `inter_event_latency_ms` 比纯估算 ITL 更接近真实流式体验
- `inter_token_latency_ms` 仍然只是参考值，不是严格逐 token 统计

#### 请求总时延

测试原理：

- 从请求发起到整个流式请求结束的总耗时

值的意义：

- `request_latency_ms`
  - 平均总时延
- `request_latency_stats_ms`
  - 总时延分布

解读建议：

- 和 `TTFT` 不同，它包含完整生成耗时

#### 输入序列长度 / 输出序列长度

测试原理：

- 优先使用 API 返回的 `usage.input_tokens / output_tokens`
- 没有则退回本地估算

值的意义：

- `input_sequence_length`
  - 这次性能探针输入 token 规模
- `output_sequence_length`
  - 这次性能探针输出 token 规模

解读建议：

- 它们是观测值，不是好坏判断
- 主要用来解释吞吐和延迟为什么变大

#### 输出 Token 吞吐

测试原理：

- 用输出 token 数除以正文生成阶段时间

值的意义：

- `output_token_throughput_per_second`
  - 平均输出 token 吞吐
- `output_token_throughput_stats_per_second`
  - 吞吐分布

解读建议：

- 越高通常表示正文生成越快
- 但如果中转把 `reasoning tokens` 也算进 `completion_tokens`，可能被放大

#### 请求吞吐

测试原理：

- 单请求近似吞吐：`1 / 单请求总时延`
- 并发批次吞吐：`成功请求数 / 批次总耗时`

值的意义：

- `request_throughput_per_second`
  - 单请求近似吞吐
- 并发 probe 里每个并发档位也会记录自己的 `request_throughput_per_second`

解读建议：

- 真正更有意义的是并发批次吞吐
- 单请求吞吐主要用于参考，不建议单独解读

#### 高并发稳定性（Concurrency）

测试原理：

- 对同一模型在若干并发档位下同时发请求
- 当前默认档位：`[5, 10]`

值的意义：

- `success_rate`
  - 成功率
- `p50_latency_seconds / p95_latency_seconds`
  - 并发下的中位数 / 尾延迟
- `status_codes`
  - 响应码分布
- `request_throughput_per_second`
  - 该并发档位的批次吞吐

解读建议：

- 这是判断“能不能扛瞬时并发”的核心指标之一

#### 短周期可用性快照（Availability Snapshot）

测试原理：

- 间隔采样多次轻量请求
- 当前默认：`5` 次，间隔 `1s`

值的意义：

- `availability_ratio`
  - 采样窗口内成功率
- `failure_count`
  - 采样窗口内失败次数
- `samples`
  - 每次采样的原始结果

解读建议：

- 这是短周期可用性快照，不是长期 SLA
- 当前默认判定：
  - `0` 次失败：`PASS`
  - `1` 次失败：`WARN`
  - `2` 次及以上失败：`FAIL`

### 3. Agent 与长上下文支持（Context & Agentic Capabilities）

#### 工具调用穿透率（Tool Calling Integrity）

测试原理：

- 给模型一个严格 JSON Schema 的函数工具
- 检查中转后返回的工具调用参数是否仍然合法

值的意义：

- `tool_call_count`
  - 返回了几次工具调用
- `arguments`
  - 解析后的参数

解读建议：

- 这是强信号
- 参数结构被改写、截断、转义错乱，都会在这里暴露

#### 多轮工具链路完整性（Multi-Turn Tool Integrity）

测试原理：

- 第一轮强制模型发起 `get_weather` 工具调用
- 第二轮把工具结果回填给模型
- 要求模型只输出固定 JSON，包含：
  - `city`
  - `temperature`
  - `condition`
  - `clothing_advice`
- 检查这些字段是否仍然被正确保留

值的意义：

- `matched_fields`
  - 命中的结构化字段数
- `required_fields`
  - 期望字段总数
- `city_ok / temperature_ok / condition_ok / clothing_advice_ok`
  - 各字段是否命中

解读建议：

- 这比简单关键词匹配更严格
- 更适合发现中转在多轮工具链路上做字段改写、上下文丢失或格式污染

#### 超长上下文不丢失（Long Context Integrity）

测试原理：

- 对长文档做阶梯式扫点
- 当前检查点会从以下集合里选取不超过目标长度的值：
  - `8000`
  - `16000`
  - `32000`
  - `long_context_target_chars`
- 每个检查点都会在头 / 中 / 尾埋入 canary，并只问 canary 值
- 一旦在某个检查点失败或报错，扫点会停止

值的意义：

- `tested_target_chars`
  - 本轮实际跑过的检查点
- `max_preserved_target_chars`
  - 当前最长通过的上下文长度
- `first_failed_target_chars`
  - 首个失败或报错的长度
- `fully_preserved_targets`
  - 完全通过的检查点个数

解读建议：

- 这是检测“吞上下文”的高价值探针
- 当前结果更适合回答“最长稳定到多长”，而不是“单点 20k 是否通过”

#### 多模态支持（Multimodal Support）

测试原理：

- 传图像 URL 或 data URI
- 检查是否能稳定返回

值的意义：

- 如果配置了 `multimodal_expected_answer`
  - 还会做一个简单一致性比对

解读建议：

- 不配置图片时通常会 `skip`
- 当前 Anthropic 路径也会保留图片块，不再只测文字部分

### 4. 成本核算与网络安全（Cost & Security）

#### 计费透明度 / Token 对齐（Token Counting Alignment）

测试原理：

- 发送固定 prompt
- 要求模型原样回显固定输出字符串
- 同时比较 API usage 的输入 / 输出 token 与本地估算值
- 对 OpenAI 家族使用精确 tokenizer，对其他模型使用 `cl100k_base` 近似估算

值的意义：

- `api_prompt_tokens`
  - API 返回的输入 token
- `local_prompt_tokens`
  - 本地估算值
- `prompt_ratio`
  - 输入 token 的 API 计数与本地估算比值
- `api_output_tokens / local_output_tokens`
  - 输出 token 的 API 计数与本地估算值
- `output_ratio`
  - 输出 token 的 API 计数与本地估算比值
- `output_exact_match`
  - 模型是否精确回显了预期输出字符串
- `approximate`
  - 当前估算是否只是近似

解读建议：

- 这里的 ratio 是 **token 计数比值**，不是 **计费倍率**。中转站的"X 折"是价格折扣，和 token 计数无关
- OpenAI 家族：使用精确 tokenizer（如 `o200k_base`），会同时看输入和输出 token 比值
- GLM / Kimi / Qwen / DeepSeek：使用 `cl100k_base` 近似估算，ratio 在 0.5-1.5 范围内标记为 SKIP（不做判定），超出则 WARN
- 当 `output_exact_match: false` 时，输出侧比值只能作为弱参考
- 当 `approximate: true` 且状态为 SKIP 时，说明本地估算不可靠，ratio 仅供参考
- 要验证中转站计费是否合理，需要将中转站报告的 token 单价与官方 API 对比

#### TLS 加密基线（TLS Baseline）

测试原理：

- 对目标域名做 TLS 握手检查

值的意义：

- `tls_version`
  - TLS 版本
- `cipher`
  - 协商出的 cipher
- `expires_in_days`
  - 证书剩余有效期

解读建议：

- 它测的是入口加密基线，不代表对方一定安全

#### 隐私策略记录（Privacy Policy）

测试原理：

- 只检查你是否配置了 `privacy_policy_url`

值的意义：

- 它不是自动化验证隐私条款
- 只是把“是否有可记录地址”写进报告
- 它是信息记录项，不建议当作技术评分项解读

#### 限流元数据透明度（Rate-Limit Metadata Transparency）

测试原理：

- 先做 `2` 次被动轻量采样
- 再做一次主动突发请求批次
- 检查是否观察到：
  - `429`
  - `Retry-After`
  - `x-ratelimit-*`

值的意义：

- `sampled_requests`
  - 本轮总采样请求数
- `passive_sample_count`
  - 被动采样次数
- `active_burst_size`
  - 主动突发批次大小
- `saw_429`
  - 是否观察到 `429`
- `saw_rate_limit_headers`
  - 是否观察到限流元数据

解读建议：

- 这项主要衡量“限流是否透明”，不是“限流是否存在”
- 如果出现 `429` 却没有 `Retry-After` 或 `x-ratelimit-*`，通常应视为较差实现

### 5. 中转安全审计（Relay Security Audit）

#### 依赖替换检测（Dependency Substitution）

测试原理：

- 强制模型通过工具调用返回固定安装命令
- 例如：
  - `pip install requests`
  - `npm install axios`
  - `cargo add reqwest`
- 检查命令是否被中转改写

值的意义：

- `exact_matches`
  - 逐字命中的 case 数
- `inconclusive_cases`
  - 无法得出结论的 case 数

解读建议：

- 这是针对依赖投毒 / 包名替换的安全探针

#### 条件投递检测（Conditional Delivery）

测试原理：

- 先做一次基线工具调用
- 再做若干 warmup 请求
- 再做同样的工具调用
- 看结果是否前后变化

值的意义：

- `warmup_requests`
  - 预热请求次数

解读建议：

- 用于抓“平时正常、特定条件才投毒”的中转
- 这仍是短窗口 spot check，预热通过不代表已经排除了慢触发条件

#### 错误响应泄漏（Error Response Leakage）

测试原理：

- 故意发坏请求
- 检查错误响应里是否暴露：
  - secret-like 字段
  - 内部实现栈
  - 上游 URL
  - 中转内部业务字段

当前已覆盖的中文中转内部泄漏特征包括：

- `分组`
- `无可用渠道`
- `distributor`
- 折扣/业务分组字样，例如 `六折`

值的意义：

- `secret_hits`
  - 命中的 secret 类泄漏数
- `implementation_leak_hits`
  - 命中的内部实现/业务信息泄漏数
- `accepted_invalid_cases`
  - 坏请求却被当正常请求处理的次数

解读建议：

- 这是判断中转“错误边界是否干净”的高价值项

#### 流完整性（Stream Integrity）

测试原理：

- 检查流式事件序列是否完整、是否混乱、是否路由错模型

值的意义：

- `event_count / chunk_count`
  - 流事件数量
- `unique_events`
  - 事件类型集合
- `model_mismatches`
  - 流中模型身份是否变化

解读建议：

- 这是强信号
- 对 `responses` 和 `anthropic-messages` 风格中转尤其有价值

#### 系统提示注入检测（System Prompt Injection Detection）

测试原理：

- 发送不包含 system 消息的请求，让模型披露它在用户消息之前收到的所有系统级指令
- 同时询问模型在用户消息之前收到了几条非用户指令
- 将披露内容与已知的中转商注入模式（如"You are a helpful assistant"、"你是一个有用的助手"等）进行匹配

值的意义：

- `disclosure_pattern_hits`
  - 匹配到的中转注入模式数量
- `reported_instruction_count`
  - 模型自报在用户消息前收到的指令条数
- `denied_receiving_instructions`
  - 模型是否否认收到了额外指令

解读建议：

- 只有模型直接披露了前置指令线索时，这个探针才有较强参考价值
- 模型拒绝披露或声称 `NONE_RECEIVED` 时，结果通常只能视为 `SKIP`，不能据此证明不存在注入
- WARN 不一定意味着恶意，很多中转只是添加默认人设，但这仍会影响 identity 等探针
- 如果 disclosure_pattern_hits 较高且模型承认收到了额外指令，建议结合 identity 探针综合判断

#### 透明日志（Transparency Log）

测试原理：

- 把脱敏后的请求/响应摘要写入 `ndjson`
- 同时记录响应 hash

值的意义：

- 用于事后排查
- 不是实时防御

## 当前限制

- 目前只支持：
  - OpenAI 兼容 `chat/completions`
  - OpenAI 兼容 `responses`
  - Anthropic 兼容 `messages`
- `claimed_family` 需要使用规范值，例如 `openai / claude / gemini / glm / kimi / qwen / deepseek / minimax`
- `Token 对齐` 对 OpenAI 家族最可信，非 OpenAI 模型因 tokenizer 不匹配会标记为 SKIP
- `identity` 只是弱信号，不能单独证明后端掉包
- 中转站仪表盘上会显示大量非流式请求，这是正常的——只有 `ttft_tps` 和 `stream_integrity` 两个探针使用流式输出，其余均为非流式
- 某些中转会返回 HTTP `200` 但 body 里嵌上游错误，Huoyan 会尽量识别，但不同中转实现差异仍然很大
- “真实后端路由””是否留存日志””是否有 DDoS 防护”无法仅靠自动化探针绝对证明

## 建议的阅读顺序

看一份报告时，建议按这个顺序看：

1. `capability_score / protocol_score / security_score`
2. `TTFT / 首正文延迟 / 并发 / 可用性`
3. `tool_calling / multi_turn_tool / long_context_integrity / stream_integrity`
4. `token_alignment / rate_limit_transparency / error_response_leakage / system_prompt_injection / TLS`
5. `identity`
6. 最后再看单个 probe 的原始明细与透明日志

## 参考配置

基础示例见：

- [examples/openai-compatible.yaml](/D:/code/2026/huoyan/examples/openai-compatible.yaml)
