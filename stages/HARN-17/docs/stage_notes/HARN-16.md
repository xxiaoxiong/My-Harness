# HARN-16：Scheduler + Reliability

## 本阶段解决的问题

HARN-15 已把 Client 与 Worker 解耦，但 Worker 仍只能按明确 Task ID 单次执行。多个 Task 同时到达时，没有优先级、并发上限或队列；临时 Model 故障直接落为 FAILED；Worker 超时或崩溃后的重复执行还可能再次触发有副作用的 Tool。

HARN-16 增加：

```text
Client submit
  → TaskScheduler priority queue
  → max_concurrency slots
  → AgentWorker attempt
  → RetryPolicy / timeout / exponential backoff
  → TaskStore outcome

ToolAgentLoop
  → stable call idempotency key
  → ToolExecutor
  → IdempotencyStore
  → Tool implementation only on cache miss
```

## Priority Queue 与 max_concurrency

`TaskScheduler.submit()` 创建 AgentTask 并进入内存优先队列。数值更高的 `priority` 先运行，相同优先级按提交顺序 FIFO。

`run_until_idle()` 只启动不超过 `max_concurrency` 个异步 Task。每个槽位调用同一个 AgentWorker，但 Worker 为每个 AgentTask 构造独立 ToolAgentLoop。已取消或不再是 PENDING 的陈旧队列项会被跳过。

```python
scheduler = TaskScheduler(store, worker, max_concurrency=2)
scheduler.submit("low", priority=1)
scheduler.submit("high", priority=10)
await scheduler.run_until_idle()
```

HARN-15 的显式 `worker.execute(task_id)` 仍可使用；Scheduler 使用更细的 `worker.begin()` 和 `worker.run_attempt()`，把“是否再试”的决定留在可靠性层。

## RetryPolicy

`RetryPolicy` 定义：

- `max_attempts`：包含第一次在内的总尝试数；
- `base_delay_seconds`：第一次失败后的等待；
- `max_delay_seconds`：退避上限；
- `retryable(error)`：错误分类函数。

第 n 次失败后的 delay：

```text
min(max_delay, base_delay × 2^(n-1))
```

默认只重试 `ModelProviderError` 和整个 Worker attempt 的 `TimeoutError`。参数/协议错误等非临时错误不会盲目重试。每次尝试都会递增 AgentTask.attempts；超过上限后 Worker 才把 Task 最终写为 FAILED。

## 两层 Timeout

本项目现在有两个不同层次的超时：

```text
Shell request timeout
  → LocalSandbox 终止进程
  → timed_out=True 的 Tool observation

Task attempt timeout
  → Scheduler 取消整个 Worker attempt
  → RetryPolicy 判断是否重试
```

Tool timeout 可以被 Agent 观察并调整下一步；Task timeout 是 Runtime 对整个 attempt 的硬边界。两者不能互相替代。

## Cancellation

`scheduler.cancel(task_id)` 支持：

- 队列中的 PENDING：从 queued set 移除并写为 CANCELLED；
- 正在运行的 RUNNING：取消对应 asyncio Task，由 Scheduler 清理并写为 CANCELLED；
- WAITING / SUSPENDED：直接写为 CANCELLED；
- 已 CANCELLED：幂等返回；
- 已 COMPLETED / FAILED：拒绝改写历史终态。

这是同一进程内的协作取消。若 Provider、Tool 或底层 SDK 吞掉 cancellation，Runtime 不能保证立即停止；进程类 Tool 仍需依赖 Sandbox 自己的终止边界。

## 两种 Idempotency

### Task 提交幂等

Client 可传 `idempotency_key`：

```python
first = scheduler.submit(goal, idempotency_key="request-42")
same = scheduler.submit(goal, idempotency_key="request-42")
assert first.task_id == same.task_id
```

同一 key 与相同 goal/priority 返回已有 Task，不重复入队；同一 key 配不同输入会抛 `TaskSubmissionConflictError`。

### Tool 执行幂等

ToolAgentLoop 根据：

```text
task_id + tool name + canonical JSON arguments
```

生成稳定 SHA-256 key。配置了 `IdempotencyStore` 的 ToolExecutor 在实际执行成功后立即保存 `IdempotencyRecord(call, result)`；重放相同调用时直接返回已完成结果。

```python
store = InMemoryIdempotencyStore()
harness = Harness(idempotency_store=store)
```

缓存发生在 Tool 返回之后、`after_tool_call` Hook 与 Agent Checkpoint 之前。因此即使 Worker 在“Tool 已产生副作用、Checkpoint 尚未保存”的窗口崩溃，重试同一 Task/Call 也能复用结果。

预期 ToolError 不会缓存，因为失败调用可能在外部条件改变后恢复。

## 四个可靠性 Case

### Case 1：LLM API 临时失败

第一次 Model 调用抛 `ModelTransportError`。Scheduler 保持 Task RUNNING，等待退避后创建新 attempt；后续成功则 Task COMPLETED。只有尝试耗尽才 FAILED。

### Case 2：Tool Timeout

ShellTool 通过 LocalSandbox 执行超时命令。Sandbox 杀死进程并返回 `timed_out=True`，Agent 在下一步观察后完成，不会让 Worker 永久挂住。另有 Scheduler `task_timeout_seconds` 保护整个 attempt。

### Case 3：Tool 后、Checkpoint 前崩溃

测试 Hook 在第一次 `after_tool_call` 抛出模拟 Worker Crash。此时 Tool result 已写入 IdempotencyStore，但尚未进入 Agent State/Checkpoint。Scheduler 从头重试时相同 key 命中缓存，真实副作用执行次数仍为 1。

### Case 4：Agent 连续调用相同 Tool

同一 Task 内 name 和 arguments 完全相同的连续调用得到同一完成结果，Tool implementation 只执行一次。这保护常见的模型重复动作，但也带来“调用方是否真的想重复副作用”的语义取舍。

## Retry、Recovery、Resume 不相等

```text
Retry    = 重新尝试一个失败的操作/attempt
Recovery = 从 durable truth 重建崩溃前状态
Resume   = 从明确暂停点继续，例如 WAITING_APPROVAL
```

- 临时 Model 错误通常适合 Retry；
- 进程重启后加载 Checkpoint 是 Recovery；
- 用户批准 pending Tool Call 后继续是 Resume；
- 没有可靠恢复点时，Retry 可能从旧位置重复副作用，因此必须考虑 idempotency。

## 为什么 Side Effect + Retry 危险

网络超时或 Worker crash 只能证明“调用方没有看到成功”，不能证明“外部动作没有成功”。若直接重试 `charge_card`、`send_email` 或 `git_commit`，可能产生重复副作用。

本阶段把完成的 Tool Result 尽早写入幂等 Store，缩小不确定窗口。但生产系统仍需要 durable/atomic storage，最好还把 idempotency key 传给真正执行副作用的外部系统。

## 最小 Demo

```bash
python -m examples.harn_16_scheduler_reliability
```

Demo 依次显示 priority/max_concurrency、临时模型错误与指数退避、Tool timeout、Tool 后崩溃恢复、连续重复调用去重，以及队列取消。

## 最值得阅读的代码

1. `TaskScheduler._launch_available()`：priority queue 与并发槽位。
2. `TaskScheduler._run_task()`：attempt timeout、错误分类和退避。
3. `RetryPolicy`：次数与指数 delay 计算。
4. `AgentWorker.begin/run_attempt/fail`：执行机制与可靠性决策分离。
5. `tool_call_idempotency_key()`：稳定的逻辑调用身份。
6. `ToolExecutor.execute()`：cache hit 与副作用后立即保存。
7. `tests/test_scheduler_reliability.py`：四个 Case 的可执行规格。

## Trade-off

- Scheduler、queue、TaskStore 和 IdempotencyStore 都是单进程内存实现。
- 没有 lease、compare-and-swap 或 distributed lock，不能安全运行多进程 Worker。
- Task timeout 使用协作式 asyncio cancellation，不能强杀不响应取消的任意代码。
- 默认 retry 分类刻意保守；业务需显式扩展 transient error 判定。
- exact-call 幂等会把同一 Task 中有意重复的同参数调用也视为重放。
- Tool result cache hit 仍会触发 Tool 生命周期 Hook；本阶段没有单独的 cache-hit event。
- 内存幂等记录与进程同生共死，不能覆盖整机崩溃。
- Tool 执行与 idempotency record 保存不是跨系统原子事务，仍存在极小失败窗口。

## 尚未解决的问题

- durable queue、数据库 Task Store、Worker lease/heartbeat 与崩溃 reclaim 尚未实现。
- 外部服务端 idempotency key、事务 outbox 与 exactly-once 不在本阶段保证范围。
- retry、状态转移、cache hit 与并发行为的结构化追踪留给 HARN-17。
- 跨进程 Scheduler、动态扩缩容、公平性与配额尚未实现。
