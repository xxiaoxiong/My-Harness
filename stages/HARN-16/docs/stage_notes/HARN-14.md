# HARN-14：MCP Adapter

## 本阶段解决的问题

HARN-13 的 Tool 全部由本地 Python 实现。Agent Loop 认识统一的 Tool Schema 和 Tool Result，却还无法把 MCP Server 动态暴露的能力接入同一路径。

HARN-14 增加传输无关的 MCP Client 端口和适配层：

```text
Agent
  → Context 中的 Tool Definitions
  → ToolRegistry
  → ToolExecutor
  ├─ Local Tool implementation
  └─ MCPToolAdapter
       → MCPClientAdapter
         → MCPClient
           → MCP Server / external system
```

Agent Loop、Policy、Hook、State 和 ContextBuilder 都不增加 `if tool_is_mcp` 分支。

## 三个概念的关系

### Function Calling

Function Calling 是模型输入/输出协议：Harness 给模型可调用能力的名称和参数 Schema，模型返回“我想调用哪个能力以及参数”。它表达意图，不负责执行代码，也不决定权限。

### Tool

Tool 是 Harness 内的运行时抽象：

```text
ToolSchema + async execute(arguments) → JSON value
```

calculator、ShellTool 和 MCPToolAdapter 对 Agent Loop 来说都是 Tool。

### MCP

MCP 是客户端与外部 Server 之间发现和调用能力的协议边界。它解决跨进程、跨服务的互操作，不替代模型 Function Calling，也不替代 Harness 的 Tool 执行、Policy 或 Sandbox。

```text
Function Calling：模型怎样选择动作
Tool：Harness 怎样表示和执行动作
MCP：远程能力怎样被发现和调用
```

## MCP Client 端口

`MCPClient` 只暴露适配层需要的两个异步操作：

```python
await client.list_tools()                 # → MCPToolDefinition[]
await client.call_tool(name, arguments)  # → MCPCallResult
```

具体实现可以包装 MCP SDK 的 stdio、HTTP 或其他 transport。Harness 核心不依赖某个 SDK，也不接触 session、JSON-RPC 或连接生命周期。

`MCPToolDefinition` 保存远端名称、可选 description 和 input schema；`MCPCallResult` 把成功 content 与 `is_error` 归一化。

## MCPClientAdapter

`MCPClientAdapter` 负责两件事：

1. 调用 Client 发现 Tool Definition，并为每项创建 `MCPToolAdapter`；
2. 校验远端返回值，再把调用交给 Client transport。

发现结果必须是 `MCPToolDefinition` 序列，且一个 Server 内的名称不能重复。批量注册前还会预检本地 Registry 名称冲突，避免注册到一半才失败。

```python
adapter = MCPClientAdapter(client, server_name="filesystem-server")
remote_tools = await adapter.register_tools(registry)
```

若使用 HARN-12 的 Harness 组合根，也可先发现、再逐个注册：

```python
for tool in await adapter.discover_tools():
    harness.register_tool(tool)
```

## MCPToolAdapter

每个远端定义被包装成一个普通 `Tool`：

```text
MCP name         → ToolSchema.name
MCP description  → ToolSchema.description
MCP inputSchema  → ToolSchema.parameters
MCP call result  → Tool.execute result
```

远端 `is_error=True` 和 `MCPClientError` 被转换成 `MCPToolError`，因此 ToolExecutor 会把它们变成普通的 Tool Result error 并反馈给模型，和本地 Tool 的可恢复错误行为一致。

## Agent Loop 为什么无需改动

Runtime 只依赖：

```text
executor.list_schemas()
executor.execute(ToolCall)
```

MCPToolAdapter 已经满足相同 Tool 接口。远端来源信息只保留在 Adapter 上用于诊断，不渗入 Agent 的控制流。这是适配器模式的关键收益：变化发生在系统边缘，而不是循环中心。

## 最小 Demo

```bash
python -m examples.harn_14_mcp_adapter
```

Demo 使用确定性的 `DemoMCPClient` 模拟一个已连接的 MCP Server，动态发现 `remote_uppercase`。同一个 Harness 同时注册本地 `CalculatorTool` 和远端 `MCPToolAdapter`，Agent 依次调用二者并获得一致形状的 Tool observation。Demo 不需要网络或外部服务。

## 最值得阅读的代码

1. `MCPClient`：与具体 MCP SDK/transport 解耦的端口。
2. `MCPToolDefinition` / `MCPCallResult`：远端数据归一化。
3. `MCPClientAdapter.discover_tools()`：发现与协议校验。
4. `MCPToolAdapter.execute()`：远端调用到本地 Tool 语义的转换。
5. `tests/test_mcp_adapter.py`：本地/远端统一执行、错误与冲突规格。
6. `examples/harn_14_mcp_adapter.py`：同一 Agent Loop 混合调用演示。

## Trade-off

- 本阶段定义 SDK-neutral client port，不绑定或实现某一种 MCP transport。
- Tool discovery 是显式快照，没有自动刷新、Server capability change 通知或连接重试。
- MCP Server 和本地 Tool 共享平面名称；冲突会拒绝注册，尚未提供命名空间。
- MCP content 被视为 JSON-compatible 数据，尚未细分 text、image、resource 等 content block。
- 批量发现与注册不是 HARN-12 同步 Plugin setup 的一部分，因为远端发现是异步操作。

## 尚未解决的问题

- MCP SDK session、stdio/HTTP transport、认证与连接生命周期尚未实现。
- 多 Server 命名空间、断线重连、Schema 更新与 capability cache 尚未实现。
- MCP Tool 仍会经过 Policy，但尚未增加按 Server、资源 URI 或远端身份的细粒度规则。
- 更完整的 Agent Runtime 能力留给后续阶段。
