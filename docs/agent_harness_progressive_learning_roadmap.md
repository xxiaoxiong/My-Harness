# Agent Harness 渐进式构建学习路线

## 0. 项目目标

这个项目不是为了尽快开发一个功能完整的 Agent Framework。

目标是：

> 从零开始，逐步构建一个真正的 Agent Harness / Runtime，并通过每一次架构演进理解 Agent Systems 的核心机制。

最终希望理解并亲手实现：

```text
Agent
│
├── Agent Loop
├── Model Adapter
├── Context Engine
├── Tool System
├── State
├── Checkpoint
├── Interrupt / Resume
├── Policy / Permission
├── Hook
├── Skill / Plugin
├── Sandbox
├── MCP
├── Runtime
├── Task Queue
├── Scheduler
├── Reliability
└── Observability
```

最终架构大致演进为：

```text
User / API / CLI
        │
        ▼
┌───────────────────────┐
│     Agent Harness     │
│                       │
│ Agent Loop            │
│ Context Engine        │
│ Tool Registry         │
│ Skill / Plugin        │
│ Hook / Policy         │
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐
│     Agent Runtime     │
│                       │
│ Task                  │
│ State Machine         │
│ Checkpoint            │
│ Scheduler             │
│ Worker                │
│ Interrupt / Resume    │
│ Failure Recovery      │
└───────────┬───────────┘
            │
     ┌──────┴──────┐
     ▼             ▼
 Sandbox        Model Gateway
 Tool/MCP          │
                   ▼
               LLM / vLLM
```

---

# 一、全局开发规则

Claude Code / Codex 必须遵循以下规则。

## 1. 严格渐进开发

每次只实现当前任务。

禁止提前实现后续阶段功能。

例如 HARN-03 只要求 Tool Calling 时：

不要顺便实现：

- Plugin
- MCP
- Memory
- Scheduler
- Sandbox
- Multi-Agent

即使认为未来需要，也只允许留下接口思路或 TODO。

---

## 2. 前期禁止大型 Agent Framework

HARN-00 ～ HARN-12 阶段禁止直接使用：

- LangChain
- LangGraph
- DeepAgents
- AutoGen
- CrewAI

目的是理解 Agent Harness 自身。

可以使用：

- Python
- asyncio
- Pydantic
- httpx
- FastAPI（后期）
- pytest

模型接口优先设计为：

```text
OpenAI-compatible API
```

方便连接：

- OpenAI
- DeepSeek
- GLM
- Qwen
- LiteLLM
- vLLM

---

## 3. 每阶段必须解释“为什么”

每完成一个任务，都更新：

```text
docs/stage_notes/HARN-XX.md
```

内容必须包括：

### 本阶段解决的问题

之前的架构哪里不够？

### 新增机制

增加了什么？

### 调用链

用 ASCII 图表示，例如：

```text
User
 ↓
AgentLoop
 ↓
Model
 ↓
Tool
 ↓
AgentLoop
```

### 核心代码

指出最值得阅读的 3～5 个类 / 函数。

### 架构收益

为什么比上一阶段更好？

### Trade-off

新架构带来了什么复杂度？

### 暂时没有解决的问题

这些问题留给之后哪一个阶段？

---

## 4. 每阶段必须提供最小 Demo

例如：

```bash
python examples/harn_03_tool_agent.py
```

让我能够直接看到：

```text
User
↓
LLM
↓
Tool Call
↓
Tool Result
↓
LLM
↓
Final Answer
```

---

## 5. 每阶段必须有测试

至少覆盖当前新增核心机制。

重点不是测试数量，而是体现系统边界。

---

# 二、第一阶段：把 LLM 变成 Agent

---

# HARN-00：建立最小项目骨架

## 目标

只建立代码结构，不实现 Agent。

建议：

```text
agent_harness/
│
├── harness/
│   ├── core/
│   ├── model/
│   ├── tools/
│   ├── context/
│   ├── state/
│   └── runtime/
│
├── examples/
├── tests/
└── docs/
```

当前只允许真正实现：

```text
config
logging
基础类型
```

## 必须理解

为什么 Harness 应该模块化？

为什么不能把所有逻辑写进：

```python
run_agent()
```

---

# HARN-01：Model Adapter

## 目标

先不要做 Agent。

只完成：

```text
User
 ↓
ModelAdapter
 ↓
LLM
 ↓
Response
```

定义统一模型接口：

```python
class ModelProvider:
    async def generate(...)
```

实现一个 OpenAI-compatible provider。

## 学习重点

理解：

```text
Harness
≠
LLM SDK
```

Harness 不应该直接绑定：

```text
OpenAI
Claude
DeepSeek
Qwen
```

而应该面向 Model Adapter。

## 必须观察

模型调用的：

- request
- response
- token usage
- latency
- finish reason

---

# HARN-02：最小 Agent Loop

## 目标

第一次真正构建 Agent。

暂时没有 Tool。

模型只能返回：

```text
CONTINUE
FINISH
```

构建：

```text
Goal
 ↓
Agent Loop
 ↓
LLM
 ↓
Decision
 ↓
Continue / Finish
```

必须设置：

```text
max_steps
```

防止无限循环。

## 核心问题

为什么普通：

```text
Prompt → LLM
```

不是 Agent？

为什么：

```text
LLM → State → LLM
```

开始具备 Agent 属性？

---

# HARN-03：加入第一个 Tool

## 目标

增加一个最简单 Tool：

```text
calculator
```

执行链：

```text
User
 ↓
LLM
 ↓
Tool Call
 ↓
Tool Executor
 ↓
Tool Result
 ↓
LLM
 ↓
Answer
```

暂时不要 Tool Registry。

Tool 可以硬编码。

## 学习重点

理解 Agent 最核心的 Feedback Loop：

```text
Reason
→ Act
→ Observe
→ Reason
```

## 必须处理

Tool：

- name
- arguments
- result
- error

---

# HARN-04：Tool Registry

## 目标

解决 HARN-03 的硬编码问题。

设计：

```text
Tool
ToolSchema
ToolRegistry
ToolExecutor
```

支持：

```python
registry.register(tool)

registry.get(name)

registry.list_tools()
```

Agent 不应该直接知道具体 Tool 实现。

## 学习重点

理解：

```text
Tool Definition
≠
Tool Implementation
≠
Tool Execution
```

这是 Harness 非常核心的分层。

---

# 三、第二阶段：Agent 为什么需要 State 和 Context

---

# HARN-05：Agent State + Trajectory

## 目标

正式引入 Agent State。

至少记录：

```text
task_id
goal
current_step
status
messages
tool_calls
tool_results
created_at
updated_at
```

增加完整 trajectory：

```text
Step 1
LLM Call

Step 2
Tool Call

Step 3
Tool Result

Step 4
LLM Call
```

## 学习重点

理解：

```text
Context
State
Trajectory
```

并不是一回事。

State 是系统真实状态。

Context 是某一次发给模型的信息。

Trajectory 是 Agent 已经走过的执行路径。

---

# HARN-06：Context Engine

## 目标

不要再让 Agent Loop 自己拼 Prompt。

设计：

```text
ContextBuilder
```

负责：

```text
System Prompt
+
Goal
+
Relevant History
+
Tool Definitions
+
Current State
```

形成 Model Input。

## 加入第一个 Context Budget

例如：

```text
max_context_chars
```

先不用真正 tokenizer。

超出后进行简单：

```text
truncate
```

## 学习重点

最重要的问题：

> 为什么 Context Management 必须独立于 Agent Loop？

---

# HARN-07：Context Compaction

## 目标

故意制造长 Agent Session。

让 Context 超限。

实现最简单：

```text
Old History
      ↓
Summarizer
      ↓
Summary
+
Recent Messages
```

形成：

```text
System
Goal
Summary
Recent History
Tool Definitions
```

## 学习重点

观察：

```text
State 并没有丢
```

但是：

```text
Context 被压缩了
```

由此真正理解：

> Context ≠ State。

同时记录 Summary 可能造成的信息损失。

---

# 四、第三阶段：让 Agent 可以暂停、崩溃和恢复

---

# HARN-08：Checkpoint

## 目标

实现：

```text
Agent
 ↓
Step 1
 ↓
Checkpoint
 ↓
Step 2
 ↓
Checkpoint
```

首先使用：

```text
JSON / SQLite
```

即可。

Checkpoint 至少保存：

```text
task_id
status
step
state
trajectory
context metadata
```

## Demo

让程序执行到 Step 3 后人为退出。

重新启动：

```bash
python resume.py <task_id>
```

从 Checkpoint 恢复。

## 学习重点

理解：

```text
State
≠
Checkpoint
```

Checkpoint 是 State 的持久化恢复点。

---

# HARN-09：Interrupt / Resume

## 目标

加入主动暂停机制。

例如 Tool：

```text
delete_file
```

不能直接执行。

Agent Runtime 返回：

```text
WAITING_APPROVAL
```

执行链：

```text
Agent
 ↓
Dangerous Tool
 ↓
Interrupt
 ↓
Checkpoint
 ↓
WAITING_APPROVAL

用户批准

Resume
 ↓
继续执行
```

## 学习重点

真正理解 Long-running Agent：

> Agent 不应该依赖一个永远不断开的进程或 HTTP Connection。

---

# 五、第四阶段：Harness 的控制层

---

# HARN-10：Hook / Middleware

## 目标

增加生命周期事件：

```text
before_model_call
after_model_call

before_tool_call
after_tool_call

on_error

before_step
after_step
```

允许注册 Hook。

例如：

```text
LoggingHook
MetricsHook
```

## 学习重点

观察为什么很多 Harness 都存在：

```text
Hook
Middleware
Event
Interceptor
```

它们解决的是：

> 横切逻辑不能污染 Agent Loop。

---

# HARN-11：Policy / Permission

## 目标

不要在 Tool 中硬编码安全规则。

设计：

```text
PolicyEngine
PermissionDecision
```

例如：

```text
read_file
→ ALLOW

write_file
→ ALLOW

git push
→ REQUIRE_APPROVAL

rm -rf
→ DENY
```

执行链：

```text
Agent
 ↓
Tool Call
 ↓
Policy Engine
 ↓
ALLOW / DENY / APPROVAL
 ↓
Tool Executor
```

## 学习重点

理解：

```text
模型决定“想做什么”
```

和：

```text
Runtime 决定“允不允许做”
```

必须分离。

---

# HARN-12：Skill / Plugin

## 目标

开始做 Harness 扩展机制。

先实现简单 Plugin：

```python
class Plugin:
    def setup(harness):
        ...
```

Plugin 可以注册：

```text
Tool
Hook
Prompt Fragment
Policy
```

然后实现 Skill：

```text
GitSkill
```

加载以后才出现：

```text
git_status
git_diff
git_commit
```

## 学习重点

理解：

```text
Tool
Skill
Plugin
```

三者区别。

建议形成：

```text
Tool
= 一个原子动作

Skill
= 一组围绕任务领域组织的能力

Plugin
= 向 Harness 注入能力的扩展机制
```

---

# 六、第五阶段：真正进入 Harness / Runtime 工程问题

---

# HARN-13：Sandbox

## 目标

为 Coding Agent 增加 Shell。

但禁止 Agent 直接：

```python
subprocess.run()
```

设计：

```text
Sandbox
 ├── LocalSandbox
 └── future: DockerSandbox
```

Tool Executor 只能：

```text
Tool
↓
Sandbox API
↓
Process
```

加入：

```text
cwd
timeout
environment
output limit
```

## 学习重点

理解为什么：

```text
Tool Permission
```

和：

```text
Sandbox Isolation
```

是两个不同层次的问题。

---

# HARN-14：MCP Adapter

## 目标

让 Harness 不只能够执行本地 Tool。

加入：

```text
MCP Client Adapter
```

执行链：

```text
Agent
 ↓
Tool Registry
 ↓
MCP Tool Adapter
 ↓
MCP Client
 ↓
MCP Server
 ↓
External System
```

最终：

```text
LocalTool
MCPTool
```

对于 Agent Loop 应该基本没有区别。

## 学习重点

真正理解：

```text
Function Calling
Tool
MCP
```

三者之间的关系。

---

# 七、第六阶段：从 Harness 进入 Agent Runtime

---

# HARN-15：Task Runtime + Worker

## 目标

此前：

```text
CLI
↓
Agent.run()
```

现在拆成：

```text
Client
 ↓
Task
 ↓
Task Store
 ↓
Worker
 ↓
Agent Harness
```

创建：

```text
AgentTask
AgentWorker
TaskStore
```

Task 状态：

```text
PENDING
RUNNING
WAITING
SUSPENDED
COMPLETED
FAILED
CANCELLED
```

## 最重要的变化

执行和调用方解耦。

以前：

```text
Client connection
=
Agent lifetime
```

现在：

```text
Task lifetime
≠
Client lifetime
```

这一步是从：

> Agent Harness

真正跨入：

> Agent Runtime

的重要节点。

---

# HARN-16：Scheduler + Reliability

## 目标

为多个 Agent Task 增加：

```text
Scheduler
```

最初只需要支持：

```text
priority
max_concurrency
task queue
```

然后加入：

```text
retry
timeout
exponential backoff
cancellation
idempotency
```

重点模拟：

### Case 1

LLM API 临时失败。

### Case 2

Tool Timeout。

### Case 3

Worker 在 Tool 执行后、Checkpoint 前崩溃。

### Case 4

Agent 连续调用相同 Tool。

## 学习重点

这一步重点理解：

```text
Retry
≠
Recovery
≠
Resume
```

以及为什么：

```text
Side Effect
+
Retry
```

会产生重复执行风险。

---

# HARN-17：Observability

## 目标

让 Harness 从“能运行”变成“能排错”。

每一个 Task 生成：

```text
task_id
trace_id
session_id
```

记录：

```text
Agent Step
Model Call
Tool Call
Tool Result
Token
TTFT
Latency
Retry
Context Size
Error
State Transition
```

形成：

```text
Task
 │
 ├── Step 1
 │    ├── Model
 │    └── Tool
 │
 ├── Step 2
 │    ├── Model
 │    └── Tool
 │
 └── Step 3
```

先使用结构化日志。

然后再考虑：

```text
OpenTelemetry
Langfuse
Prometheus
```

## 学习重点

能够回答：

> Agent 为什么失败？

到底是：

```text
Model
Prompt
Context
Tool
Harness
Runtime
Infrastructure
```

哪一层出了问题？

---

# HARN-18：最终整合——Mini Coding Agent Harness

## 最终任务

在前面所有模块都存在以后，再构建一个简化 Coding Agent。

提供：

```text
read_file
write_file
search_files
shell
git_diff
```

实现：

```text
用户：
修复这个项目中的 bug

        ↓

Task Runtime
        ↓
Agent Harness
        ↓
Context Builder
        ↓
LLM
        ↓
search_files
        ↓
read_file
        ↓
edit
        ↓
run test
        ↓
observe error
        ↓
edit again
        ↓
test passes
        ↓
final answer
```

并支持：

```text
Checkpoint
Interrupt
Resume
Permission
Sandbox
Plugin
MCP
Tracing
```

这一阶段不应该大量增加新架构。

它的目的只是：

> 把之前独立学习的机制真正串成一个完整 Agent System。

---

# 八、最终建议目录

完成整个路线以后，项目可以自然演化为：

```text
agent_harness/
│
├── harness/
│   │
│   ├── core/
│   │   ├── agent.py
│   │   ├── agent_loop.py
│   │   └── events.py
│   │
│   ├── model/
│   │   ├── base.py
│   │   └── openai_compatible.py
│   │
│   ├── context/
│   │   ├── builder.py
│   │   └── compaction.py
│   │
│   ├── tools/
│   │   ├── base.py
│   │   ├── registry.py
│   │   └── executor.py
│   │
│   ├── state/
│   │   ├── state.py
│   │   └── checkpoint.py
│   │
│   ├── policy/
│   │   └── engine.py
│   │
│   ├── hooks/
│   │   └── manager.py
│   │
│   ├── plugins/
│   │   └── manager.py
│   │
│   ├── sandbox/
│   │   ├── base.py
│   │   └── local.py
│   │
│   ├── mcp/
│   │   └── adapter.py
│   │
│   └── runtime/
│       ├── task.py
│       ├── worker.py
│       ├── scheduler.py
│       └── recovery.py
│
├── examples/
├── tests/
└── docs/
```

注意：

不要从第一天就创建并填满这些模块。

它应该随着每个阶段的问题自然长出来。

---

# 九、每次交给 Claude Code / Codex 的统一提示词

每完成一个阶段后，下一阶段使用下面这个模板。

```text
你正在帮助我通过亲手构建 Agent Harness 学习 Agent Systems Engineering。

项目根目录存在：

HARNESS_LEARNING_ROADMAP.md

请先阅读该文件。

本次只执行：

HARN-XX

必须严格遵守以下要求：

1. 先阅读当前项目代码，理解上一阶段已经实现的架构。
2. 不允许提前实现之后阶段的功能。
3. 尽可能在现有架构上做最小演进，而不是推翻重写。
4. 开始编码前，先告诉我：
   - 当前架构是什么
   - 当前架构为什么不能很好解决本阶段问题
   - 本阶段准备增加什么机制
5. 再进行实现。
6. 完成后必须：
   - 提供可直接运行的 Demo
   - 增加必要测试
   - 更新 docs/stage_notes/HARN-XX.md
7. Stage Note 必须解释：
   - 本阶段解决的问题
   - 核心机制
   - 完整调用链
   - 最值得阅读的代码
   - Trade-off
   - 尚未解决的问题
8. 不要为了“工程完整性”加入当前阶段没有要求的架构。
9. 优先写容易理解的代码，而不是炫技式抽象。
10. 如果发现当前项目存在问题，可以修复，但必须解释原因。

这个项目的主要目标不是快速完成产品，而是让我真正理解：

Agent Harness / Agent Runtime 为什么会演化成现在这样的架构。
```

---

# 十、学习过程中最重要的原则

每一阶段都不要只问：

> 这个模块怎么实现？

而要理解三个问题：

```text
1. 没有它的时候有什么问题？

2. 它到底控制了哪一部分 Agent 行为？

3. 引入它之后，又产生了什么新的问题？
```

Agent Harness 的架构实际上就是这样一步一步长出来的：

```text
LLM Call
↓
Agent Loop

Agent Loop 太硬编码
↓
Tool Registry

历史越来越多
↓
Context Engine

进程挂了任务全丢
↓
Checkpoint

需要人工确认
↓
Interrupt / Resume

横切逻辑越来越多
↓
Hook

权限越来越复杂
↓
Policy

能力越来越多
↓
Skill / Plugin

Shell 风险太高
↓
Sandbox

外部工具越来越多
↓
MCP

任务越来越长
↓
Task Runtime

用户越来越多
↓
Scheduler / Worker

失败越来越复杂
↓
Reliability

系统越来越难排错
↓
Observability
```

这条“问题 → 架构机制”的演进链，本身就是整个 Agent Harness 最值得掌握的知识。
