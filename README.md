# Huoyan

Huoyan 是一个面向大模型中转商的测试与审计框架。

它的目标不是只做压测，也不是只做真假识别，而是把下面几类问题放到一份统一报告里：

- 这个中转商给你的模型，和它声称的模型是否大体一致
- 性能是否真实，尤其是流式首包、正文可见时间、吞吐和并发
- Tool Calling、长上下文、多模态这些高级能力是否能真正穿透
- Token 计费、TLS、错误边界、内部信息泄漏这些基础设施问题是否存在

当前版本是可运行的 CLI MVP，已经支持多协议、多模型、多维度报告和透明日志。

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

- `performance_stream_samples: 5`
  - 性能流式探针默认重复采样 5 次
- `concurrency_levels: [5]`
  - 默认并发档位为 5
- `uptime_samples: 5`
  - 默认可用性采样 5 次
- `security_retry_attempts: 3`
  - 安全审计探针默认最多重试 3 次
- `security_retry_backoff_seconds: 3.0`
  - 安全审计探针默认指数退避起始秒数

## 预估消耗

单模型完整测评（默认配置，全部 suite 开启）大约产生以下 API 请求：

| 探针 | 请求数 | 流式 | 输入 token/次 | 输出 token/次 |
| --- | --- | --- | --- | --- |
| identity | 1 | 否 | ~70 | ~20 |
| acrostic_constraints | 1 | 否 | ~80 | ~50 |
| boundary_reasoning | 1 | 否 | ~120 | ~30 |
| linguistic_fingerprint | 1 | 否 | ~110 | ~70 |
| response_consistency | 3 | 否 | ~80 | ~200 |
| ttft_tps | 5 | 是 | ~45 | ~750 |
| concurrency | 5 | 否 | ~15 | ~5 |
| availability | 5 | 否 | ~15 | ~5 |
| tool_calling | 1 | 否 | ~150 | ~30 |
| long_context_integrity | 1 | 否 | ~15000 | ~60 |
| token_alignment | 1 | 否 | ~60 | ~5 |
| rate_limit_transparency | 3 | 否 | ~10 | ~10 |
| dependency_substitution | 3 | 否 | ~90 | ~30 |
| conditional_delivery | 1+3 预热 | 否 | ~90 / ~15 | ~30 / ~5 |
| error_response_leakage | 3 | 否（坏请求） | ~10 | ~0 |
| stream_integrity | 1 | 是 | ~40 | ~200 |
| system_prompt_injection | 2 | 否 | ~60 | ~80 |

**默认配置下，单模型完整测评预估消耗：**

- 总请求数：约 38-42 次
- 输入 token：约 18,000-20,000（其中 long_context_integrity 占 ~15,000）
- 输出 token：约 4,500-5,500
- 总 token：约 23,000-25,000

**调整建议：**

- 如果想快速测试，可以只开 `authenticity` + `security_audit` 两个 suite，消耗约 3,000 token
- `long_context_integrity` 单项消耗最大（~15,000 输入 token），可通过 `long_context_target_chars` 调整文档长度或关闭 `agentic` suite 来跳过
- `performance_stream_samples` 控制流式采样次数，默认 5 次；减少到 3 次可省约 1,500 token
- `concurrency_levels` 和 `uptime_samples` 各产生少量 token，影响不大

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
- 真正的好坏判断主要看该探针的主状态，例如 `TTFT`、`并发稳定性`、`可用性`

## 指标说明

下面是当前报告中各指标的测试原理和数值含义。

### 1. 模型保真度与掺水（Authenticity & Purity）

#### 综合保真度评分（Authenticity Consistency Score）

测试原理：

- 不是单独跑一个请求
- 而是把多类信号聚合成一个分数
- 当前信号来源包括：
  - `identity`
  - `acrostic_constraints`
  - `boundary_reasoning`
  - `linguistic_fingerprint`
  - `response_consistency`
  - `token_alignment`
  - `tool_calling`
  - `long_context_integrity`
  - `stream_integrity`
  - `error_response_leakage`
  - `system_prompt_injection`

权重设计：

- 弱信号：`identity`
- 中信号：能力题、语言题、输出一致性、Token 对齐
- 强信号：工具调用、长上下文、流完整性、错误边界、系统提示注入

值的意义：

- `consistency_score`
  - 0 到 100 的综合一致性评分
- `grade`
  - `高一致 / 中等一致 / 低一致 / 证据不足 / 未评分`
- `coverage_ratio`
  - 本次评分到底覆盖了多少可用信号

解读建议：

- 这是“后端一致性概率”的工程化近似，不是数学证明
- `ERROR` 和 `SKIP` 不再按 0 分硬扣，而是从分母里剔除
- 如果 `coverage_ratio` 很低，分数只能参考，不能下强结论

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

- 用固定 prompt 在 temperature=0 下重复请求 3 次
- 用 SequenceMatcher 对归一化后的文本两两比较相似度
- 同时检查语义锚点是否被覆盖

值的意义：

- `average_similarity`
  - 重复输出间的平均文本相似度
- `min_similarity`
  - 最低一对的相似度
- `anchor_group_hits`
  - 命中了几个语义锚点组

解读建议：

- 这是中信号
- temperature=0 下大多数现代模型都会高度一致
- 如果出现大幅波动，可能暗示后端在不同请求间切换了模型
- 3 次采样的统计意义有限，主要用于抓极端异常

### 2. 性能与高可用（Performance & Reliability）

性能部分在报告里分成 3 组：

- 响应启动指标
- 正文生成指标
- 负载与稳定性指标

#### 首次回复延迟（TTFT）

测试原理：

- 对流式请求做重复采样
- 当前 TTFT 定义为：
  - 首次回复事件的时间
  - 对 `openai-chat`，`reasoning_content` 或 `content` 任一先到都算

值的意义：

- `ttft_seconds`
  - 多次采样后的均值
- `ttft_stats_seconds`
  - 包含 `avg / min / max / p99 / p90 / p75`

解读建议：

- 这是“系统开始回复有多快”
- 不是“正文开始可见有多快”

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

#### 相邻 Token 延迟（ITL）

测试原理：

- 用生成阶段耗时除以相邻 token 数量近似得到

值的意义：

- `inter_token_latency_ms`
  - 平均相邻 token 延迟
- `inter_token_latency_stats_ms`
  - ITL 分布

解读建议：

- 越低通常表示生成更顺滑
- 它是观测值，不是直接 pass/fail 指标

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
- 当前默认档位：`[5]`

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

#### 可用性（Availability）

测试原理：

- 间隔采样多次轻量请求
- 当前默认：`5` 次，间隔 `1s`

值的意义：

- `availability_ratio`
  - 采样窗口内成功率
- `samples`
  - 每次采样的原始结果

解读建议：

- 这是短周期可用性，不是长期 SLA

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

#### 超长上下文不丢失（Long Context Integrity）

测试原理：

- 构造长文档
- 在头 / 中 / 尾埋入 canary
- 最后只问 canary 值

值的意义：

- `canary_hits`
  - 命中了几个 canary
- `target_chars`
  - 本轮构造的目标上下文长度

解读建议：

- 这是检测“吞上下文”的高价值探针

#### 多模态支持（Multimodal Support）

测试原理：

- 传图像 URL 或 data URI
- 检查是否能稳定返回

值的意义：

- 如果配置了 `multimodal_expected_answer`
  - 还会做一个简单一致性比对

解读建议：

- 不配置图片时通常会 `skip`

### 4. 成本核算与网络安全（Cost & Security）

#### 计费透明度 / Token 对齐（Token Counting Alignment）

测试原理：

- 发送固定 prompt
- 比较 API usage 与本地 tiktoken 估算值
- 对 OpenAI 家族使用精确 tokenizer，对其他模型使用 `cl100k_base` 近似估算

值的意义：

- `api_prompt_tokens`
  - API 返回的输入 token
- `local_prompt_tokens`
  - 本地估算值
- `delta_tokens`
  - 二者差值
- `ratio`
  - API 计数与本地估算的比值（不是计费倍率）
- `approximate`
  - 当前估算是否只是近似

解读建议：

- 这里的 ratio 是 **token 计数比值**，不是 **计费倍率**。中转站的"X 折"是价格折扣，和 token 计数无关
- OpenAI 家族：使用精确 tokenizer（如 `o200k_base`），ratio 在 0.9-1.1 为 PASS
- GLM / Kimi / Qwen / DeepSeek：使用 `cl100k_base` 近似估算，ratio 在 0.5-1.5 范围内标记为 SKIP（不做判定），超出则 WARN
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

- 这是强信号
- 中转商注入系统提示词会直接影响模型行为，可能导致 identity 探针等结果失真
- WARN 不一定意味着恶意——很多中转商只是添加默认人设，但用户有权知道
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
- `Token 对齐` 对 OpenAI 家族最可信，非 OpenAI 模型因 tokenizer 不匹配会标记为 SKIP
- `identity` 只是弱信号，不能单独证明后端掉包
- 中转站仪表盘上会显示大量非流式请求，这是正常的——只有 `ttft_tps` 和 `stream_integrity` 两个探针使用流式输出，其余均为非流式
- 某些中转会返回 HTTP `200` 但 body 里嵌上游错误，Huoyan 会尽量识别，但不同中转实现差异仍然很大
- “真实后端路由””是否留存日志””是否有 DDoS 防护”无法仅靠自动化探针绝对证明

## 建议的阅读顺序

看一份报告时，建议按这个顺序看：

1. `综合保真度评分`
2. `TTFT / 首正文延迟 / 并发 / 可用性`
3. `tool_calling / long_context_integrity / stream_integrity`
4. `token_alignment / error_response_leakage / system_prompt_injection / TLS`
5. 最后再看单个原始明细

## 参考配置

基础示例见：

- [examples/openai-compatible.yaml](/D:/code/2026/huoyan/examples/openai-compatible.yaml)
