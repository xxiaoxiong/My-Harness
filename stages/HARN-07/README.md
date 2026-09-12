# My-Harness

这是一个按照 `docs/agent_harness_progressive_learning_roadmap.md` 逐步构建 Agent Harness 的学习项目。

当前进度：**HARN-07 — Context Compaction**。

## 按阶段独立学习

仓库根目录始终保存当前开发版本。每完成一个阶段，都会把当时完整、可运行的项目复制到独立快照目录；旧快照不再随新阶段修改。

| 阶段 | 独立代码 | 学习主题 |
| --- | --- | --- |
| HARN-00 | [`stages/HARN-00/`](stages/HARN-00/) | 最小项目骨架、配置、日志、基础类型 |
| HARN-01 | [`stages/HARN-01/`](stages/HARN-01/) | Model Adapter、OpenAI-compatible provider、调用观测 |
| HARN-02 | [`stages/HARN-02/`](stages/HARN-02/) | 最小 Agent Loop、决策反馈、`max_steps` 终止保护 |
| HARN-03 | [`stages/HARN-03/`](stages/HARN-03/) | calculator Tool、执行反馈、Result/Error 观察 |
| HARN-04 | [`stages/HARN-04/`](stages/HARN-04/) | Tool Schema、实现、Registry 与 Executor 分层 |
| HARN-05 | [`stages/HARN-05/`](stages/HARN-05/) | Agent State、状态转移与完整 Trajectory |
| HARN-06 | [`stages/HARN-06/`](stages/HARN-06/) | ContextBuilder、上下文组成与字符预算 |

例如，单独学习 HARN-06 时：

```powershell
cd stages/HARN-06
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
python -m examples.harn_06_context_engine
```

下一阶段将在根目录基于当前版本做最小演进；完成并验证、提交根目录代码后，运行以下命令生成新快照：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/create_stage_snapshot.ps1 HARN-07
```

完整约定见 [`docs/stage_workflow.md`](docs/stage_workflow.md)。

本阶段只包含：

- 模块化 Python 包结构；
- 环境配置读取；
- 标准日志初始化；
- 配置与日志共用的基础类型；
- 统一异步 `ModelProvider` 接口；
- OpenAI-compatible Chat Completions provider；
- request、response、token usage、latency 和 finish reason 观测；
- 由 `CONTINUE` / `FINISH` 驱动的最小 Agent Loop；
- 防止无限循环的 `max_steps` 硬限制；
- 明确区分正常完成和达到最大步数的运行结果；
- 硬编码的安全 `calculator` Tool；
- `Tool Call → Tool Result → Final Answer` Feedback Loop；
- Tool 的 `name`、`arguments`、`result` 和 `error` 数据边界；
- 分离的 `ToolSchema`、`Tool`、`ToolRegistry` 与 `ToolExecutor`；
- 通过注册发现实现，Agent Loop 不再依赖具体 calculator；
- 包含 task、goal、step、status、消息、工具活动和时间的 `AgentState`；
- 记录 Model Call、Tool Call、Tool Result 和终止事件的完整 trajectory；
- 独立组装 System、Goal、History、Tool Definitions 和 Current State 的 `ContextBuilder`；
- 基于 `max_context_chars` 的硬预算和简单截断元数据；
- `Old History → Summary + Recent History` 的 Context Compaction；
- 保留完整 State、并显式记录摘要范围与信息损失。

本阶段还没有持久化 Checkpoint；该能力会在下一阶段加入。

## 运行 Demo

项目使用 Python 3.11 或更高版本。先在项目根目录安装项目依赖：

```bash
python -m venv .venv
```

PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

macOS / Linux：

```bash
source .venv/bin/activate
python -m pip install -e .
```

随后执行：

```bash
python -m examples.harn_00_project_skeleton
python -m examples.harn_01_model_adapter
python -m examples.harn_02_minimal_agent_loop
python -m examples.harn_03_tool_agent
python -m examples.harn_04_tool_registry
python -m examples.harn_05_agent_state
python -m examples.harn_06_context_engine
python -m examples.harn_07_context_compaction
```

可通过环境变量观察配置生效：

```bash
HARNESS_ENV=production HARNESS_LOG_LEVEL=DEBUG python -m examples.harn_00_project_skeleton
```

PowerShell：

```powershell
$env:HARNESS_ENV = "production"
$env:HARNESS_LOG_LEVEL = "DEBUG"
python -m examples.harn_00_project_skeleton
```

## 运行测试

```bash
python -m unittest discover -s tests -v
```

各阶段的架构说明见 `docs/stage_notes/`，其中 HARN-07 的摘要压缩与信息损失见 [`docs/stage_notes/HARN-07.md`](docs/stage_notes/HARN-07.md)。
