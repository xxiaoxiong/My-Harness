import asyncio
import json
import sys
import tempfile
import unittest
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
    TaskStatus,
    TaskSubmissionConflictError,
    Tool,
    ToolAgentLoop,
    ToolCall,
    ToolExecutor,
    ToolRegistry,
    ToolSchema,
    tool_call_idempotency_key,
)
from harness.core import JsonValue
from harness.model import ModelTransportError


class FinalProvider(ModelProvider):
    def __init__(self, answer: str = "done") -> None:
        self._answer = answer

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return _response(
            request,
            {"type": "final_answer", "answer": self._answer},
        )


class TransientProvider(ModelProvider):
    def __init__(self, failures: int) -> None:
        self._failures = failures
        self.calls = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls <= self._failures:
            raise ModelTransportError(f"temporary model failure {self.calls}")
        return _response(
            request,
            {"type": "final_answer", "answer": "recovered"},
        )


class TimeoutThenSuccessProvider(ModelProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(60)
        return _response(
            request,
            {"type": "final_answer", "answer": "after timeout"},
        )


class ExactToolCallProvider(ModelProvider):
    def __init__(self, *, repeats: int, tool_name: str = "side_effect") -> None:
        self._repeats = repeats
        self._tool_name = tool_name

    async def generate(self, request: ModelRequest) -> ModelResponse:
        step = _current_step(request)
        if step <= self._repeats:
            action: dict[str, object] = {
                "type": "tool_call",
                "name": self._tool_name,
                "arguments": {"value": "same"},
            }
        else:
            action = {"type": "final_answer", "answer": "side effect observed"}
        return _response(request, action)


class ShellTimeoutProvider(ModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        if _current_step(request) == 1:
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
                "answer": "tool timeout was observed",
            }
        return _response(request, action)


class CountingSideEffectTool(Tool):
    def __init__(self) -> None:
        self.executions = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="side_effect",
            description="Increment a visible side-effect counter.",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        )

    async def execute(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        self.executions += 1
        return {"execution": self.executions, "value": arguments["value"]}


class SimulatedWorkerCrash(RuntimeError):
    pass


class CrashAfterFirstTool(Hook):
    def __init__(self) -> None:
        self.crashes = 0

    async def after_tool_call(self, context: HookContext) -> None:
        if self.crashes == 0:
            self.crashes += 1
            raise SimulatedWorkerCrash("crash after Tool, before Checkpoint")


class ConcurrencyState:
    def __init__(self, required_starts: int) -> None:
        self.start_order: list[str] = []
        self.active = 0
        self.max_active = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self._required_starts = required_starts


class ControlledProvider(ModelProvider):
    def __init__(self, label: str, state: ConcurrencyState) -> None:
        self._label = label
        self._state = state

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self._state.start_order.append(self._label)
        self._state.active += 1
        self._state.max_active = max(self._state.max_active, self._state.active)
        if len(self._state.start_order) >= self._state._required_starts:
            self._state.started.set()
        try:
            await self._state.release.wait()
        finally:
            self._state.active -= 1
        return _response(
            request,
            {"type": "final_answer", "answer": f"{self._label} done"},
        )


def _response(
    request: ModelRequest,
    action: dict[str, object],
) -> ModelResponse:
    return ModelResponse(
        response_id=f"reliability-response-{_current_step(request)}",
        model=request.model,
        message=ModelMessage(
            MessageRole.ASSISTANT,
            json.dumps(action, separators=(",", ":")),
        ),
        finish_reason="stop",
        usage=None,
        latency_ms=0.0,
    )


def _current_step(request: ModelRequest) -> int:
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
        model="reliability-model",
        max_steps=max_steps,
        tool_executor=executor or ToolExecutor(ToolRegistry()),
        context_builder=ContextBuilder(max_context_chars=6_000),
        hook_manager=hooks,
    )


class ToolIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_call_is_executed_once_and_replayed_from_store(self) -> None:
        tool = CountingSideEffectTool()
        registry = ToolRegistry()
        registry.register(tool)
        cache = InMemoryIdempotencyStore()
        executor = ToolExecutor(registry, idempotency_store=cache)
        call = ToolCall("side_effect", {"value": "same"})
        key = tool_call_idempotency_key("task-one", call)

        first = await executor.execute(call, idempotency_key=key)
        second = await executor.execute(call, idempotency_key=key)

        self.assertEqual(tool.executions, 1)
        self.assertEqual(first, second)
        self.assertEqual(len(cache), 1)
        self.assertNotEqual(
            key,
            tool_call_idempotency_key("task-two", call),
        )

    async def test_consecutive_identical_agent_calls_reuse_completed_result(
        self,
    ) -> None:
        tool = CountingSideEffectTool()
        harness = Harness(idempotency_store=InMemoryIdempotencyStore())
        harness.register_tool(tool)

        result = await harness.create_agent_loop(
            ExactToolCallProvider(repeats=2),
            max_steps=3,
            model="idempotency-model",
            max_context_chars=6_000,
        ).run("Repeat one exact call.", task_id="repeat-task")

        self.assertEqual(tool.executions, 1)
        self.assertEqual(len(result.state.tool_results), 2)
        self.assertEqual(result.state.tool_results[0], result.state.tool_results[1])


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_priority_queue_and_max_concurrency(self) -> None:
        store = InMemoryTaskStore()
        state = ConcurrencyState(required_starts=2)
        worker = AgentWorker(
            store,
            lambda task: _loop(ControlledProvider(task.task_id, state)),
        )
        scheduler = TaskScheduler(store, worker, max_concurrency=2)
        scheduler.submit("low", task_id="low", priority=1)
        scheduler.submit("high", task_id="high", priority=10)
        scheduler.submit("middle", task_id="middle", priority=5)

        draining = asyncio.create_task(scheduler.run_until_idle())
        await state.started.wait()
        self.assertEqual(state.start_order, ["high", "middle"])
        self.assertEqual(state.max_active, 2)
        self.assertEqual(store.get("low").status, TaskStatus.PENDING)
        state.release.set()
        await draining

        self.assertEqual(state.start_order, ["high", "middle", "low"])
        self.assertTrue(
            all(task.status is TaskStatus.COMPLETED for task in store.list_tasks())
        )

    async def test_transient_model_failure_retries_with_exponential_backoff(
        self,
    ) -> None:
        store = InMemoryTaskStore()
        provider = TransientProvider(failures=2)
        delays: list[float] = []

        async def record_delay(delay: float) -> None:
            delays.append(delay)

        worker = AgentWorker(store, lambda task: _loop(provider))
        scheduler = TaskScheduler(
            store,
            worker,
            retry_policy=RetryPolicy(
                max_attempts=3,
                base_delay_seconds=0.25,
                max_delay_seconds=1.0,
            ),
            sleeper=record_delay,
        )
        task = scheduler.submit("Recover model call.", task_id="transient")

        await scheduler.run_until_idle()
        completed = store.get(task.task_id)

        self.assertEqual(completed.status, TaskStatus.COMPLETED)
        self.assertEqual(completed.attempts, 3)
        self.assertEqual(provider.calls, 3)
        self.assertEqual(delays, [0.25, 0.5])

    async def test_task_timeout_is_retryable_and_bounded(self) -> None:
        store = InMemoryTaskStore()
        provider = TimeoutThenSuccessProvider()
        worker = AgentWorker(store, lambda task: _loop(provider))
        scheduler = TaskScheduler(
            store,
            worker,
            retry_policy=RetryPolicy(
                max_attempts=2,
                base_delay_seconds=0,
                max_delay_seconds=0,
            ),
            task_timeout_seconds=0.03,
        )
        task = scheduler.submit("Bound total attempt time.", task_id="timeout")

        await scheduler.run_until_idle()
        completed = store.get(task.task_id)

        self.assertEqual(completed.status, TaskStatus.COMPLETED)
        self.assertEqual(completed.attempts, 2)
        self.assertEqual(provider.calls, 2)

    async def test_retry_exhaustion_persists_final_failure(self) -> None:
        store = InMemoryTaskStore()
        provider = TransientProvider(failures=10)
        worker = AgentWorker(store, lambda task: _loop(provider))
        scheduler = TaskScheduler(
            store,
            worker,
            retry_policy=RetryPolicy(
                max_attempts=2,
                base_delay_seconds=0,
                max_delay_seconds=0,
            ),
        )
        task = scheduler.submit("Eventually fail.", task_id="exhausted")

        await scheduler.run_until_idle()
        failed = store.get(task.task_id)

        self.assertEqual(failed.status, TaskStatus.FAILED)
        self.assertEqual(failed.attempts, 2)
        self.assertEqual(failed.error, "temporary model failure 2")

    async def test_active_and_queued_tasks_can_be_cancelled(self) -> None:
        store = InMemoryTaskStore()
        state = ConcurrencyState(required_starts=1)
        worker = AgentWorker(
            store,
            lambda task: _loop(ControlledProvider(task.task_id, state)),
        )
        scheduler = TaskScheduler(store, worker, max_concurrency=1)
        active = scheduler.submit("active", task_id="active", priority=2)
        queued = scheduler.submit("queued", task_id="queued", priority=1)

        draining = asyncio.create_task(scheduler.run_until_idle())
        await state.started.wait()
        cancelled_queued = await scheduler.cancel(queued.task_id)
        cancelled_active = await scheduler.cancel(active.task_id)
        await draining

        self.assertEqual(cancelled_queued.status, TaskStatus.CANCELLED)
        self.assertEqual(cancelled_active.status, TaskStatus.CANCELLED)
        self.assertEqual(state.start_order, ["active"])

    async def test_submission_idempotency_deduplicates_and_detects_conflict(
        self,
    ) -> None:
        store = InMemoryTaskStore()
        worker = AgentWorker(store, lambda task: _loop(FinalProvider()))
        scheduler = TaskScheduler(store, worker)

        first = scheduler.submit(
            "same request",
            priority=4,
            idempotency_key="request-42",
        )
        duplicate = scheduler.submit(
            "same request",
            priority=4,
            idempotency_key="request-42",
        )

        self.assertIs(first, duplicate)
        self.assertEqual(len(store.list_tasks()), 1)
        self.assertEqual(scheduler.queued_task_ids, (first.task_id,))
        with self.assertRaises(TaskSubmissionConflictError):
            scheduler.submit(
                "different request",
                priority=4,
                idempotency_key="request-42",
            )

    async def test_tool_result_survives_crash_before_checkpoint(self) -> None:
        store = InMemoryTaskStore()
        idempotency = InMemoryIdempotencyStore()
        side_effect = CountingSideEffectTool()
        crash_hook = CrashAfterFirstTool()

        def factory(task: AgentTask) -> ToolAgentLoop:
            registry = ToolRegistry()
            registry.register(side_effect)
            hooks = HookManager()
            hooks.register(crash_hook)
            return _loop(
                ExactToolCallProvider(repeats=1),
                executor=ToolExecutor(
                    registry,
                    idempotency_store=idempotency,
                ),
                hooks=hooks,
            )

        worker = AgentWorker(store, factory)
        scheduler = TaskScheduler(
            store,
            worker,
            retry_policy=RetryPolicy(
                max_attempts=2,
                base_delay_seconds=0,
                max_delay_seconds=0,
                retryable=lambda error: isinstance(error, HookExecutionError)
                and isinstance(error.cause, SimulatedWorkerCrash),
            ),
        )
        task = scheduler.submit("Perform one side effect.", task_id="crash-task")

        await scheduler.run_until_idle()
        completed = store.get(task.task_id)

        self.assertEqual(completed.status, TaskStatus.COMPLETED)
        self.assertEqual(completed.attempts, 2)
        self.assertEqual(crash_hook.crashes, 1)
        self.assertEqual(side_effect.executions, 1)
        self.assertEqual(len(idempotency), 1)

    async def test_shell_tool_timeout_is_an_agent_observation(self) -> None:
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
                model="tool-timeout-model",
                max_steps=2,
                max_context_chars=6_000,
            ).run("Observe a Tool timeout.", task_id="tool-timeout")

            process = result.state.tool_results[0].result
            self.assertTrue(process["timed_out"])  # type: ignore[index]
            self.assertEqual(result.final_answer, "tool timeout was observed")


if __name__ == "__main__":
    unittest.main()
