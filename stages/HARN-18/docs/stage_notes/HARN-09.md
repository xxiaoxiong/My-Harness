# HARN-09：Interrupt / Resume

## 本阶段解决的问题

Checkpoint 能从意外退出恢复，却不能表达“Runtime 主动停下来等待外部决定”。如果模型请求 `delete_file`，进程不能一边声称等待批准，一边已经执行删除；也不能要求原来的 Python 进程或 HTTP Connection 一直存活。

HARN-09 增加一个可持久化的等待态：

```text
Agent → delete_file → Interrupt → Checkpoint → WAITING_APPROVAL
                                              ↓
新进程 + 用户决定 → Resume → 执行或拒绝 → Agent 继续
```

## State 新增的事实

- `AgentStatus.WAITING_APPROVAL`：当前任务不应继续调用模型。
- `AgentState.pending_tool_call`：等待决定、尚未产生 Tool Result 的调用。
- `TrajectoryEventKind.INTERRUPT`：记录暂停原因与 Tool 名称。
- `TrajectoryEventKind.RESUME`：记录批准或拒绝决定。

等待态有明确不变量：`pending_tool_call` 必须是最新 Tool Call，并且 Tool Call 数量恰好比 Tool Result 多一。JSON 加载也会经过同样的 `AgentState` 校验，因此篡改或不完整的等待数据不能恢复。

## Runtime 控制边界

本阶段通过 `ToolAgentLoop(approval_required_tools={...})` 指定需要暂停的 Tool 名称：

```text
Model returns Tool Call
  → record Tool Call
  → name requires approval?
      ├─ no  → ToolExecutor.execute
      └─ yes → state.interrupt_for_approval
               → save Checkpoint
               → return WAITING_APPROVAL
```

`delete_file` 实现本身只负责参数和根目录边界，不判断“谁可以批准”。这样能清楚看到执行机制与安全决策是两个问题；通用的 `PolicyEngine` 会在 HARN-11 引入。

只要 `approval_required_tools` 非空，构造 Runtime 时就必须提供 `CheckpointStore`。这条约束保证主动暂停一定是可跨进程恢复的，而不是只能依赖内存对象。

## Resume 的三种输入

```python
await loop.resume(task_id)                 # 仍然等待，不做任何事
await loop.resume(task_id, approve=True)   # 执行 pending Tool Call
await loop.resume(task_id, approve=False)  # 不执行，产生 rejected Tool Result
```

批准和拒绝都会记录 `RESUME` 事件。拒绝会形成普通错误观察：

```json
{
  "name": "delete_file",
  "result": null,
  "error": "tool call rejected by user"
}
```

模型下一步可以读取该观察并调整行为。对非等待任务传入批准参数会报错，防止把决定误用到另一个执行阶段。

## 跨进程恢复为什么成立

第一次进程返回 `WAITING_APPROVAL` 前，Checkpoint 已保存 `status` 和 `pending_tool_call`。之后可以彻底结束进程。第二次进程重新创建 Provider、ToolRegistry、ToolExecutor 和 AgentLoop，再按 `task_id` 读取 State。

```text
Process A                         Disk                       Process B
Tool Call → Interrupt → save  →  waiting checkpoint  →  load → decision → continue
          process may exit                                  new runtime objects
```

因此 Long-running Agent 的生命周期不等于服务器请求、进程或内存对象的生命周期。

## 副作用与恢复语义

批准后，Runtime 先把加载到内存的 State 改为 running，再调用 Tool；磁盘 Checkpoint 在 Tool Result 成功保存前仍保持 `WAITING_APPROVAL`。如果此时进程崩溃，恢复后会再次看到同一个待批准调用。

这避免了把“未完成调用”误认为完成，但属于至少一次执行语义：若 Tool 已产生外部副作用、进程却在保存结果前崩溃，重新批准可能重复副作用。生产系统需要 operation ID、幂等 Tool、事务日志或补偿机制。

## `delete_file` Demo Tool 的边界

- 只能接收一个相对路径；
- 只能删除配置根目录下的普通文件；
- 拒绝绝对路径和 `..` 目录穿越；
- Demo 根目录固定为被 Git 忽略的 `.harness-approval-demo/`；
- 是否需要批准由 Runtime 配置，不由 Tool 自己硬编码。

## Checkpoint Schema 2

Checkpoint schema 从 1 升为 2，`state` 新增 `pending_tool_call`。本阶段显式拒绝旧 schema，而不是猜测等待状态；HARN-08 的独立快照仍保留并可运行 schema 1 实现。通用迁移机制仍留待后续。

## 最小 Demo

进程 A 请求删除并主动返回等待态，文件仍存在：

```bash
python -m examples.harn_09_interrupt_resume start harn-09-demo
```

进程 B 明确批准并继续，文件此时才被删除：

```bash
python -m examples.harn_09_interrupt_resume resume harn-09-demo --approve
```

也可重新执行第一条命令后测试拒绝：

```bash
python -m examples.harn_09_interrupt_resume resume harn-09-demo --reject
```

## 最值得阅读的代码

1. `AgentState.interrupt_for_approval()`：建立等待态不变量。
2. `AgentState.resume_from_approval()`：记录决定并取回 pending call。
3. `ToolAgentLoop._run_state()`：在执行前截断危险调用。
4. `ToolAgentLoop._resume_waiting()`：批准、拒绝与继续执行。
5. `JsonCheckpointStore`：等待态的序列化和恢复校验。
6. `tests/test_interrupt_resume.py`：Tool 在批准前确实没有执行的证据。

## Trade-off

- 工具名称集合只解决 HARN-09 的暂停机制，不是完整权限策略。
- 一个 Task 当前最多有一个 pending Tool Call，没有并行审批。
- 决定只记录布尔值，没有审批人、理由、时限或签名。
- 拒绝后 Agent 可以再次请求相同 Tool；本阶段没有重试策略。
- 至少一次副作用语义需要上层幂等设计。

## 尚未解决的问题

- Hook / Middleware 生命周期事件留给 HARN-10。
- 通用 Policy / Permission 决策与 `DENY` 留给 HARN-11。
- 多审批人、审批过期、撤回、租约与并发竞争尚未实现。
- Checkpoint schema 迁移与 exactly-once Tool 执行尚未实现。
