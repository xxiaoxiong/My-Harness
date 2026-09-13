# HARN-10：Hook / Middleware

## 本阶段解决的问题

随着 Runtime 增加 Checkpoint、Interrupt 和 Resume，日志、指标、审计、调试等横切逻辑也会越来越多。如果把它们直接写进 Agent Loop，控制流很快会被与推理无关的代码淹没，而且每增加一种观察方式都要修改 Runtime。

HARN-10 引入 Hook 边界：Agent Loop 只负责在稳定生命周期点发出结构化事件；可替换的 Hook 决定如何处理事件。

```text
Agent control flow ── emits ──→ HookManager ──→ LoggingHook
                                      └───────→ MetricsHook
```

## 七个生命周期事件

```text
before_step
  before_model_call
  after_model_call
  before_tool_call
  after_tool_call
after_step

on_error  ← 任一 Runtime 或 Hook 操作抛出异常
```

一个正常的 Tool step 顺序是：

```text
before_step
→ before_model_call
→ ModelProvider.generate
→ after_model_call
→ parse Tool Call
→ before_tool_call
→ ToolExecutor.execute
→ after_tool_call
→ update State + Checkpoint
→ after_step
```

最终回答的 step 没有 Tool 事件。`ToolError` 会由 Executor 转成正常 `ToolResult.error`，因此仍触发 `after_tool_call`，不会触发 `on_error`；只有逃出正常数据边界的异常才触发 `on_error`。

## HookContext

所有 Hook 接收同一种只读 `HookContext`，其中包含：

- `task_id`、`goal`、`step`、当时的 `status`；
- 当前 `phase`；
- 可选的 `ModelRequest` / `ModelResponse`；
- 可选的 `ToolCall` / `ToolResult`；
- `on_error` 的异常对象。

Context 不暴露可变 `AgentState`，避免观察型 Hook 意外改写系统事实。每个回调只拿到该生命周期点需要的数据。

## 注册与调度

```python
hooks = HookManager()
hooks.register(LoggingHook())
hooks.register(MetricsHook())

loop = ToolAgentLoop(..., hook_manager=hooks)
```

Hook 回调是异步方法，按注册顺序串行执行。这样顺序完全可预测，也允许未来实现异步 trace exporter。调度时使用 Hook 列表快照，因此回调期间的新注册不会改变当前事件的接收者。

Hook 默认方法都是 no-op；自定义 Hook 只覆盖关心的事件，不必实现七个方法。

## 错误语义

Hook 失败会包装为 `HookExecutionError`，其中保存 callback 名称、Hook 实例和原始异常。Runtime 随后向所有 Hook 发出 `on_error`，再把原异常重新抛给调用者。即使某个 `on_error` 回调失败，后续错误观察者仍会运行；通知失败只作为附注附加到最初异常。

当前策略是 fail-fast：Hook 不是后台“尽力而为”任务。它适合需要可靠审计或策略拦截的扩展点，也意味着错误的 Logging Hook 可以中止 Agent。若 `on_error` 自己失败，Runtime 给原异常添加说明，但不覆盖最初失败原因。

## Checkpoint 与事件顺序

完整 step 会先更新 State 并保存 Checkpoint，再发出 `after_step`。因此 `after_step` 看到的是最终状态；即使该 Hook 失败，已经完成的恢复点仍然存在。

等待批准的 Tool 在 Interrupt 时完成当前 step，因此会触发 `after_step(status=waiting_approval)`；真正的 `before_tool_call` / `after_tool_call` 延迟到新进程收到批准并实际调用 Tool 时。拒绝不执行 Tool，所以不产生 Tool 生命周期事件，而是产生普通 rejected Tool Result。

## LoggingHook

`LoggingHook` 使用项目现有日志边界输出 task、step、model、response、tool、成功状态和错误 phase。它不更改 State、Context 或返回结果。

## MetricsHook

`MetricsHook` 记录：

- step、Model Call、Tool Call 的 started/completed 计数；
- 未被正常数据边界吸收的错误数；
- Model 和 Tool 调用经过时间。

`metrics.snapshot` 返回不可变的 `HookMetrics`。指标只存在于当前进程，不属于 Agent State 或 Checkpoint；跨进程聚合需要后续接入外部 metric backend。

## 最小 Demo

```bash
python -m examples.harn_10_hooks
```

Demo 注册 `LoggingHook` 和 `MetricsHook`，完成一次 calculator 调用再回答。终端先显示完整生命周期日志，随后显示 `2/2` steps、`2/2` model calls、`1/1` tool calls 和 `0` errors。

## 最值得阅读的代码

1. `Hook`：只覆盖所需回调的最小扩展契约。
2. `HookContext`：不暴露可变 State 的事件数据边界。
3. `HookManager._dispatch()`：按注册顺序调用与失败归因。
4. `ToolAgentLoop._run_state()`：控制流中的七个稳定发射点。
5. `ToolAgentLoop._notify_error()`：保留原始失败的错误通知。
6. `LoggingHook` / `MetricsHook`：同一事件边界上的两个独立消费者。
7. `tests/test_hooks.py`：顺序、错误和“不改变结果”的可执行规格。

## Trade-off

- 串行 Hook 容易理解，但慢 Hook 会增加 Agent 延迟。
- fail-fast 适合关键扩展，不适合所有遥测场景；未来可增加错误隔离策略。
- HookContext 使用统一可选字段，简单但不如每种事件独立类型严格。
- MetricsHook 是进程内聚合，重启后清零，也没有并发锁。
- 当前不支持 Hook 优先级、卸载、并行分发或修改请求/结果。

## 尚未解决的问题

- Policy / Permission 的 `ALLOW`、`REQUIRE_APPROVAL`、`DENY` 留给 HARN-11。
- Trace span、持久 metric exporter 和跨进程关联留给后续观测阶段。
- Retry / Backoff 与错误分类尚未实现。
- Hook 隔离、超时和采样策略尚未实现。
