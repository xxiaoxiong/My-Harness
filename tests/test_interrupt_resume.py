import json
import tempfile
import unittest
from pathlib import Path

from harness.context import ContextBuilder
from harness.hooks import Hook, HookContext, HookManager
from harness.model import (
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
)
from harness.runtime import JsonCheckpointStore, ToolAgentLoop
from harness.policy import PermissionDecision, StaticPolicyEngine
from harness.state import AgentStatus, TrajectoryEventKind
from harness.tools import (
    DELETE_FILE_NAME,
    DeleteFileTool,
    ToolExecutor,
    ToolRegistry,
)


class SequenceProvider(ModelProvider):
    def __init__(self, *actions: dict[str, object]) -> None:
        self._actions = list(actions)
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._actions:
            raise AssertionError("provider was called unexpectedly")
        action = self._actions.pop(0)
        return ModelResponse(
            response_id=f"interrupt-response-{len(self.requests)}",
            model=request.model,
            message=ModelMessage(
                MessageRole.ASSISTANT,
                json.dumps(action, separators=(",", ":")),
            ),
            finish_reason="stop",
            usage=None,
            latency_ms=0.0,
        )


class ToolRecordingHook(Hook):
    def __init__(self) -> None:
        self.events: list[str] = []

    async def before_tool_call(self, context: HookContext) -> None:
        self.events.append("before_tool_call")

    async def after_tool_call(self, context: HookContext) -> None:
        self.events.append("after_tool_call")


def _delete_call(path: str) -> dict[str, object]:
    return {
        "type": "tool_call",
        "name": DELETE_FILE_NAME,
        "arguments": {"path": path},
    }


def _final(answer: str) -> dict[str, object]:
    return {"type": "final_answer", "answer": answer}


def _loop(
    provider: ModelProvider,
    store: JsonCheckpointStore,
    file_root: Path,
    *,
    hook_manager: HookManager | None = None,
) -> ToolAgentLoop:
    registry = ToolRegistry()
    registry.register(DeleteFileTool(file_root))
    return ToolAgentLoop(
        provider,
        model="interrupt-demo-model",
        max_steps=3,
        tool_executor=ToolExecutor(registry),
        context_builder=ContextBuilder(max_context_chars=4_000),
        checkpoint_store=store,
        policy_engine=StaticPolicyEngine(
            {DELETE_FILE_NAME: PermissionDecision.REQUIRE_APPROVAL}
        ),
        hook_manager=hook_manager,
    )


class InterruptResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_approval_interrupt_requires_checkpoint_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ToolRegistry()
            registry.register(DeleteFileTool(root))

            loop = ToolAgentLoop(
                SequenceProvider(_delete_call("anything.txt")),
                model="interrupt-demo-model",
                max_steps=3,
                tool_executor=ToolExecutor(registry),
                context_builder=ContextBuilder(max_context_chars=4_000),
                policy_engine=StaticPolicyEngine(
                    {DELETE_FILE_NAME: PermissionDecision.REQUIRE_APPROVAL}
                ),
            )

            with self.assertRaisesRegex(RuntimeError, "needs a checkpoint_store"):
                await loop.run("Delete a file.")

    async def test_dangerous_tool_waits_then_executes_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "delete-after-approval.txt"
            target.write_text("keep until approved", encoding="utf-8")
            store = JsonCheckpointStore(root / "checkpoints")
            before_approval_hook = ToolRecordingHook()

            waiting = await _loop(
                SequenceProvider(_delete_call(target.name)),
                store,
                root,
                hook_manager=HookManager([before_approval_hook]),
            ).run("Delete the disposable file.", task_id="approval-task")

            self.assertEqual(waiting.status, AgentStatus.WAITING_APPROVAL)
            self.assertEqual(waiting.steps_executed, 1)
            self.assertEqual(waiting.pending_tool_call.name, DELETE_FILE_NAME)  # type: ignore[union-attr]
            self.assertTrue(target.exists())
            self.assertEqual(len(waiting.state.tool_results), 0)
            self.assertEqual(before_approval_hook.events, [])

            durable = store.load("approval-task")
            self.assertEqual(durable.status, AgentStatus.WAITING_APPROVAL)
            self.assertEqual(durable.state.pending_tool_call, waiting.pending_tool_call)
            self.assertEqual(durable.state.trajectory[-1].kind, TrajectoryEventKind.INTERRUPT)

            unused_provider = SequenceProvider()
            still_waiting = await _loop(
                unused_provider,
                store,
                root,
            ).resume("approval-task")
            self.assertEqual(still_waiting.status, AgentStatus.WAITING_APPROVAL)
            self.assertEqual(unused_provider.requests, [])
            self.assertTrue(target.exists())

            resumed_provider = SequenceProvider(_final("The file was deleted."))
            after_approval_hook = ToolRecordingHook()
            completed = await _loop(
                resumed_provider,
                store,
                root,
                hook_manager=HookManager([after_approval_hook]),
            ).resume("approval-task", approve=True)

            self.assertFalse(target.exists())
            self.assertEqual(completed.status, AgentStatus.FINISHED)
            self.assertEqual(completed.steps_executed, 2)
            self.assertIsNone(completed.pending_tool_call)
            self.assertEqual(
                after_approval_hook.events,
                ["before_tool_call", "after_tool_call"],
            )
            self.assertEqual(completed.last_tool_result.result, {"deleted": target.name})  # type: ignore[union-attr]
            self.assertEqual(
                [event.kind for event in completed.state.trajectory],
                [
                    TrajectoryEventKind.MODEL_CALL,
                    TrajectoryEventKind.TOOL_CALL,
                    TrajectoryEventKind.PERMISSION_DECISION,
                    TrajectoryEventKind.INTERRUPT,
                    TrajectoryEventKind.RESUME,
                    TrajectoryEventKind.TOOL_RESULT,
                    TrajectoryEventKind.MODEL_CALL,
                    TrajectoryEventKind.FINAL_ANSWER,
                ],
            )
            self.assertEqual(store.load("approval-task").status, AgentStatus.FINISHED)

    async def test_rejection_becomes_a_tool_observation_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "keep.txt"
            target.write_text("do not delete", encoding="utf-8")
            store = JsonCheckpointStore(root / "checkpoints")
            await _loop(
                SequenceProvider(_delete_call(target.name)),
                store,
                root,
            ).run("Delete the file.", task_id="rejection-task")
            provider = SequenceProvider(_final("Deletion was rejected."))

            completed = await _loop(provider, store, root).resume(
                "rejection-task",
                approve=False,
            )

            self.assertTrue(target.exists())
            self.assertEqual(completed.status, AgentStatus.FINISHED)
            self.assertEqual(
                completed.last_tool_result.error,  # type: ignore[union-attr]
                "tool call rejected by user",
            )
            observation = json.loads(provider.requests[0].messages[3].content)
            self.assertEqual(observation["error"], "tool call rejected by user")
            resume_event = completed.state.trajectory[4]
            self.assertEqual(resume_event.kind, TrajectoryEventKind.RESUME)
            self.assertFalse(resume_event.details["approved"])

    async def test_approval_is_rejected_when_task_is_not_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JsonCheckpointStore(root / "checkpoints")
            await _loop(
                SequenceProvider(_final("Done.")),
                store,
                root,
            ).run("Finish.", task_id="finished-task")

            with self.assertRaisesRegex(ValueError, "not waiting"):
                await _loop(SequenceProvider(), store, root).resume(
                    "finished-task",
                    approve=True,
                )


if __name__ == "__main__":
    unittest.main()
