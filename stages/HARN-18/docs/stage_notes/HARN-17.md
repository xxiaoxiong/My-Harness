# HARN-17：Observability

## 本阶段解决的问题

HARN-16 已能调度和重试 Task，但排障仍要从日志、Agent State、Checkpoint 和异常栈中人工拼接事实。HARN-17 为每个 Task 引入稳定关联标识，并把 Task、Step、Model、Tool 和 Runtime 事件统一为结构化 Trace。

```text
session_id
  └── task_id / trace_id
        ├── Task state transition / retry
        ├── Step 1
        │     ├── Model call / context / token / TTFT / latency
        │     └── Tool call / result / latency
        └── Step 2
              └── Model call / final answer
```

## 三种 ID

- `task_id`：Runtime 查找和操作 Task 的业务身份；
- `trace_id`：一次 Task 执行链路的观测身份；
- `session_id`：把同一会话中的多个 Task 关联起来。

`AgentTask.create()` 默认生成三者，也允许 Client 显式传入 `trace_id` / `session_id`。不可变 Task 的状态更新会保留这些 ID。提交幂等键命中已有 Task 时，显式但不同的 Trace/Session ID 会被判为冲突，避免两条链路意外合并。

## 事件模型与 Recorder

`TraceEvent` 是后端无关的结构化记录，包含：

```text
event_id, kind, task_id, trace_id, session_id,
step, occurred_at, details
```

`TraceRecorder` 是输出端口。本阶段提供：

- `InMemoryTraceRecorder`：测试、教学和进程内检查；
- `StructuredLogTraceRecorder`：每个事件输出为一行 JSON；
- `CompositeTraceRecorder`：把同一个事件扇出到多个 Recorder。

因此 Agent/Runtime 不依赖 OpenTelemetry、Langfuse 或 Prometheus。以后接入这些系统时，只需新增 Recorder，不需要改 Agent Loop。

## Agent Step → Model + Tool

`TracingHook` 利用 HARN-10 的生命周期 Hook 记录：

- Step started/completed；
- Model call started/completed；
- 实际 ModelRequest 的消息数与字符数；
- Token usage、TTFT 和 provider latency；
- Tool call、Tool result 与 Harness 侧耗时；
- Hook 收到的异常及发生 phase。

`format_trace_tree()` 把平坦事件流投影为 `Task → Step → Model + Tool`。事件本身仍保持 append-only，树只是查询视图，不是第二份事实来源。

非流式 Provider 通常拿不到真正 TTFT。`ModelResponse.ttft_ms` 因此允许为 `None`，但 Trace 仍记录 `available=false`，明确区分“指标不可用”和“TTFT 为零”。支持流式或底层计时的 Provider 可填入真实值。

## Task 状态与 Retry

`TracingTaskStore` 装饰任意 `TaskStore`，仅在持久化状态真正变化时记录 transition：

```text
null → pending → running → completed
                         ↘ failed
```

同一状态内的 `attempts` 更新不会伪装成状态转移。

`TaskScheduler` 通过轻量 `RetryObserver` 暴露已决定的重试；`TracingRetryObserver` 记录失败 attempt、下一 attempt、backoff、异常类型和归因层。Runtime 只依赖 callback 协议，不反向依赖 observability 包。

## 失败归因

`classify_failure()` 把异常类型和 Agent phase 映射到最可能的层：

| 层 | 典型信号 |
| --- | --- |
| Model | `ModelProviderError`、model call phase |
| Prompt | 模型输出无法解析为 Agent action |
| Context | ContextBuilder 或预算构造失败 |
| Tool | Tool 生命周期或 Policy/Tool 执行失败 |
| Harness | Hook/扩展生命周期失败 |
| Runtime | 状态、Checkpoint、调度等控制流失败 |
| Infrastructure | 未被更具体边界捕获的连接、OS、timeout 错误 |

这是诊断线索，不是绝对根因判定。比如模型输出格式错误既可能来自 Prompt，也可能来自 Model；Trace 保留 phase、异常类型和原始消息，让操作者继续验证。

## 最小组合

```python
memory = InMemoryTraceRecorder()
store = TracingTaskStore(InMemoryTaskStore(), memory)
task = scheduler.submit("goal", session_id="conversation-42")

identity = TraceIdentity(task.task_id, task.trace_id, task.session_id)
hooks = HookManager([TracingHook(identity, memory)])
retry_observer = TracingRetryObserver(memory)
```

完整 Demo 同时写结构化 JSON 日志和内存树：

```bash
python -m examples.harn_17_observability
```

## 最值得阅读的代码

1. `harness/observability/events.py`：事件种类、三种 ID 与失败层；
2. `harness/observability/recorder.py`：Recorder 端口及结构化日志；
3. `harness/observability/adapters.py`：Hook、TaskStore、Retry 到 Trace 的映射；
4. `harness/observability/tree.py`：平坦事件到 Task 树的投影；
5. `tests/test_observability.py`：瞬时 Model 失败、Retry、Tool 和状态转移的同链路断言。

## Trade-off

- Recorder 当前是同步接口，慢后端会增加 Runtime 延迟；生产适配器应使用队列和批量导出；
- 内存 Recorder 不持久化，进程退出后事件丢失；
- structured logger 使用 `default=str` 兜底未知 Tool result，复杂对象的原始类型可能丢失；
- Hook 计时为进程内单调时钟，provider latency 则由适配器提供，两者的口径不同；
- 同一 step 的 retry 事件会在树中合并到相同步号，可通过事件顺序和 retry 边界区分 attempt。

## 尚未解决的问题

- OpenTelemetry span、Langfuse trace 和 Prometheus metric exporter 尚未实现；
- 没有跨进程 Trace backend、采样、保留期、脱敏或租户隔离；
- 尚未为 Tool cache hit、Policy decision、Checkpoint I/O 建立专门事件；
- Trace Recorder 失败的降级、缓冲和 backpressure 策略留待生产化阶段处理。
