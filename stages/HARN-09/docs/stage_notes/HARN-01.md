# HARN-01：Model Adapter

## 本阶段解决的问题

HARN-00 只有配置、日志和目录边界，调用方还没有统一方式访问模型。如果业务代码直接创建 OpenAI、Claude、DeepSeek 或其他 SDK 客户端，请求格式、异常类型和响应字段就会扩散到整个 Harness，替换模型服务时必须修改上层逻辑。

HARN-01 引入一个只描述“生成模型响应”的抽象边界。Harness 面向自己的 `ModelProvider` 和领域类型，而 OpenAI-compatible 的 HTTP 细节只存在于一个适配器中。

本阶段仍然没有 Agent：只完成一次请求和一次响应，不循环、不调用工具、不维护状态。

## 新增机制

- `ModelProvider`：统一的异步 `generate()` 接口。
- `ModelRequest` / `ModelResponse`：与厂商 SDK 无关的请求和响应。
- `ModelMessage` / `MessageRole`：当前阶段的纯文本消息类型。
- `TokenUsage`：统一 token 统计。
- `OpenAICompatibleProvider`：调用 `/chat/completions` 并把 JSON 转换为领域对象。
- `ModelTransportError`、`ModelHTTPError`、`ModelProtocolError`：区分网络、HTTP 和协议错误。

实现使用 `httpx` 作为异步 HTTP 客户端，但 `httpx` 不是模型 SDK。应用可以注入自己的 `AsyncClient` 以复用连接或在测试中替换传输层。

## 完整调用链

```text
User / Caller
      ↓
 ModelRequest
      ↓
ModelProvider.generate()
      ↓
OpenAICompatibleProvider
      ↓
POST /chat/completions
      ↓
OpenAI-compatible LLM
      ↓
JSON response
      ↓
schema validation + normalization
      ↓
 ModelResponse
```

`generate()` 只执行一次。把输出再次送回模型的循环属于 HARN-02。

## 必须观察的字段

调用前记录：provider、model、消息数量和 endpoint。调用后记录并返回：

- response ID 和规范化后的模型响应；
- `prompt_tokens`、`completion_tokens`、`total_tokens`；
- 从发起 HTTP 请求到收到响应的 `latency_ms`；
- `finish_reason`。

日志刻意不记录 API key 和消息正文，避免把凭据或用户内容泄漏到普通应用日志。完整 request/response 可通过类型化对象和测试观察；更系统的结构化事件留给 HARN-17。

## 最值得阅读的代码

1. `ModelProvider.generate()`：Harness 与具体模型实现之间的最小契约。
2. `ModelRequest`：所有 provider 共同接受的输入边界。
3. `ModelResponse`：把厂商响应归一化为 Harness 自己的数据。
4. `OpenAICompatibleProvider.generate()`：一次完整调用的编排、计时和错误映射。
5. `_parse_response()`：外部 JSON 进入内部可信类型的校验边界。

## 架构收益

`Harness ≠ LLM SDK`。SDK 或 HTTP API 只是外部模型协议的一种实现，Harness 则拥有自己的调用契约。上层代码只依赖 `ModelProvider`，因此以后可以增加其他 provider，而不改变调用者理解请求、usage、结束原因和错误的方式。

适配器也集中处理了外部数据不可信的问题：成功状态不代表 JSON 结构正确。只有通过校验的数据才能成为 `ModelResponse`。

## Trade-off

- 领域对象和外部 JSON 之间需要显式转换代码。
- 目前每次未注入客户端的调用都会创建短生命周期 HTTP 客户端，易于理解但无法充分复用连接。
- 为保持 OpenAI-compatible 服务兼容性，`finish_reason` 保留为字符串，而不是封闭枚举。
- 当前只支持非流式纯文本响应；内容分片、多模态和 tool calls 尚未进入类型系统。

## 尚未解决的问题

- 没有把模型输出反馈给下一步，也没有 `max_steps`；留给 HARN-02。
- 不解析或执行 tool calls；留给 HARN-03 和 HARN-04。
- 不保存 trajectory 或状态；留给 HARN-05。
- 没有重试、退避或熔断；留给 HARN-16。
- 只有文本日志，没有统一 trace/metric；留给 HARN-17。

## 最小 Demo

```bash
python -m examples.harn_01_model_adapter
```

Demo 使用 `httpx.MockTransport` 模拟一个 OpenAI-compatible LLM，因此不需要 API key 或网络，但实际经过同一个 provider 的请求构造、响应解析、usage、finish reason 和 latency 观测路径。

