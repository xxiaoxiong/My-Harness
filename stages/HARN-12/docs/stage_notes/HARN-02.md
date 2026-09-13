# HARN-02：最小 Agent Loop

## 本阶段解决的问题

HARN-01 的调用链在收到一次 `ModelResponse` 后立即结束。无论 Prompt 写得多复杂，只要程序没有读取模型输出并据此决定下一步，它仍然只是一次函数调用，不是循环运行的 Agent。

HARN-02 加入第一个控制循环：给定 Goal 后，模型只能选择 `CONTINUE` 或 `FINISH`。`CONTINUE` 会成为下一次请求的一部分，`FINISH` 则终止运行。程序同时维护当前步数，并以 `max_steps` 设置硬上限。

本阶段仍然没有 Tool。它展示的是 Agent 最小的“观察结果—更新运行上下文—再次决策”骨架，而不是一个已经能完成现实任务的 Agent。

## 新增机制

- `AgentLoop`：协调 Goal、模型调用、反馈和终止条件。
- `AgentDecision`：把模型允许返回的值限制为 `CONTINUE` / `FINISH`。
- `AgentRunStatus`：区分正常完成与达到最大步数。
- `AgentRunResult`：返回规范化目标、停止原因、执行步数和最后决策。
- `InvalidAgentDecision`：拒绝解释文字、标点或其他未定义输出。
- `max_steps`：保证每次运行最多调用模型固定次数。

## 完整调用链

```text
Goal + max_steps
       ↓
   AgentLoop.run()
       ↓
build ModelRequest ───────────────┐
       ↓                          │
ModelProvider.generate()          │
       ↓                          │
 ModelResponse                    │
       ↓                          │
parse exact decision              │
       ↓                          │
  ┌────┴───────────────┐          │
  │                    │          │
FINISH              CONTINUE      │
  │                    │          │
  ↓                    ↓          │
FINISHED       append decision +  │
result          next-step prompt  │
                       │          │
                       ├── step < max_steps ──┘
                       │
                       └── step == max_steps
                                   ↓
                         MAX_STEPS_REACHED
                                result
```

循环中暂时存在的最小状态是消息列表和当前步数。第一次请求携带 Goal；模型返回 `CONTINUE` 后，该决策和下一步编号被追加到消息列表，下一次模型调用因此能看到此前进度。

## 最值得阅读的代码

1. `AgentLoop.run()`：HARN-02 的完整闭环和两个退出路径。
2. `AgentLoop._parse_decision()`：不可信模型文本进入控制流之前的校验边界。
3. `AgentDecision`：把自由文本压缩为两个合法控制信号。
4. `AgentRunResult`：调用方无需检查 Prompt 或原始响应即可知道运行结果。
5. `SequenceProvider` 测试替身：不依赖网络即可精确验证每一步发给模型的消息。

## 为什么它开始具备 Agent 属性

普通 `Prompt → LLM` 是无反馈的一次映射：模型响应不会改变程序接下来做什么。

HARN-02 中，模型输出会改变控制流，并被写入下一轮可见的运行上下文；程序还会跨调用维护步数。于是结果不再由调用前的固定流程完全决定，而是由模型在运行中的决策推动。这已经具备最小的 Agent 属性：目标导向、连续决策和可终止循环。

但“具备 Agent 属性”不等于“已经有用”。因为当前输出只能控制继续或结束，模型既不能执行外部动作，也不能保存结构化工作成果。HARN-03 才会加入第一个 Tool。

## `max_steps` 为什么必须是硬限制

模型输出是不可信的外部输入。它可能持续返回 `CONTINUE`，也可能因为目标含糊而永远无法判断完成。如果只依赖模型自觉返回 `FINISH`，调用次数、延迟和费用都没有上界。

`max_steps` 在进入模型前验证为正整数，并直接控制 `for` 循环的最大迭代次数。即使模型始终要求继续，结果也会以 `MAX_STEPS_REACHED` 返回，且模型调用次数绝不会超过上限。

## Trade-off

- 严格决策协议容易理解，但模型返回 `FINISH.` 或解释文字时会直接失败。
- 每轮携带已有消息，使模型能看到反馈，但上下文会随步数增长。
- 当前只保存运行中的临时消息和步数，没有可恢复、可序列化的正式 Agent State。
- 达到 `max_steps` 被建模为一个正常停止状态，而不是异常；调用方必须显式判断它是否符合业务预期。

## 尚未解决的问题

- 没有 Tool，`CONTINUE` 还不能触发现实世界动作；留给 HARN-03。
- 没有 Tool Router 或未知工具错误；留给 HARN-04。
- 没有正式的 `AgentState`、trajectory 和状态转移；留给 HARN-05。
- Prompt 仍由循环内部直接拼装；留给 HARN-06。
- 没有上下文窗口控制；留给 HARN-07。
- 没有重试、预算控制或结构化可观测事件；留给后续阶段。

## 最小 Demo

```bash
python -m examples.harn_02_minimal_agent_loop
```

Demo 使用一个确定性的 `ModelProvider`，依次返回 `CONTINUE` 和 `FINISH`。因此不需要 API key 或网络，输出可以直接观察两次模型调用和最终停止状态。
