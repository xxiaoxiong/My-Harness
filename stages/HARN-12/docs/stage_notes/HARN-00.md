# HARN-00：建立最小项目骨架

## 本阶段解决的问题

路线图开始时只有设计文档，还没有可导入、可运行、可测试的代码边界。HARN-00 先建立稳定的工程形状，让后续每个机制都能进入自己的模块，而不必不断拆解一个不断膨胀的入口函数。

本阶段刻意不实现 Agent、模型适配、工具调用或运行时逻辑。

## 新增机制

- `harness/core/`：当前唯一包含实现的包，提供配置、日志和共享基础类型。
- `harness/model/`、`tools/`、`context/`、`state/`、`runtime/`：仅声明未来模块边界，不包含行为。
- `examples/`：保存每个阶段可以直接运行的最小示例。
- `tests/`：验证当前阶段的行为边界。
- `pyproject.toml`：声明 Python 版本、包发现和测试配置。

配置只读取两个进程环境变量：

- `HARNESS_ENV`：`development`、`test` 或 `production`；
- `HARNESS_LOG_LEVEL`：Python 标准日志等级。

## 调用链

```text
Process Environment
        ↓
   load_config()
        ↓
  HarnessConfig
        ↓
configure_logging()
        ↓
  harness.* Logger
```

这条调用链只是应用启动过程，不是 Agent Loop。

## 核心代码

1. `HarnessConfig`：不可变的进程级配置对象，避免配置在运行中被意外修改。
2. `load_config()`：环境变量与 Python 对象之间的唯一转换边界。
3. `configure_logging()`：只配置项目拥有的 `harness` logger，并保证重复调用不会重复输出。
4. `get_logger()`：为不同模块创建 `harness.<component>` 命名空间 logger。
5. `Environment` / `LogLevel`：用枚举约束配置中的基础值，尽早拒绝拼写错误。

## 架构收益

Harness 应该模块化，因为模型、上下文、工具、状态和运行时具有不同的变化原因，也需要彼此独立地测试和替换。清晰的包边界使依赖方向可见：未来的功能模块可以依赖 `core`，但 `core` 不需要知道任何具体 Agent 能力。

如果把所有逻辑都写入 `run_agent()`，配置读取、模型调用、循环控制、工具执行和错误处理会共享局部状态并互相耦合。这样既难以单独测试，也难以在替换某一个机制时确认影响范围。HARN-00 先固定边界，后续阶段再逐个填入行为。

## Trade-off

- 当前存在多个几乎为空的包，代码量看起来比单文件更大。
- 环境值使用枚举约束，增加了少量转换代码，但错误会在启动时暴露。
- 日志配置只负责控制台文本输出；更丰富的结构化事件需要后续可观测性阶段处理。

这些成本是有意承担的：目录只表达已经由路线图确认的边界，没有预先设计后续接口。

## 暂时没有解决的问题

- 没有模型接口或网络请求；留给 HARN-01。
- 没有 Agent Loop、目标或终止判断；留给 HARN-02。
- 没有工具系统；留给 HARN-03 和 HARN-04。
- 没有 Agent State、上下文或持久化；留给 HARN-05 及以后阶段。
- 没有结构化事件与指标系统；留给 HARN-17。

## 最小 Demo

在项目根目录执行：

```bash
python -m examples.harn_00_project_skeleton
```

预期输出类似：

```text
2026-09-09T01:23:45Z INFO harness: HARN-00 skeleton is ready (environment=development, log_level=INFO)
```

