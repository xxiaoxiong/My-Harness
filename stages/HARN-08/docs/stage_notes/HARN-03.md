# HARN-03：加入第一个 Tool

## 本阶段解决的问题

HARN-02 的模型只能控制 `CONTINUE` 或 `FINISH`，无法对环境执行动作。循环虽然存在，却没有新的外部观察可以改变下一次推理。

HARN-03 加入唯一一个硬编码工具 `calculator`。模型先输出结构化 Tool Call，Harness 校验并执行算术表达式，再把包含 `name`、`arguments`、`result` 和 `error` 的 Tool Result 送回模型。模型基于真实计算结果生成最终答案，由此形成第一个完整 Feedback Loop。

## 新增机制

- `ToolCall`：保存模型选择的工具名称和参数对象。
- `ToolResult`：同时保留名称、原参数、成功结果和错误字段。
- `calculate()`：只支持数字、括号、`+ - * /` 与一元正负号的安全计算器。
- `execute_tool_call()`：用显式 `if call.name == "calculator"` 硬编码执行路径。
- `ToolAgentLoop`：解析模型动作、执行工具、追加观察并再次调用模型。
- `InvalidAgentAction`：阻止无效 JSON 或不符合协议的对象进入控制流。

模型使用当前阶段自己的最小文本 JSON 协议：

```json
{"type":"tool_call","name":"calculator","arguments":{"expression":"2 + 3 * 4"}}
```

工具执行后，Harness 追加：

```json
{"type":"tool_result","name":"calculator","arguments":{"expression":"2 + 3 * 4"},"result":14,"error":null}
```

模型准备结束时返回：

```json
{"type":"final_answer","answer":"结果是 14。"}
```

## 完整调用链

```text
User Goal
   ↓
ToolAgentLoop
   ↓
ModelProvider.generate()
   ↓
JSON Tool Call
   ↓
validate name + arguments
   ↓
execute_tool_call() ── hard-coded ──→ calculator
   ↓                                      │
Tool Result ←──────── result / error ─────┘
   ↓
append observation to messages
   ↓
ModelProvider.generate()
   ↓
JSON Final Answer
   ↓
ToolAgentRunResult
```

这正是：

```text
Reason（模型选择 calculator）
  → Act（Harness 执行表达式）
  → Observe（result 或 error 回到消息）
  → Reason（模型根据观察回答或修正）
```

## 最值得阅读的代码

1. `ToolAgentLoop.run()`：把模型动作和工具观察接成闭环。
2. `_parse_action()`：对模型 JSON 执行严格的类型与字段校验。
3. `execute_tool_call()`：HARN-03 故意保留的硬编码分发点。
4. `calculate()` / `_evaluate()`：通过 AST 白名单计算，而不是执行任意 Python。
5. `ToolResult`：同一个对象表达成功和可反馈给模型的失败。

## Tool 的四个必备信息

- `name`：模型请求了什么能力；当前唯一合法实现是 `calculator`。
- `arguments`：本次调用输入；calculator 要求只有字符串 `expression`。
- `result`：成功执行后产生的 JSON 兼容值。
- `error`：未知工具、参数错误或计算错误的可观察描述。

工具错误不会直接让 Agent 进程崩溃。`execute_tool_call()` 将预期内错误转换为 `ToolResult(error=...)`，模型下一轮可以观察并修正。协议错误则不同：无效模型动作无法安全解释，所以抛出 `InvalidAgentAction`。

## 为什么计算器不能直接使用 `eval()`

Tool arguments 来自模型，因此属于不可信输入。`eval()` 不只是计算数学表达式，还能访问 Python 名称、调用函数和触发其他副作用。

本阶段先用 `ast.parse(..., mode="eval")` 得到语法树，再递归接受数字、四则运算与一元正负号。函数调用、属性访问、变量、列表、幂运算等节点都会被拒绝，同时限制表达式长度和语法节点数量。

## 架构收益

Agent 第一次能通过外部执行结果改变后续推理。模型负责提出动作和解释观察，Harness 负责验证协议、控制调用次数并执行确定性代码；两者责任不混在同一段 Prompt 中。

`ToolCall` 与 `ToolResult` 还建立了清晰的数据边界。即使当前只有一个硬编码工具，测试也能分别检查模型动作解析、工具执行和观察反馈。

## Trade-off

- 文本 JSON 协议可以兼容 HARN-01 的纯文本 Model Adapter，但还不是厂商原生 tool-calling 协议。
- `execute_tool_call()` 直接判断工具名，添加第二个工具就会继续增加条件分支。
- 错误当前只是字符串，没有错误码或可重试分类。
- `ToolAgentLoop` 与 HARN-02 的最小 `AgentLoop` 并存，使两个阶段差异容易比较，但产生少量循环参数校验重复。
- 计算器只实现教学所需的基础算术，不追求完整数学语言。

## 尚未解决的问题

- 没有 Tool Definition、Tool Schema、Registry 或通用 Executor；留给 HARN-04。
- 没有正式 State、tool call 历史或 trajectory；留给 HARN-05。
- Loop 仍然自己拼装模型消息；留给 HARN-06。
- 没有审批、权限和副作用分级；留给 HARN-10。
- 没有 Tool 超时、并发控制、重试或结构化事件；留给后续运行时阶段。

## 最小 Demo

```bash
python -m examples.harn_03_tool_agent
```

Demo 使用离线的确定性 `ModelProvider`，可直接看到：

```text
User
→ LLM Tool Call
→ calculator execution
→ Tool Result
→ LLM Final Answer
```
