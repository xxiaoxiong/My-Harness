# HARN-08：Checkpoint

## 本阶段解决的问题

HARN-07 的 `AgentState` 只存在于当前 Python 进程中。进程退出后，即使 State 和 Trajectory 在内存里是完整的，也无法重建执行现场。

HARN-08 增加持久化恢复点：每个完整 Agent step 结束后，把恢复所需事实原子写入 JSON。新进程只要拿到 `task_id`，就能加载最近一次成功保存的状态并继续运行。

## State 不等于 Checkpoint

```text
State                         Checkpoint
当前进程里的可变事实          某一时刻 State 的持久化恢复点
可能已进入尚未完成的下一步    只代表最后一个完整结束的 step
随运行持续变化                保存后不会随内存对象继续变化
```

Demo 在内存中进入 Step 4 后故意抛出异常。由于 Step 4 没有完成，磁盘上的 Checkpoint 仍是 Step 3；重启后会重新执行 Step 4。这种“至少一次”语义也意味着，真实世界中有外部副作用的 Tool 还需要幂等键或去重机制。

## 持久化结构

每个任务保存为 `<checkpoint_dir>/<task_id>.json`，顶层明确包含路线图要求的字段：

```json
{
  "schema_version": 1,
  "saved_at": "2026-09-13T12:00:00+00:00",
  "task_id": "harn-08-demo",
  "status": "running",
  "step": 3,
  "state": {
    "task_id": "harn-08-demo",
    "goal": "...",
    "current_step": 3,
    "status": "running",
    "messages": [],
    "tool_calls": [],
    "tool_results": [],
    "created_at": "...",
    "updated_at": "...",
    "final_answer": null
  },
  "trajectory": [],
  "context": {
    "message_count": 8,
    "total_chars": 1000,
    "max_context_chars": 4000,
    "truncated": false,
    "dropped_messages": 0,
    "summary": null,
    "summarized_messages": 0
  }
}
```

`trajectory` 独立放在顶层，避免把执行历史埋在 State 内部；加载时再把它组装回 `AgentState.trajectory`。顶层 `task_id`、`status`、`step` 会与 State 内字段交叉校验，防止恢复互相矛盾的数据。

## 保存与恢复调用链

```text
ToolAgentLoop._run_state
  → 完成 Model Call
  → 可选：完成 Tool Call + Tool Result
  → 更新 AgentState / Trajectory
  → Checkpoint.capture(state, context metadata)
  → JsonCheckpointStore.save
  → 写临时文件
  → os.replace 原子替换 task JSON

新进程
  → ToolAgentLoop.resume(task_id)
  → JsonCheckpointStore.load
  → 校验 schema / 类型 / 重复字段 / trajectory 顺序
  → 重建 AgentState
  → 从 current_step + 1 继续 _run_state
```

最终答案和 `MAX_STEPS_REACHED` 也会保存。对终态调用 `resume()` 时直接返回已保存 State，不重复请求模型。

## 为什么采用 JSON

JSON 让本阶段的持久化边界可直接观察，适合逐字段学习和手工检查。`CheckpointStore` 是抽象边界，Runtime 不依赖 JSON 细节，后续可以换成 SQLite 或远程数据库。

写入采用“同目录临时文件 + `os.replace`”，避免进程在写到一半时留下半截正式文件。随机临时文件名也会在失败路径中清理。任务 ID 只允许有限的安全文件名字符，阻止目录穿越。

## Schema 与错误边界

- `schema_version` 当前固定为 `1`；未知版本拒绝恢复，而不是猜测字段含义。
- 文件不存在抛出 `CheckpointNotFoundError`。
- JSON、字段类型、时间、枚举或交叉校验错误抛出 `CheckpointCorruptError`。
- 不安全的 task ID 抛出 `InvalidCheckpointTaskId`。
- 每个任务只保留最新恢复点；本阶段不实现历史版本和并发写锁。

## 最小 Demo

先运行到 Step 3，然后模拟进程退出：

```bash
python -m examples.harn_08_checkpoint_demo harn-08-demo
```

新进程按路线图指定的入口恢复：

```bash
python resume.py harn-08-demo
```

第一条命令会显示磁盘恢复点为 `step=3, status=running`。第二条命令重新加载该文件，从 Step 4 继续，最终在 Step 5 得到答案并保存终态。

## 最值得阅读的代码

1. `Checkpoint` 与 `CheckpointContext`：运行时状态和恢复点的边界。
2. `CheckpointStore`：Runtime 面向的最小持久化契约。
3. `JsonCheckpointStore.save()`：原子替换与安全 task ID。
4. `_checkpoint_from_payload()`：不信任磁盘数据的完整重建路径。
5. `ToolAgentLoop._save_checkpoint()` 与 `resume()`：逐步落盘和恢复入口。
6. `tests/test_checkpoint.py`：Step 4 崩溃后仍从 Step 3 恢复的证据。

## Trade-off

- JSON 可读但每步重写整个 State，长任务的写放大会很明显。
- `os.replace` 保证单个文件替换的原子性，但没有提供多进程并发控制。
- Checkpoint 记录的是最近一次 Model 输入的 Context metadata，不复制完整 Context；完整事实仍由 State 保存，恢复时重新构建 Context。
- 当前是“最后写入者胜出”，没有乐观锁、租约或 checkpoint revision。
- 对 Tool 外部副作用的 exactly-once 保证不属于本阶段。

## 尚未解决的问题

- 主动暂停、等待批准和明确的可恢复等待态留给 HARN-09。
- Checkpoint 迁移、SQLite、并发锁和历史保留策略尚未实现。
- 外部 Tool 的幂等键、事务边界和副作用补偿尚未实现。
- Hook、Trace、Metric 和结构化运行观测留给后续阶段。
