# HARN-18：最终整合——Mini Coding Agent Harness

## 本阶段解决的问题

HARN-00～17 分别解决了配置、模型、循环、工具、状态、上下文、恢复、审批、扩展、安全、远端工具、任务运行、可靠性和排障问题，但“模块都存在”不等于“系统已经协同工作”。最后阶段不再发明大架构，而是用一个可运行的 Coding Agent 验证这些边界能够组合成完整闭环。

本阶段增加的领域能力只有：

```text
search_files
read_file
write_file
shell       （复用 HARN-13）
git_diff    （复用 HARN-12）
```

## 核心机制

### 工作区文件工具

`ReadFileTool`、`WriteFileTool` 和 `SearchFilesTool` 都绑定一个显式 workspace root：

- 只接受相对路径并用解析后的绝对路径检查越界；
- `read_file` 只读 UTF-8 文本并限制返回字符数；
- `write_file` 限制内容大小，在目标目录写临时文件后原子替换；
- `search_files` 限制文件大小和结果数，跳过 `.git`、`.venv`、`__pycache__` 及越界符号链接。

这只是应用层文件边界，不是 OS jail。进程 Tool 仍必须通过 Sandbox；生产环境应使用容器、权限隔离或远端执行环境。

### CodingSkill

`CodingSkill` 通过既有 Plugin API 一次注册五个 Tool、Coding prompt 和 Policy：

```text
CodingSkill.setup(harness)
  ├── SearchFilesTool
  ├── ReadFileTool
  ├── WriteFileTool
  ├── ShellTool(LocalSandbox)
  ├── GitDiffTool(SandboxGitBackend)
  ├── PromptFragment(coding-workflow)
  └── StaticPolicy(write_file/shell → REQUIRE_APPROVAL)
```

`SandboxGitBackend` 只允许 `git status` / `git diff`，明确拒绝 commit。它让 Git Tool 继续依赖 `GitBackend` 端口，同时所有真实 Git 进程仍只从 Sandbox 启动。

### 完整调用链

Demo 在临时 Git 仓库放入一个有缺陷的 `average()` 和两个测试，并执行：

```text
Client submit
  → TaskScheduler
  → Model 第一次连接失败
  → RetryPolicy + TracingRetryObserver
  → AgentWorker
  → Harness.load_plugin(CodingSkill)
  → ContextBuilder
  → search_files
  → read_file
  → write_file intent
  → Policy REQUIRE_APPROVAL
  → Checkpoint + Task WAITING
  → Worker.resume(approve=True)
  → 原子写入第一次修复
  → shell intent → approval → Sandbox
  → unittest FAILED / ZeroDivisionError
  → Model 观察失败
  → 第二次 write_file + approval/resume
  → 第二次 shell + approval/resume
  → unittest OK
  → SandboxGitBackend → git_diff
  → MCP project_hint
  → final answer
  → Task COMPLETED + structured Trace tree
```

第一次编辑把 `sum(values)` 改为平均值计算，但遗漏空列表；测试失败反馈进入 Agent history 后，第二次编辑补上空输入处理，再次测试通过。这验证的不是脚本能改文件，而是错误观察能驱动下一次 Agent 决策。

## 前面机制如何接入

| 机制 | 最终系统中的位置 |
| --- | --- |
| Model Adapter | `CodingDemoProvider` 实现统一 `ModelProvider`；可替换为真实 Provider |
| Agent Loop / Context | `Harness.create_agent_loop()` 组装 Tool feedback loop 与预算上下文 |
| State / Checkpoint | 每个完整 Step 保存；审批后从 pending Tool Call 继续 |
| Interrupt / Resume | `write_file` 与 `shell` 返回 WAITING，`AgentWorker.resume()` 继续 |
| Permission | CodingSkill 为写入和进程执行注册 `REQUIRE_APPROVAL` |
| Sandbox | 测试和 Git 命令都由 `LocalSandbox` 执行 |
| Plugin / Skill | Coding 能力通过 `CodingSkill.setup()` 注入 |
| MCP | `MCPClientAdapter` 发现并注册 `project_hint`，Agent Loop 无远端分支 |
| Task Runtime / Reliability | Scheduler、Worker、Retry 和 Tool 幂等贯穿同一 Task |
| Tracing | Task、Step、Model、Tool、审批状态与 Retry 使用同一 Trace identity |

## 幂等与“重新运行测试”

HARN-16 的 exact-call 幂等键由 `task_id + tool name + arguments` 组成。它能保护崩溃重放，却会把同一 Task 内有意重复的完全相同调用视为缓存命中。

Coding Demo 为两次测试传入不同的 `CODING_TEST_RUN` 环境标识：

```text
shell(command=same, environment={CODING_TEST_RUN: first})
shell(command=same, environment={CODING_TEST_RUN: second})
```

因此一次逻辑测试的崩溃重试仍可复用结果，编辑前后的两轮测试则会分别执行。生产系统通常应把 logical operation ID、Tool 的幂等语义和外部服务 idempotency key 设计得更明确。

## 运行 Demo

```bash
python -m examples.harn_18_mini_coding_agent
```

预期关键输出：

```text
Approve from Checkpoint: write_file
Approve from Checkpoint: shell
...
status=completed
test exit codes=[1, 0]
retries=1
```

Demo 使用临时目录和本地临时 Git 仓库，退出后自动清理，不改当前项目。

## 最值得阅读的代码

1. `harness/tools/filesystem.py`：工作区路径边界、原子写入和有界搜索；
2. `harness/skills/coding.py`：不增加新框架的最终能力组合；
3. `harness/skills/git.py` 的 `SandboxGitBackend`：Git 端口到 Sandbox 的适配；
4. `examples/harn_18_mini_coding_agent.py`：所有机制的完整调用链；
5. `tests/test_coding_agent.py`：文件安全边界和端到端系统规格。

## Trade-off

- Demo Provider 是确定性 LLM stand-in，用于让控制流可重复；接入真实模型不会改变 Harness 边界，但结果不再完全确定；
- `LocalSandbox` 不是容器，Shell 仍拥有宿主进程权限；
- 文件 Tool 使用同步文件 I/O，超大仓库应改为异步/远端工作区服务；
- 搜索是 Python 逐文件扫描，不具备 ripgrep、索引或语言服务器的性能与语义；
- 写入以整文件替换为单位，没有 patch、行级冲突或并发版本检查；
- Demo 自动批准危险动作只为展示 Resume；真实 Client 必须把审批交给人或外部策略系统；
- Git backend 只读，最终 Agent 不会自行 add/commit/push。

## 尚未解决的问题

- 没有 AST/LSP 感知编辑、patch Tool、代码格式化器编排或测试选择器；
- 没有 Docker/VM 级隔离、网络策略、资源配额或秘密管理；
- 内存 Task/Trace/幂等 Store 仍不支持多进程生产部署；
- 没有真实 MCP transport、远端工作区或生产 Observability exporter；
- 没有自动评测修复质量、测试充分性、安全性或成本；
- 这是一套教学用 Mini Harness，不保证任意仓库中的自主修复正确性。
