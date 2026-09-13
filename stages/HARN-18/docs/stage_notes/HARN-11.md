# HARN-11：Policy / Permission

## 本阶段解决的问题

模型输出 Tool Call 只表示“模型想做什么”，不等于“系统允许执行”。若安全规则散落在 `delete_file`、`git_push` 或 Agent Loop 的条件分支里，规则难以复用、测试、审计和替换。

HARN-11 增加独立授权边界：

```text
Model intent
  → Tool Call
  → PolicyEngine.decide(PermissionRequest)
  → ALLOW / REQUIRE_APPROVAL / DENY
  → Runtime 执行对应控制流
```

Tool 仍只负责参数、业务边界和实际执行；Policy 只做权限决定；Runtime 负责落实决定。

## 核心类型

`PermissionRequest` 是 Policy 的输入：

- `task_id`、`goal`、当前 `step`；
- 完整 `ToolCall`，包括名称和参数。

因此 Policy 既能按 Tool 名称判断，也能检查参数，例如允许普通 shell 命令、拒绝 `{"command": "rm -rf demo"}`。

`PermissionDecision` 是严格三态枚举：

```text
ALLOW
REQUIRE_APPROVAL
DENY
```

自定义 `PolicyEngine` 使用异步 `decide()`，可以在未来查询远程权限服务。返回普通字符串会触发 `InvalidPermissionDecision`，Runtime 不会把模糊结果当成允许。

## 三条执行路径

### ALLOW

```text
decision=ALLOW
→ before_tool_call Hook
→ ToolExecutor.execute
→ after_tool_call Hook
→ Tool Result
```

### REQUIRE_APPROVAL

```text
decision=REQUIRE_APPROVAL
→ Interrupt
→ Checkpoint
→ WAITING_APPROVAL
```

批准后沿用 HARN-09 的跨进程 Resume。若没有配置 `CheckpointStore`，Runtime 会拒绝进入不可恢复的等待态。

### DENY

```text
decision=DENY
→ 不调用 ToolExecutor
→ ToolResult(error="tool call denied by policy")
→ 作为观察交给模型
```

DENY 不是 Runtime 异常，而是对当前 action 的正常控制决定。模型可以读取错误观察后改用安全方案或给出最终答案。因为 Tool 没有执行，`before_tool_call` / `after_tool_call` 也不会触发。

## Trajectory 可审计性

模型请求与 Runtime 决定分别记录：

```text
MODEL_CALL
TOOL_CALL                 ← 模型意图
PERMISSION_DECISION       ← Runtime 决定
TOOL_RESULT / INTERRUPT   ← 决定产生的结果
```

`PERMISSION_DECISION` 保存 Tool 名称和 `allow`、`require_approval` 或 `deny`。这样回放时不会把“模型建议删除”误读成“系统已经允许删除”。

## 内置 Policy

- `AllowAllPolicyEngine`：默认兼容行为，所有调用进入 Executor。
- `StaticPolicyEngine`：按规范化 Tool 名称查表，并支持默认决定。

```python
policy = StaticPolicyEngine(
    {
        "read_file": PermissionDecision.ALLOW,
        "write_file": PermissionDecision.ALLOW,
        "git_push": PermissionDecision.REQUIRE_APPROVAL,
        "remove_tree": PermissionDecision.DENY,
    }
)
```

需要按用户、环境、路径或参数判断时，继承 `PolicyEngine` 实现自己的 `decide()`。

## 与 HARN-09 的关系

HARN-09 用 `approval_required_tools` 集合先证明 Interrupt / Resume 机制。本阶段根实现删除这个临时分支，由 Policy 的 `REQUIRE_APPROVAL` 驱动同一个等待态；HARN-09 独立快照仍保留原始教学版本。

## 最小 Demo

```bash
python -m examples.harn_11_policy_permission
```

Demo 使用无副作用 Tool 展示四种意图：

```text
read_file  → ALLOW             → executed=True
write_file → ALLOW             → executed=True
git_push   → REQUIRE_APPROVAL  → 先等待，批准后执行
shell rm   → DENY              → executed=False
```

Demo 不会真的读写文件、推送 Git 或执行 shell，只记录 ToolExecutor 是否收到调用。

## 最值得阅读的代码

1. `PermissionRequest`：模型意图进入权限系统的数据边界。
2. `PolicyEngine.decide()`：可替换的异步决策契约。
3. `StaticPolicyEngine`：最小确定性规则实现。
4. `ToolAgentLoop._run_state()`：三态决定如何改变执行链。
5. `AgentState.record_permission_decision()`：意图与许可分开审计。
6. `tests/test_policy.py`：ALLOW、DENY 和错误 Policy 的执行规格。

## Trade-off

- `StaticPolicyEngine` 只有 Tool 名称规则，没有用户、角色或环境概念。
- `PermissionDecision` 不携带理由、规则 ID 或审批说明。
- 默认 AllowAll 有利于向后兼容，但生产系统通常应采用 default-deny。
- DENY 作为模型观察可能让模型再次请求相同行为；尚无重试抑制。
- Policy 决定没有缓存、超时、组合或优先级。

## 尚未解决的问题

- Plugin / Skill 对 Tool、Hook、Prompt 和 Policy 的组合注册留给 HARN-12。
- 审批主体、审计签名和细粒度身份权限尚未实现。
- Policy 规则语言、组合、热更新和远程 PDP 尚未实现。
- 操作级幂等与 exactly-once 外部副作用仍未解决。
