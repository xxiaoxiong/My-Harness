import asyncio
import json
import tempfile
import unittest

from harness import (
    AgentTask,
    AgentWorker,
    CalculatorTool,
    ContextBuilder,
    DuplicateTaskError,
    InMemoryTaskStore,
    InvalidTaskTransition,
    InvalidWorkerTaskState,
    JsonCheckpointStore,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    PermissionDecision,
    StaticPolicyEngine,
    TaskNotFoundError,
    TaskStatus,
    ToolAgentLoop,
    ToolExecutor,
    ToolRegistry,
)


class FinalProvider(ModelProvider):
    def __init__(
        self,
        answer: str = "Worker completed the stored task.",
        *,
        started: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        self._answer = answer
        self._started = started
        self._release = release

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if self._started is not None:
            self._started.set()
        if self._release is not None:
            await self._release.wait()
        return _response(
            request,
            {"type": "final_answer", "answer": self._answer},
        )


class FailingProvider(ModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        raise RuntimeError("model service unavailable")


class ApprovalProvider(ModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        step = _current_step(request)
        if step == 1:
            action: dict[str, object] = {
                "type": "tool_call",
                "name": "calculator",
                "arguments": {"expression": "20 + 22"},
            }
        else:
            action = {"type": "final_answer", "answer": "Approved result is 42."}
        return _response(request, action)


class RepeatingToolProvider(ModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        return _response(
            request,
            {
                "type": "tool_call",
                "name": "calculator",
                "arguments": {"expression": "1 + 1"},
            },
        )


def _response(
    request: ModelRequest,
    action: dict[str, object],
) -> ModelResponse:
    return ModelResponse(
        response_id=f"task-response-{_current_step(request)}",
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
    max_steps: int = 2,
    checkpoint_store: JsonCheckpointStore | None = None,
    approval: bool = False,
) -> ToolAgentLoop:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return ToolAgentLoop(
        provider,
        model="task-runtime-model",
        max_steps=max_steps,
        tool_executor=ToolExecutor(registry),
        context_builder=ContextBuilder(max_context_chars=6_000),
        checkpoint_store=checkpoint_store,
        policy_engine=(
            StaticPolicyEngine(
                {"calculator": PermissionDecision.REQUIRE_APPROVAL}
            )
            if approval
            else None
        ),
    )


class AgentTaskTests(unittest.TestCase):
    def test_explicit_lifecycle_is_immutable_and_validated(self) -> None:
        pending = AgentTask.create("Finish later.", task_id="task-lifecycle")
        running = pending.mark_running()
        waiting = running.mark_waiting()
        resumed = waiting.mark_running()
        completed = resumed.mark_completed("Done.")

        self.assertEqual(pending.status, TaskStatus.PENDING)
        self.assertEqual(completed.status, TaskStatus.COMPLETED)
        self.assertEqual(completed.final_answer, "Done.")
        self.assertTrue(completed.terminal)
        self.assertGreaterEqual(completed.updated_at, pending.updated_at)
        with self.assertRaises(InvalidTaskTransition):
            completed.mark_running()
        with self.assertRaises(ValueError):
            running.mark_completed(" ")
        with self.assertRaises(ValueError):
            AgentTask.create("Goal.", task_id=" ")

    def test_cancelled_and_failed_are_distinct_terminal_states(self) -> None:
        cancelled = AgentTask.create("Cancel me.").mark_cancelled()
        failed = AgentTask.create("Fail me.").mark_running().mark_failed("boom")

        self.assertEqual(cancelled.status, TaskStatus.CANCELLED)
        self.assertIsNone(cancelled.error)
        self.assertEqual(failed.status, TaskStatus.FAILED)
        self.assertEqual(failed.error, "boom")


class InMemoryTaskStoreTests(unittest.TestCase):
    def test_create_get_update_and_filter(self) -> None:
        store = InMemoryTaskStore()
        first = AgentTask.create("First.", task_id="first")
        second = AgentTask.create("Second.", task_id="second")
        store.create(first)
        store.create(second)
        store.update(first.mark_running())

        self.assertEqual(store.get(" first ").status, TaskStatus.RUNNING)
        self.assertEqual(
            [task.task_id for task in store.list_tasks()],
            ["first", "second"],
        )
        self.assertEqual(
            store.list_tasks(status=TaskStatus.PENDING),
            (second,),
        )
        with self.assertRaises(DuplicateTaskError):
            store.create(first)
        with self.assertRaises(TaskNotFoundError):
            store.get("missing")


class AgentWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_can_disconnect_while_worker_owns_execution(self) -> None:
        store = InMemoryTaskStore()
        submitted = AgentTask.create(
            "Complete independently.",
            task_id="disconnected-client",
        )
        store.create(submitted)
        started = asyncio.Event()
        release = asyncio.Event()
        worker = AgentWorker(
            store,
            lambda task: _loop(
                FinalProvider(started=started, release=release)
            ),
        )

        background = asyncio.create_task(worker.execute(submitted.task_id))
        await started.wait()
        self.assertEqual(
            store.get(submitted.task_id).status,
            TaskStatus.RUNNING,
        )
        release.set()
        completed = await background

        self.assertEqual(completed.status, TaskStatus.COMPLETED)
        self.assertEqual(store.get(submitted.task_id), completed)
        self.assertEqual(
            completed.final_answer,
            "Worker completed the stored task.",
        )

    async def test_waiting_task_is_resumed_by_a_later_worker_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_store = JsonCheckpointStore(directory)
            store = InMemoryTaskStore()
            task = AgentTask.create("Calculate with approval.", task_id="approval-task")
            store.create(task)
            worker = AgentWorker(
                store,
                lambda agent_task: _loop(
                    ApprovalProvider(),
                    max_steps=3,
                    checkpoint_store=checkpoint_store,
                    approval=True,
                ),
            )

            waiting = await worker.execute(task.task_id)
            self.assertEqual(waiting.status, TaskStatus.WAITING)
            self.assertEqual(
                checkpoint_store.load(task.task_id).status.value,
                "waiting_approval",
            )

            completed = await worker.resume(task.task_id, approve=True)
            self.assertEqual(completed.status, TaskStatus.COMPLETED)
            self.assertEqual(completed.final_answer, "Approved result is 42.")

    async def test_worker_persists_failure_instead_of_losing_task(self) -> None:
        store = InMemoryTaskStore()
        task = AgentTask.create("Fail safely.", task_id="failure-task")
        store.create(task)
        worker = AgentWorker(store, lambda agent_task: _loop(FailingProvider()))

        failed = await worker.execute(task.task_id)

        self.assertEqual(failed.status, TaskStatus.FAILED)
        self.assertEqual(failed.error, "model service unavailable")
        self.assertEqual(store.get(task.task_id), failed)

    async def test_max_steps_maps_to_suspended_task(self) -> None:
        store = InMemoryTaskStore()
        task = AgentTask.create("Keep trying.", task_id="suspended-task")
        store.create(task)
        worker = AgentWorker(
            store,
            lambda agent_task: _loop(RepeatingToolProvider(), max_steps=1),
        )

        suspended = await worker.execute(task.task_id)

        self.assertEqual(suspended.status, TaskStatus.SUSPENDED)
        self.assertFalse(suspended.terminal)

    async def test_worker_rejects_operations_for_wrong_stored_state(self) -> None:
        store = InMemoryTaskStore()
        cancelled = AgentTask.create(
            "Already cancelled.",
            task_id="cancelled-task",
        ).mark_cancelled()
        store.create(cancelled)
        worker = AgentWorker(store, lambda agent_task: _loop(FinalProvider()))

        with self.assertRaises(InvalidWorkerTaskState):
            await worker.execute(cancelled.task_id)
        with self.assertRaises(InvalidWorkerTaskState):
            await worker.resume(cancelled.task_id, approve=True)


if __name__ == "__main__":
    unittest.main()
