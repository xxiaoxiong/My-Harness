# HARN-04：Tool Registry

## 本阶段解决的问题

HARN-03 已经有完整 Tool Feedback Loop，但 `execute_tool_call()` 直接写死 `calculator` 名称、参数校验和函数调用。每增加一个工具，都必须修改同一个分发函数；Agent 的可用工具说明也写在固定 Prompt 中。

HARN-04 将“工具是什么”“工具怎样工作”“去哪里找到工具”“怎样执行一次调用”拆成四个独立职责：

```text
ToolSchema   → Tool Definition
Tool         → Tool Implementation contract
ToolRegistry → Tool discovery
ToolExecutor → Tool execution
```

`ToolAgentLoop` 只依赖 `ToolExecutor` 暴露的 definitions 和执行入口，不再导入 `CalculatorTool`，也不再判断任何具体工具名。

## 新增机制

- `ToolSchema`：名称、描述和参数 JSON Schema，是给模型看的定义数据。
- `Tool`：实现必须提供 `schema` 和异步 `execute(arguments)`。
- `CalculatorTool`：calculator 的具体实现，负责自己的参数校验和计算。
- `ToolRegistry`：支持 `register(tool)`、`get(name)`、`list_tools()`。
- `ToolExecutor`：通过 Registry 查找实现、执行调用并转换预期内错误。
- `DuplicateToolError`：避免同名实现导致不确定分发。
- `ToolNotFoundError`：把未知工具与工具自身执行失败区分开。
- `ToolError`：允许实现声明可安全反馈给模型的预期失败。

## 完整调用链

```text
Application startup
       │
       ├── CalculatorTool ── schema + execute()
       │          ↓
       └── registry.register(tool)
                  ↓
            ToolRegistry
                  ↓ list schemas
            ToolExecutor
                  ↓
            ToolAgentLoop
                  ↓
         model sees ToolSchema
                  ↓
              ToolCall
                  ↓
        ToolExecutor.execute(call)
                  ↓
        registry.get(call.name)
                  ↓
         selected Tool.execute()
                  ↓
          result or ToolError
                  ↓
             ToolResult
                  ↓
         model observes and answers
```

## 三个 Registry 操作

```python
registry.register(tool)
registry.get("calculator")
registry.list_tools()
```

- `register()` 使用 `tool.schema.name` 建立索引，并拒绝重复名称。
- `get()` 返回具体实现；找不到时抛出独立的 `ToolNotFoundError`。
- `list_tools()` 按注册顺序返回不可变 tuple，使生成 Prompt 和测试结果稳定。

Registry 不执行工具，Executor 也不拥有工具定义。两者通过最小接口协作。

## 最值得阅读的代码

1. `ToolSchema` 与 `Tool`：Definition 数据和 Implementation 行为的分界线。
2. `ToolRegistry.register()`：工具实现进入运行时发现系统的唯一入口。
3. `ToolRegistry.get()`：字符串名称到实现对象的解析边界。
4. `ToolExecutor.execute()`：查找、异步调用和预期错误归一化。
5. `_build_instruction()`：Agent 只读取 schema，不接触具体实现。

## Definition、Implementation、Execution 为什么不同

`ToolSchema` 是可序列化说明：模型用它决定工具名与参数，但它本身不能做任何事情。

`CalculatorTool` 是实现：它理解 calculator 参数并完成计算，但不决定何时被调用，也不负责按字符串名称查找其他工具。

`ToolExecutor` 是一次调用的控制边界：它从 Registry 获取实现，等待执行结果，并把未知工具或 `ToolError` 转换为统一 `ToolResult`。

如果把三者放在一个类里，改变 Prompt 描述、替换实现、改变错误策略或增加执行控制都会修改同一个模块。分层后这些变化拥有清晰落点。

## 错误边界

- 重复注册是应用装配错误，`register()` 立即抛出 `DuplicateToolError`。
- 直接查询不存在的工具会抛出 `ToolNotFoundError`。
- Agent 请求未知工具时，Executor 把查找错误变成 `ToolResult.error`，让模型观察。
- Calculator 参数或算术错误以 `ToolError` 子类表示，也转换成观察结果。
- 未声明的实现异常不会被静默吞掉；当前阶段让它向上传播，以免把程序缺陷伪装成普通工具错误。

## 架构收益

增加新工具时只需实现 `Tool` 并在应用启动时注册，无需修改 Agent Loop 或 Executor。模型看到的可用工具也直接来自 Registry 中实现的 schema，因此定义和实际可执行集合保持一致。

异步 `Tool.execute()` 为以后接入网络或文件工具保留自然接口；当前 calculator 虽然立即返回，也遵守同一个执行契约。

## Trade-off

- 与单个 `if` 相比，四层结构增加了类型和装配代码。
- Registry 当前只存在于进程内，没有插件发现、版本或命名空间。
- `ToolSchema.parameters` 只是 JSON-compatible 数据，本阶段不引入完整 JSON Schema 校验器。
- Executor 只区分预期 `ToolError` 和其他异常，还没有超时、重试、权限或并发控制。
- Agent Loop 在创建时拍摄 schema；运行期间再注册工具不会自动刷新该实例的 Prompt。

## 尚未解决的问题

- 没有正式 Agent State、tool call/result 历史和 trajectory；留给 HARN-05。
- Agent Loop 仍负责拼装 Prompt；留给 HARN-06。
- 没有 Checkpoint、Interrupt/Resume 或权限审批；留给 HARN-08/HARN-09。
- 没有 Hook、并发限制、超时和失败隔离；留给后续控制与运行时阶段。
- 没有 Plugin 或 MCP 动态发现；留给 HARN-11/HARN-12。

## 最小 Demo

```bash
python -m examples.harn_04_tool_registry
```

Demo 依次打印 Tool Definition、CalculatorTool 实现、Registry 内容、模型选择的 Tool Call、Executor 观察和最终答案，便于直接对照四层职责。
