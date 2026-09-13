# HARN-12：Skill / Plugin

## 本阶段解决的问题

HARN-11 已有 Tool、Hook、Prompt 和 Policy，但应用仍需逐项手工拼装。一个领域能力往往同时需要多种组件，例如 Git 能力需要 Tool 定义、模型指导和提交审批规则；让使用者分别注册会遗漏依赖，也无法表达“加载这个能力包”。

HARN-12 增加组合根与扩展协议：

```text
Plugin.setup(harness)
  ├─ register_tool
  ├─ register_hook
  ├─ register_prompt_fragment
  └─ register_policy

Harness.create_agent_loop()
  → ToolRegistry + HookManager + ContextBuilder + CompositePolicyEngine
  → ToolAgentLoop
```

## Tool、Skill、Plugin 的区别

```text
Tool   = 一个原子动作，例如 git_diff
Skill  = 围绕一个任务领域组织的一组能力，例如 GitSkill
Plugin = 把能力注入 Harness 的通用扩展机制
```

`Skill` 是 `Plugin` 的语义化子类：两者使用相同 `setup(harness)` 加载协议，但 Skill 强调面向任务领域的组合，而 Plugin 也可以只添加日志、策略或提示。

## Harness 组合根

`Harness` 在 Runtime 创建前收集：

- `ToolRegistry` 中的 Tool；
- `HookManager` 中的 Hook；
- 按注册顺序排列的 `PromptFragment`；
- 交给 `CompositePolicyEngine` 的 Policy；
- 已加载 Plugin 的名称和实例。

创建 Agent Loop 时，Prompt Fragment 会附加到基础 System Prompt，Tool 进入 Executor，Hook 进入生命周期调度器，Policy 进入权限链。

Runtime 构造成功后 Harness 被 sealed，拒绝继续注册。这样某次运行使用的能力集合不会在中途变化；若构造参数无效、Runtime 没有创建成功，Harness 仍保持开放。

## Plugin 加载

```python
class MyPlugin(Plugin):
    def setup(self, harness: Harness) -> None:
        harness.register_tool(...)
        harness.register_hook(...)
        harness.register_prompt_fragment(...)
        harness.register_policy(...)

harness.load_plugin(MyPlugin())
```

Plugin 名称必须唯一。Tool 和 Prompt Fragment 也沿用各自的唯一名称检查，避免模型看到歧义能力。

## Prompt Fragment

`PromptFragment(name, content)` 是一个命名指令块。Harness 按加载顺序组成：

```text
Base System Prompt

Plugin Prompt [fragment-name]:
fragment content
```

它和 Tool Schema 一样，只有在创建 Runtime 前加载 Plugin 才会进入模型 Context。

## Policy 组合

不同 Plugin 可以各自贡献 Policy。`CompositePolicyEngine` 逐个求值，并采用明确优先级：

```text
DENY > REQUIRE_APPROVAL > ALLOW
```

任何 Policy 拒绝都会立即拒绝；没有拒绝但至少一个要求批准时进入等待态；否则允许。空 Policy 集合等价于 ALLOW。

## GitSkill

加载 `GitSkill` 后才注册：

```text
git_status
git_diff
git_commit
```

同时注入 `git-skill` Prompt Fragment，并为 `git_commit` 注册 `REQUIRE_APPROVAL` Policy。`git_status` 和 `git_diff` 默认允许。

三个 Tool 不直接启动本地进程，而是依赖注入的 `GitBackend`。本阶段提供 `InMemoryGitBackend`，让接口、组合和审批行为可以确定性运行；HARN-13 引入 Sandbox 后再提供受控的本地进程执行边界。这避免在扩展阶段提前把 `subprocess` 散落进 Tool。

## 加载前后

```python
harness = Harness()
assert harness.tools == ()

harness.load_plugin(GitSkill(backend))
assert [tool.schema.name for tool in harness.tools] == [
    "git_status", "git_diff", "git_commit"
]
```

“代码里存在 GitSkill 类”和“当前 Agent 拥有 Git 能力”是两件事；只有执行 Plugin setup 后，Tool Definitions 才会出现在 Context。

## 最小 Demo

```bash
python -m examples.harn_12_skill_plugin
```

Demo 会显示加载前 Tool 列表为空，加载后出现三个 Git Tool。Agent 依次调用 status、diff 和 commit；commit 先返回 `WAITING_APPROVAL`，新的 Harness 实例加载同一 Skill 后批准恢复。`InMemoryGitBackend` 最终记录一条 commit message，不会修改真实 Git 仓库。

## 最值得阅读的代码

1. `Plugin.setup()`：统一扩展注入协议。
2. `Harness.load_plugin()`：加载与重复名称边界。
3. `Harness.create_agent_loop()`：把注册内容组装成 Runtime。
4. `CompositePolicyEngine`：多 Plugin Policy 的确定性合并。
5. `GitSkill.setup()`：Tool + Prompt + Policy 的领域组合。
6. `GitBackend`：Skill 能力与未来 Sandbox 执行的分界。
7. `tests/test_plugins.py`：加载前后、四类注入、封闭和审批恢复规格。

## Trade-off

- Plugin setup 是同步且非事务的；setup 中途失败可能留下已注册组件。
- Harness 创建 Runtime 后不可再热加载或卸载 Plugin。
- Prompt Fragment 只按顺序拼接，没有优先级、冲突分析或 token 配额。
- Composite Policy 使用固定“最严格决定获胜”，不携带原因链。
- InMemoryGitBackend 用于学习，不操作真实仓库。

## 尚未解决的问题

- `LocalSandbox` 与真实受控进程执行留给 HARN-13。
- Plugin 发现、包元数据、版本、依赖解析和隔离尚未实现。
- Plugin setup 事务、卸载、热更新与能力命名空间尚未实现。
- Skill 配置持久化与远程分发尚未实现。
