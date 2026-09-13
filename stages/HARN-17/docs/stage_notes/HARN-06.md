# HARN-06：Context Engine

## 本阶段解决的问题

HARN-05 已经区分 State、Context 和 Trajectory，但 `ToolAgentLoop` 仍然亲自创建 System Prompt、Goal、Tool Definitions、Tool Result 消息和 Current State 表达。控制流因此知道太多 Prompt 细节；任何上下文顺序或预算变化都必须修改 Loop。

HARN-06 引入独立 `ContextBuilder`。Loop 只在每次模型调用前把当前 `AgentState` 和 `ToolSchema` 交给 Builder，再使用返回的 `messages` 创建 `ModelRequest`。Prompt 组成、序列化和字符预算全部位于 `harness/context`。

## 新增机制

- `ContextBuilder`：构造某一次模型输入。
- `ContextBuildResult`：返回消息 tuple 以及预算元数据。
- `DEFAULT_TOOL_AGENT_SYSTEM_PROMPT`：由 Context 层拥有的工具动作协议。
- `max_context_chars`：第一个近似上下文预算，不依赖 tokenizer。
- `tool_result_message()`：统一把 Tool Result 转为模型可观察的 History。
- 简单 History 截断：优先保留最新消息，超出的旧消息不进入本次 Context。

## Context 的五个组成

```text
System Prompt
+ Goal
+ Relevant History
+ Tool Definitions
+ Current State
= ModelRequest.messages
```

当前消息顺序为：

```text
1. System Prompt       (developer)
2. Goal                (user)
3. selected History    (original roles)
4. Tool Definitions    (developer)
5. Current State       (developer)
```

Current State 是紧凑 JSON，只包含 `task_id`、`current_step`、`status`、Tool Call 数和 Tool Result 数。完整对象仍保存在 Runtime，不会复制进 Prompt。

## 完整调用链

```text
ToolAgentLoop
      ↓
state.begin_model_step()
      ↓
ContextBuilder.build(state, tool_schemas)
      │
      ├── System Prompt
      ├── Goal
      ├── bounded History
      ├── Tool Definitions
      └── Current State
      ↓
ContextBuildResult
      ↓
ModelRequest(context.messages)
      ↓
ModelProvider.generate()
      ↓
response updates full AgentState
```

Agent Loop 不再包含 Prompt 字符串、Tool Schema JSON 或 Tool Result JSON 的构造代码。

## 字符预算算法

本阶段还没有 tokenizer，因此预算定义为：

```python
sum(len(message.content) for message in context.messages)
```

构造过程：

1. 先计算 System、Goal、Tool Definitions 和 Current State 四个固定组成。
2. 如果固定组成未超限，剩余字符用于 History。
3. 从最新 History 向前选择完整消息；最后一个放不下的消息只保留尾部并加 `[truncated]` 标记。
4. 更旧消息不进入本次 Context，并计入 `dropped_messages`。
5. 如果固定组成自身已超限，则不加入 History，并按均衡字符额度截短四个固定消息。
6. 返回的 `total_chars` 始终不大于 `max_context_chars`。

这个策略故意简单。它能建立明确硬边界，但被截掉的旧信息完全丢失于 Context。HARN-07 将用 Summary 改善这一点。

## ContextBuildResult 可观察字段

- `messages`：不可变 tuple，可直接放入 `ModelRequest`。
- `total_chars`：实际模型输入正文字符数。
- `max_context_chars`：本次使用的硬上限。
- `truncated`：是否发生过 History 丢弃或固定组成截短。
- `dropped_messages`：完全未包含的 State History 消息数量。

这些字段让测试和上层调用方可以知道预算是否生效，而无需反向猜测 Prompt。

## 最值得阅读的代码

1. `ContextBuilder.build()`：五种输入组成与预算分配的唯一入口。
2. `_latest_history()`：从 State 选择本次 Context History，但不修改 State。
3. `_fit_fixed_messages()`：极小预算下仍保证硬字符上限。
4. `ContextBuilder.tool_result_message()`：工具观察的模型表示归属 Context 层。
5. `ToolAgentLoop.run()`：现在只消费 ContextBuildResult，不再拼 Prompt。

## 为什么 Context Management 必须独立于 Agent Loop

Loop 的职责是决定何时调用模型、何时执行工具和何时停止；Context 的职责是决定模型这一次能看到哪些信息。两者变化原因不同：增加停止条件不应修改 Prompt 预算，改变历史选择也不应重写控制流。

独立后，同一个 `AgentState` 可以被不同 ContextBuilder 用不同 System Prompt 或预算投影成不同模型输入，而状态事实保持不变。测试也可以在不运行模型或工具的情况下单独验证上下文边界。

## State 没有因截断而变化

Builder 只读取 `state.messages` 并创建新的 tuple。即使 `ContextBuildResult.dropped_messages > 0`：

- `AgentState.messages` 仍保留完整历史；
- `AgentState.tool_calls`、`tool_results` 和 trajectory 不变；
- 下一次可以用更大预算重新构建不同 Context。

这进一步证明 `Context ≠ State`。

## Trade-off

- 字符数只是 token 使用量的粗略替代，不同语言和模型的误差不同。
- 简单截断没有摘要，被丢弃的旧信息无法被模型利用。
- 部分消息截断可能切断 JSON 或自然语言结构。
- 极小预算会连 Tool Definitions 等固定组成一起截短，使模型输入退化但仍满足硬上限。
- 当前每次模型调用都重新序列化 Tool Schema 和 State，尚未缓存。

## 尚未解决的问题

- 没有把 Old History 总结后与 Recent History 组合；留给 HARN-07。
- 没有 tokenizer 或模型专属 token budget。
- Context metadata 尚未进入 Checkpoint；留给 HARN-08。
- 没有长期 Memory、RAG 或语义相关性选择；留给后续 Memory 阶段。
- 没有失败恢复、Hook 或结构化指标；留给后续运行时阶段。

## 最小 Demo

```bash
python -m examples.harn_06_context_engine
```

Demo 构造 3,000 多字符的 State History，再用约 900 字符预算生成 Context。输出显示 State 仍有 3 条完整历史，而 Context 只保留 1 条截短的最新消息，`total_chars` 精确等于预算。
