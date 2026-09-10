# My-Harness

这是一个按照 `docs/agent_harness_progressive_learning_roadmap.md` 逐步构建 Agent Harness 的学习项目。

当前进度：**HARN-01 — Model Adapter**。

本阶段只包含：

- 模块化 Python 包结构；
- 环境配置读取；
- 标准日志初始化；
- 配置与日志共用的基础类型；
- 统一异步 `ModelProvider` 接口；
- OpenAI-compatible Chat Completions provider；
- request、response、token usage、latency 和 finish reason 观测。

Agent Loop、工具执行等能力会在后续阶段按路线图逐步加入。

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

各阶段的架构说明见 `docs/stage_notes/`。
