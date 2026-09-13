# HARN-15：Task Runtime + Worker

## 本阶段解决的问题

到 HARN-14 为止，调用方仍直接等待：

```text
Client / CLI
  → Agent.run()
  → 同一个请求连接等待最终结果
```

即使 Agent 内部已有 Checkpoint，任务的外部生命周期仍与发起调用绑在一起。连接断开后，调用方没有独立的 Task ID、状态记录或 Worker 可以继续拥有这项工作。

HARN-15 把调用链拆成：

```text
Client
  → AgentTask(PENDING)
  → TaskStore

AgentWorker
  → TaskStore.get(task_id)
  → Agent Harness / ToolAgentLoop
  → TaskStore.update(outcome)

Later Client
  → TaskStore.get(task_id)
```

Client 的职责变成“提交并查询”，Worker 的职责变成“执行并持久化状态”。

## AgentTask

`AgentTask` 是不可变的外部运行记录，包含：

- `task_id` 与用户 goal；
- 当前 `TaskStatus`；
- 创建、更新时间；
- 完成后的 final answer，或失败后的 error。

状态集合严格对应路线图：

```text
PENDING
RUNNING
WAITING
SUSPENDED
COMPLETED
FAILED
CANCELLED
```

本阶段允许的主要转换：

```text
PENDING ──→ RUNNING ──→ COMPLETED
   │             ├────→ FAILED
   │             ├────→ WAITING ──→ RUNNING
   │             └────→ SUSPENDED
   └──────→ CANCELLED

WAITING / SUSPENDED ──→ CANCELLED
```

完成、失败和取消是 terminal。`COMPLETED` 必须有 final answer；`FAILED` 必须有 error；非 terminal Task 不能偷带终态数据。不可变 record 让 Store 中每次更新都是显式状态替换。

## Task 状态与 Agent 状态

两者不是重复概念：

```text
AgentState：一次 Agent Loop 内的消息、Step、Tool 和 Checkpoint 真相
AgentTask：Client / Worker 看到的工作生命周期与最终结果
```

Worker 在边界处映射：

```text
Agent FINISHED           → Task COMPLETED
Agent WAITING_APPROVAL   → Task WAITING
Agent MAX_STEPS_REACHED  → Task SUSPENDED
Agent / Worker exception → Task FAILED
```

这样客户端无需理解 Agent 内部 trajectory，也能知道工作是否完成、等待外部决定或失败。

## TaskStore

`TaskStore` 定义四个同步存储操作：

```python
create(task)
get(task_id)
update(task)
list_tasks(status=None)
```

`InMemoryTaskStore` 保持创建顺序、拒绝重复 ID、不允许 update 不存在的 Task，并可按状态过滤。它足以展示 Client 与 Worker 解耦；持久化数据库或消息队列可以在不改变 AgentTask/Worker 调用方的情况下实现同一接口。

## AgentWorker

Worker 接收 `TaskStore` 和 `AgentLoopFactory`：

```python
worker = AgentWorker(store, loop_factory)
outcome = await worker.execute(task_id)
```

`execute()` 只接收 PENDING Task，先把 RUNNING 写回 Store，再构造和运行 ToolAgentLoop。无论 Agent 最终完成、等待、达到步数上限或抛出异常，Worker 都把对应 Task 状态写回 Store。

对于 HARN-09 的审批中断：

```python
waiting = await worker.execute(task_id)
completed = await worker.resume(task_id, approve=True)
```

Worker 的 `resume()` 只接收 WAITING Task，并复用 Agent Checkpoint 继续执行。Task Store 管外部生命周期，Checkpoint Store 管 Agent 内部恢复点，两者各有职责。

## Client lifetime 不再等于 Agent lifetime

提交 Client 可以在 `store.create(task)` 后立刻返回 Task ID。Worker 可能稍后才启动；执行期间其他 Client 可读取 RUNNING；原 Client 已不存在也不会影响 Worker 持有的 Agent Loop。

本阶段的 `InMemoryTaskStore` 只保证“请求连接生命周期”和“任务生命周期”分离，不保证服务进程重启后的持久化。这是接口解耦已经成立、具体持久化尚未加入的边界。

## 最小 Demo

```bash
python -m examples.harn_15_task_runtime
```

Demo 中提交函数创建 PENDING Task 后立即结束。随后独立 Worker 启动，Task Store 可在模型调用尚未释放时读到 RUNNING；完成后，模拟后来的 Client 再按相同 Task ID 读取 final answer。

## 最值得阅读的代码

1. `AgentTask`：不可变 Task record 与状态不变量。
2. `_TRANSITIONS`：本阶段明确允许的生命周期变化。
3. `TaskStore` / `InMemoryTaskStore`：Client 与 Worker 的共享边界。
4. `AgentWorker.execute()`：PENDING 到 Agent 运行结果的映射。
5. `AgentWorker.resume()`：Task WAITING 与 Agent Checkpoint Resume 的衔接。
6. `tests/test_task_runtime.py`：断连、等待、失败和 suspend 规格。

## Trade-off

- InMemoryTaskStore 随进程退出丢失，不是 durable production store。
- Worker 通过明确 Task ID 执行；尚没有队列、优先级或自动 claim。
- Store 没有 compare-and-swap 或 lease，多 Worker 并发领取会产生竞争。
- Worker 捕获普通异常并记录 FAILED，但尚无错误分类、retry 或 backoff。
- Worker 被强制取消或在 RUNNING 写入后崩溃时，Task 可能停留在 RUNNING。
- CANCELLED 已是状态模型的一部分，但运行中协作取消留给下一阶段。
- SUSPENDED 表示未完成的硬停止，本阶段不提供继续该状态的执行语义。

## 尚未解决的问题

- priority、task queue 与 `max_concurrency` 留给 HARN-16 Scheduler。
- retry、timeout、exponential backoff、可靠 cancellation 与 idempotency 留给 HARN-16。
- Worker crash 后的 lease/reclaim 和 Task/Checkpoint 对账尚未实现。
- durable Task Store、跨进程通知和分布式 Worker 尚未实现。
- Task trace、状态转移日志与性能诊断留给 HARN-17。
