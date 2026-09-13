# HARN-07：Context Compaction

## 本阶段解决的问题

HARN-06 能严格限制 Context 字符数，但超限时只是删除旧 History。模型可以继续看到最新消息，却完全失去更早发生过什么。

HARN-07 在相同硬预算内增加 Compaction：旧 History 先经过 `HistorySummarizer`，得到一个有界 Summary；最新消息继续以原始角色和内容保留。完整历史仍留在 `AgentState.messages`，只有某一次发给模型的 Context 被压缩。

## 新增机制

- `HistorySummarizer`：把一组旧消息压缩到给定字符上限的抽象边界。
- `SimpleHistorySummarizer`：无需额外模型调用的确定性教学实现。
- `recent_history_messages`：最多保留多少条最新原始消息。
- `summary_max_chars`：Summary 正文的最大字符数。
- `ContextBuildResult.summary`：实际放入 Context 的 Summary 正文。
- `ContextBuildResult.summarized_messages`：被 Summary 代表的 State 消息数量。
- `[summary truncated]`：明确标记机械摘要本身也发生了截断。

## Compaction 后的 Context

```text
System Prompt
+ Goal
+ History Summary
+ Recent History
+ Tool Definitions
+ Current State
= bounded Model Input
```

只有完整 History 超过剩余字符预算时才调用 Summarizer；短会话仍直接使用原始消息，不产生无意义 Summary。

## 完整调用链

```text
Full AgentState.messages
          ↓
ContextBuilder detects overflow
          ↓
split by recent_history_messages
          │
          ├── Old History ──→ HistorySummarizer ──→ bounded Summary
          │
          └── Recent History ────────────────→ original messages
                                                   │
System + Goal + Summary + Recent + Tools + State ─┘
          ↓
ContextBuildResult (within max_context_chars)
          ↓
ModelRequest

Full AgentState.messages ── unchanged
```

## 预算分配

1. System、Goal、Tool Definitions 和 Current State 仍是固定组成。
2. 固定组成之外的可用字符中，最多三分之一作为 Summary slot，并受 `summary_max_chars` 限制。
3. 剩余字符用于最多 `recent_history_messages` 条最新消息。
4. 若最新消息仍放不下，边界消息只保留尾部；该完整原消息也进入 Summary 输入，避免其前半段完全没有代表。
5. Summary 输出不得超过 Builder 给出的 `max_chars`。
6. 所有 Context message 正文之和仍不超过 `max_context_chars`。

`dropped_messages` 在本阶段表示“不再以完整原消息出现”的数量；当 Summary 存在时，这些消息并非从 State 删除，而是由有损摘要代表。

## SimpleHistorySummarizer 做了什么

教学实现把每条旧消息变成：

```text
role: normalized single-line content
```

再以 ` | ` 连接。如果仍超出 Summary 上限，保留开头并追加 `[summary truncated]`。它不调用 LLM，不假装理解语义，也因此非常容易测试。

这个实现故意暴露信息损失：越靠后的旧内容可能消失，措辞与结构会被压平。真正系统可以替换为模型摘要器，但同样必须承认摘要不是原始事实。

## Tool Result 保留

Tool Call 和紧随其后的 Tool Result 通常是最新两条 History。设置 `recent_history_messages=2` 时，它们优先以原始 JSON 保留，使下一次模型调用仍能读取精确 `result` 或 `error`；更早的工具轮次进入 Summary。

测试显式验证长历史压缩后最新 `"type":"tool_result"` 仍存在，而 `AgentState.messages` 数量没有变化。

## 最值得阅读的代码

1. `HistorySummarizer.summarize()`：摘要实现与 Context Builder 的最小契约。
2. `SimpleHistorySummarizer`：可重复、可观察的有损压缩基线。
3. `ContextBuilder.build()`：只在完整 History 超限时进入 Compaction 分支。
4. `_compact_history()`：Summary slot 与 Recent History 预算分配。
5. `_recent_history_for_compaction()`：保留最新消息并确定 Summary 输入边界。

## State 没有丢

Context Builder 从不删除或替换 `state.messages`。长会话 Demo 最终拥有：

```text
State messages: 9
Tool calls: 4
Tool results: 4
Trajectory events: 14
```

后两次模型请求已经出现 Summary，但这些 State 数字仍完整。这是 `Context ≠ State` 最直接的运行证据。

## Summary 的信息损失

Summary 只能是原始 History 的派生视图：

- 机械截断会丢失后部细节；
- 模型摘要可能遗漏、误解甚至改写事实；
- 多次递归总结会累积误差；
- 精确 Tool Result 不应只依赖自然语言 Summary 保存。

因此 State 和 Trajectory 必须保留原始事实；Summary 只能服务某次模型输入，不能反过来覆盖系统状态。

## Trade-off

- 预留 Summary slot 会让某些 Context 未完全用满预算。
- 当前按消息新旧划分，不做语义相关性检索。
- 极长单条最近消息可能同时被摘要并保留尾部，产生少量重复。
- 确定性 Summarizer 便于学习和测试，但压缩质量有限。
- ContextBuildResult 记录压缩元数据，但 AgentState 还没有保存每次 Context metadata。

## 尚未解决的问题

- State、Trajectory、Summary metadata 还不能持久化；留给 HARN-08 Checkpoint。
- 没有 Interrupt/Resume；留给 HARN-09。
- 没有模型式摘要器、tokenizer 或语义检索。
- 没有 Hook、结构化 trace/metric 和失败恢复；留给后续阶段。

## 最小 Demo

```bash
python -m examples.harn_07_context_compaction
```

Demo 连续执行四次 calculator Tool Call 后再回答。前几次 Context 使用完整 History，后两次出现 Summary；每次输入都不超过 1300 字符，而最终 State 和 trajectory 保持完整。
