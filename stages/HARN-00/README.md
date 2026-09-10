# My-Harness

这是一个按照 `docs/agent_harness_progressive_learning_roadmap.md` 逐步构建 Agent Harness 的学习项目。

当前进度：**HARN-00 — 最小项目骨架**。

本阶段只包含：

- 模块化 Python 包结构；
- 环境配置读取；
- 标准日志初始化；
- 配置与日志共用的基础类型。

Agent Loop、模型调用、工具执行等能力会在后续阶段按路线图逐步加入。

## 运行 Demo

项目使用 Python 3.11 或更高版本。在项目根目录执行：

```bash
python -m examples.harn_00_project_skeleton
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

HARN-00 的架构说明见 `docs/stage_notes/HARN-00.md`。
