# HARN-05：Agent State + Trajectory

## 本阶段解决的问题

HARN-04 的 Tool Loop 已能执行动作，但运行事实散落在局部变量中：消息列表只在 `run()` 内存在，计数器和最后结果由返回对象临时拼出，也没有统一 task identity 或时间信息。调用方无法完整检查 Agent 当前在哪里、做过什么。

HARN-05 引入 `AgentState` 作为一次任务运行的 system of record，并以有序 `TrajectoryEvent` 保存执行路径。`ToolAgentRunResult` 不再复制多个结果字段，而是持有完成后的 State，并通过只读属性提供常用值。

## 三个概念不是一回事

| 概念 | 回答的问题 | 当前实现 |
| --- | --- | --- |
| State | 系统现在真实处于什么状态？ | 一个任务的 `AgentState` |
| Context | 这一次模型看到了什么？ | 每次创建的 `ModelRequest.messages` tuple |
| Trajectory | Agent 按什么顺序走到这里？ | `AgentState.trajectory` 中的有序事件 |

State 可以包含 Context 的来源数据和完整 Trajectory，但三者不能互换。例如第二次模型调用只需要当时的消息快照；State 还包含 task ID、状态、工具对象和时间；Trajectory 则记录调用和动作的先后关系，而不是重新保存每份完整 Prompt。

## AgentState 必备字段

```text
task_id
goal
current_step
status
messages
tool_calls
tool_results
created_at
updated_at
```

本实现另外保存：

- `trajectory`：按 sequence 排序的事件列表；
- `final_answer`：正常完成时的最终答案。

`AgentState.start()` 可接受调用方提供的 `task_id`，未提供时生成 UUID。所有时间使用 timezone-aware UTC `datetime`，避免本地时区歧义。

## 完整状态转移与调用链

```text
AgentState.start(status=RUNNING, current_step=0)
        ↓
begin_model_step() ── current_step += 1
        ↓
ModelRequest(tuple(state.messages))  ← Context snapshot
        ↓
ModelProvider.generate()
        ↓
record MODEL_CALL + append assistant message
        ↓
parse ToolCall
        ↓
state.tool_calls.append + record TOOL_CALL
        ↓
ToolExecutor.execute()
        ↓
state.tool_results.append + record TOOL_RESULT
        ↓
append Tool Result message
        ↓
next model Context snapshot
        ↓
record MODEL_CALL + append final assistant message
        ↓
state.finish(answer)
        ↓
status=FINISHED + record FINAL_ANSWER
```

如果模型调用次数达到 `max_steps`，State 转为 `MAX_STEPS_REACHED` 并追加同名 trajectory 事件。

## 一条完整 Trajectory

一次 calculator 任务的事件顺序是：

```text
1. MODEL_CALL
2. TOOL_CALL
3. TOOL_RESULT
4. MODEL_CALL
5. FINAL_ANSWER
```

`TrajectoryEvent` 包含：

- `sequence`：从 1 开始、在一次任务内连续递增；
- `kind`：模型调用、工具调用、工具结果、最终答案或最大步数；
- `occurred_at`：事件发生时的 UTC 时间；
- `details`：该事件最小的 JSON-compatible 事实。

MODEL_CALL 只记录 step、model、response ID 和输入消息数量，不复制整份 Context。完整消息属于 State，某次输入属于 Context，事件只负责说明路径。

## 最值得阅读的代码

1. `AgentState.start()`：一次任务真实状态的创建边界。
2. `begin_model_step()`：`current_step` 与模型调用次数的明确关系。
3. `record_tool_call()` / `record_tool_result()`：同时更新当前状态和追加历史事实。
4. `TrajectoryEvent`：不可变的单个路径事件。
5. `ToolAgentLoop.run()`：现在如何围绕 State 驱动每一次状态转移。

## 不变量与错误边界

- `current_step` 从 0 开始，只在发起模型调用前递增。
- `created_at` 和 `updated_at` 必须带时区，且更新时间不能早于创建时间。
- trajectory sequence 根据当前长度生成，正常写入路径连续递增。
- `FINISHED` 或 `MAX_STEPS_REACHED` 状态拒绝继续调用状态转移方法。
- 最终答案必须是非空字符串。
- 工具调用和结果分别保存在列表中；错误结果也是真实结果，不会从 State 消失。

## 架构收益

Runtime 不再依赖零散局部变量来解释运行结果。测试、UI 或未来持久化层可以从一个 `AgentState` 获取任务身份、当前位置、完整消息、工具活动、最终状态和执行轨迹。

Trajectory 让“最后答案正确”之外的行为也可检查：可以看到模型调用了几次、是否先执行工具、错误是否进入反馈，以及在哪一步触发硬停止。这为 Checkpoint、恢复和可观测性提供必要基础，但本阶段不提前实现那些机制。

## Trade-off

- `AgentState` 是运行期可变对象，调用方拿到引用后也能修改列表；更严格的读写边界留待持久化和 Runtime 演进。
- 每次消息和事件都会累积在内存中，长任务尚无容量控制。
- 当前 Context 仍直接使用 `state.messages` 的 tuple 快照，没有独立选择或裁剪策略。
- Trajectory details 采用轻量 JSON-compatible mapping，没有版本号或专用事件 payload 类型。
- 模型协议错误抛出异常时，尚未把 State 转为 `FAILED`；失败模型将在后续运行时阶段扩展。

## 尚未解决的问题

- Context 仍由 Agent Loop 组装；留给 HARN-06 ContextBuilder。
- 没有 Context Budget 或历史裁剪；留给 HARN-06/HARN-07。
- State 只存在于内存，没有序列化或 Checkpoint；留给 HARN-08。
- 没有 Interrupt/Resume；留给 HARN-09。
- 没有 Hook、结构化 trace/metric 或失败恢复策略；留给后续阶段。

## 最小 Demo

```bash
python -m examples.harn_05_agent_state
```

Demo 对同一次运行分别打印完整 Agent State、两次 ModelRequest 的 Context 快照大小和五个有序 Trajectory 事件，可直接观察三者差异。
