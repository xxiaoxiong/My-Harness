"""HARN-16 Demo: scheduling, retry, timeout, cancellation, and idempotency."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from collections.abc import Mapping

from harness import (
    AgentTask,
    AgentWorker,
    ContextBuilder,
    Harness,
    Hook,
    HookContext,
    HookExecutionError,
    HookManager,
    InMemoryIdempotencyStore,
    InMemoryTaskStore,
    LocalSandbox,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    RetryPolicy,
    ShellTool,
    TaskScheduler,
    Tool,
    ToolAgentLoop,
    ToolExecutor,
    ToolRegistry,
    ToolSchema,
)
from harness.core import JsonValue
from harness.model import ModelTransportError


class FinalProvider(ModelProvider):
    def __init__(self, answer: str) -> None:
        self._answer = answer

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return _response(
            request,
            {"type": "final_answer", "answer": self._answer},
        )


class OrderedProvider(FinalProvider):
    def __init__(
        self,
        label: str,
        order: list[str],
        concurrency: dict[str, int],
    ) -> None:
        super().__init__(f"{label} completed")
        self._label = label
        self._order = order
        self._concurrency = concurrency

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self._order.append(self._label)
        self._concurrency["active"] += 1
        self._concurrency["max"] = max(
            self._concurrency["max"],
            self._concurrency["active"],
        )
        await asyncio.sleep(0.01)
        self._concurrency["active"] -= 1
        return await super().generate(request)


class TransientProvider(FinalProvider):
    def __init__(self) -> None:
        super().__init__("model retry recovered")
        self.calls = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            raise ModelTransportError("temporary model transport failure")
        return await super().generate(request)


class ShellTimeoutProvider(ModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        if _step(request) == 1:
            action: dict[str, object] = {
                "type": "tool_call",
                "name": "shell",
                "arguments": {
                    "command": "import time;time.sleep(60)",
                    "timeout_seconds": 0.03,
                },
            }
        else:
            action = {
                "type": "final_answer",
                "answer": "Tool timeout became an observation.",
            }
        return _response(request, action)


class RepeatedSideEffectProvider(ModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        if _step(request) <= 2:
            action: dict[str, object] = {
                "type": "tool_call",
                "name": "side_effect",
                "arguments": {"value": "same"},
            }
        else:
            action = {
                "type": "final_answer",
                "answer": "Repeated side effect was observed safely.",
            }
        return _response(request, action)


class SideEffectTool(Tool):
    def __init__(self) -> None:
        self.executions = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="side_effect",
            description="Increment one visible side-effect counter.",
            parameters={"type": "object"},
        )

    async def execute(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        self.executions += 1
        return {"execution": self.executions}


class SimulatedCrash(RuntimeError):
    pass


class CrashOnceAfterTool(Hook):
    def __init__(self) -> None:
        self.crashed = False

    async def after_tool_call(self, context: HookContext) -> None:
        if not self.crashed:
            self.crashed = True
            raise SimulatedCrash("after Tool, before Checkpoint")


def _response(
    request: ModelRequest,
    action: dict[str, object],
) -> ModelResponse:
    return ModelResponse(
        response_id=f"harn-16-response-{_step(request)}",
        model=request.model,
        message=ModelMessage(
            MessageRole.ASSISTANT,
            json.dumps(action, separators=(",", ":")),
        ),
        finish_reason="stop",
        usage=None,
        latency_ms=0.0,
    )


def _step(request: ModelRequest) -> int:
    state_message = next(
        message.content
        for message in request.messages
        if message.content.startswith("Current State:\n")
    )
    return int(json.loads(state_message.split("\n", 1)[1])["current_step"])


def _loop(
    provider: ModelProvider,
    *,
    executor: ToolExecutor | None = None,
    hooks: HookManager | None = None,
    max_steps: int = 3,
) -> ToolAgentLoop:
    return ToolAgentLoop(
        provider,
        model="reliability-demo-model",
        max_steps=max_steps,
        tool_executor=executor or ToolExecutor(ToolRegistry()),
        context_builder=ContextBuilder(max_context_chars=6_000),
        hook_manager=hooks,
    )


async def _scheduling_demo() -> None:
    store = InMemoryTaskStore()
    order: list[str] = []
    concurrency = {"active": 0, "max": 0}
    worker = AgentWorker(
        store,
        lambda task: _loop(OrderedProvider(task.task_id, order, concurrency)),
    )
    scheduler = TaskScheduler(store, worker, max_concurrency=2)
    scheduler.submit("low", task_id="low", priority=1)
    scheduler.submit("high", task_id="high", priority=10)
    scheduler.submit("middle", task_id="middle", priority=5)
    await scheduler.run_until_idle()
    print(f"Schedule order: {order}; max_concurrency_seen={concurrency['max']}")


async def _retry_and_submission_demo() -> None:
    store = InMemoryTaskStore()
    provider = TransientProvider()
    delays: list[float] = []

    async def record_delay(delay: float) -> None:
        delays.append(delay)

    worker = AgentWorker(store, lambda task: _loop(provider))
    scheduler = TaskScheduler(
        store,
        worker,
        retry_policy=RetryPolicy(
            max_attempts=2,
            base_delay_seconds=0.1,
            max_delay_seconds=0.1,
        ),
        sleeper=record_delay,
    )
    first = scheduler.submit(
        "Retry a temporary model failure.",
        idempotency_key="client-request-1",
    )
    duplicate = scheduler.submit(
        "Retry a temporary model failure.",
        idempotency_key="client-request-1",
    )
    await scheduler.run_until_idle()
    completed = store.get(first.task_id)
    print(
        "Retry: "
        f"same_task={first.task_id == duplicate.task_id}, "
        f"attempts={completed.attempts}, delays={delays}, "
        f"status={completed.status.value}"
    )


async def _tool_timeout_demo() -> None:
    with tempfile.TemporaryDirectory() as directory:
        sandbox = LocalSandbox(
            directory,
            default_timeout_seconds=0.05,
            max_timeout_seconds=1,
            shell_argv=(sys.executable, "-c"),
        )
        harness = Harness()
        harness.register_tool(ShellTool(sandbox))
        result = await harness.create_agent_loop(
            ShellTimeoutProvider(),
            model="reliability-demo-model",
            max_steps=2,
            max_context_chars=6_000,
        ).run("Observe a bounded Tool timeout.", task_id="tool-timeout-demo")
        process = result.state.tool_results[0].result
        print(
            "Tool timeout: "
            f"timed_out={process['timed_out']}, "  # type: ignore[index]
            f"task_status={result.status.value}"
        )


async def _crash_and_idempotency_demo() -> None:
    task_store = InMemoryTaskStore()
    result_store = InMemoryIdempotencyStore()
    side_effect = SideEffectTool()
    crash_hook = CrashOnceAfterTool()

    def factory(task: AgentTask) -> ToolAgentLoop:
        registry = ToolRegistry()
        registry.register(side_effect)
        hooks = HookManager()
        hooks.register(crash_hook)
        return _loop(
            RepeatedSideEffectProvider(),
            executor=ToolExecutor(
                registry,
                idempotency_store=result_store,
            ),
            hooks=hooks,
        )

    worker = AgentWorker(task_store, factory)
    scheduler = TaskScheduler(
        task_store,
        worker,
        retry_policy=RetryPolicy(
            max_attempts=2,
            base_delay_seconds=0,
            max_delay_seconds=0,
            retryable=lambda error: isinstance(error, HookExecutionError)
            and isinstance(error.cause, SimulatedCrash),
        ),
    )
    task = scheduler.submit("Run one logical side effect.", task_id="crash-demo")
    await scheduler.run_until_idle()
    completed = task_store.get(task.task_id)
    print(
        "Crash + repeated call: "
        f"attempts={completed.attempts}, logical_calls=3, "
        f"actual_executions={side_effect.executions}, "
        f"cached_results={len(result_store)}"
    )


async def _cancellation_demo() -> None:
    store = InMemoryTaskStore()
    worker = AgentWorker(store, lambda task: _loop(FinalProvider("unused")))
    scheduler = TaskScheduler(store, worker)
    task = scheduler.submit("Cancel before claim.", task_id="cancel-demo")
    cancelled = await scheduler.cancel(task.task_id)
    await scheduler.run_until_idle()
    print(f"Cancellation: status={cancelled.status.value}")


async def main() -> None:
    await _scheduling_demo()
    await _retry_and_submission_demo()
    await _tool_timeout_demo()
    await _crash_and_idempotency_demo()
    await _cancellation_demo()


if __name__ == "__main__":
    asyncio.run(main())
