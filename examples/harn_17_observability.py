"""HARN-17 Demo: one correlated Task -> Step -> Model + Tool trace."""

from __future__ import annotations

import asyncio
import json
import logging

from harness import (
    AgentTask,
    AgentWorker,
    CalculatorTool,
    CompositeTraceRecorder,
    ContextBuilder,
    HookManager,
    InMemoryTaskStore,
    InMemoryTraceRecorder,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    RetryPolicy,
    StructuredLogTraceRecorder,
    TaskScheduler,
    TokenUsage,
    ToolAgentLoop,
    ToolExecutor,
    ToolRegistry,
    TraceEventKind,
    TraceIdentity,
    TracingHook,
    TracingRetryObserver,
    TracingTaskStore,
    format_trace_tree,
)
from harness.model import ModelTransportError


class ObservableProvider(ModelProvider):
    """Fail once, call a Tool, and then return the final answer."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            raise ModelTransportError("simulated transient connection failure")
        if _step(request) == 1:
            action: dict[str, object] = {
                "type": "tool_call",
                "name": "calculator",
                "arguments": {"expression": "6 * 7"},
            }
        else:
            action = {
                "type": "final_answer",
                "answer": "The calculator result is 42.",
            }
        return ModelResponse(
            response_id=f"demo-response-{self.calls}",
            model=request.model,
            message=ModelMessage(
                MessageRole.ASSISTANT,
                json.dumps(action, separators=(",", ":")),
            ),
            finish_reason="stop",
            usage=TokenUsage(24, 9, 33),
            latency_ms=18.5,
            ttft_ms=4.25,
        )


def _step(request: ModelRequest) -> int:
    current_state = next(
        message.content
        for message in request.messages
        if message.content.startswith("Current State:\n")
    )
    return int(json.loads(current_state.split("\n", 1)[1])["current_step"])


def _loop_factory(
    provider: ModelProvider,
    registry: ToolRegistry,
    recorder: CompositeTraceRecorder,
):
    def create(task: AgentTask) -> ToolAgentLoop:
        identity = TraceIdentity(task.task_id, task.trace_id, task.session_id)
        return ToolAgentLoop(
            provider,
            model="harn-17-demo-model",
            max_steps=3,
            tool_executor=ToolExecutor(registry),
            context_builder=ContextBuilder(max_context_chars=8_000),
            hook_manager=HookManager([TracingHook(identity, recorder)]),
        )

    return create


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    memory = InMemoryTraceRecorder()
    recorder = CompositeTraceRecorder(
        [memory, StructuredLogTraceRecorder(logging.getLogger("harn-17.trace"))]
    )
    store = TracingTaskStore(InMemoryTaskStore(), recorder)
    provider = ObservableProvider()
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    worker = AgentWorker(store, _loop_factory(provider, registry, recorder))
    scheduler = TaskScheduler(
        store,
        worker,
        retry_policy=RetryPolicy(
            max_attempts=2,
            base_delay_seconds=0,
            max_delay_seconds=0,
        ),
        retry_observer=TracingRetryObserver(recorder),
    )

    task = scheduler.submit(
        "Calculate six times seven",
        task_id="harn-17-demo",
        trace_id="trace-harn-17-demo",
        session_id="learning-session",
    )
    await scheduler.run_until_idle()
    completed = store.get(task.task_id)
    tree = format_trace_tree(memory.events)

    print("\nTrace summary")
    print(
        f"Task {completed.task_id}: status={completed.status.value}, "
        f"attempts={completed.attempts}, answer={completed.final_answer!r}"
    )
    print(f"Trace {completed.trace_id} / Session {completed.session_id}")
    for step in tree["steps"]:
        model = [event["kind"] for event in step["model"]]
        tools = [event["kind"] for event in step["tools"]]
        print(f"  Step {step['step']}: Model={model}, Tool={tools}")
    retries = sum(
        event.kind is TraceEventKind.RETRY for event in memory.events
    )
    print(f"Events={len(memory.events)}, retries={retries}")


if __name__ == "__main__":
    asyncio.run(main())
