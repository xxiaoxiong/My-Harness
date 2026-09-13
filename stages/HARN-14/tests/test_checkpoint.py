import json
import tempfile
import unittest
from pathlib import Path

from harness.context import ContextBuilder
from harness.model import (
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
)
from harness.runtime import (
    CheckpointCorruptError,
    CheckpointNotFoundError,
    InvalidCheckpointTaskId,
    JsonCheckpointStore,
    ToolAgentLoop,
)
from harness.state import AgentStatus
from harness.tools import CalculatorTool, ToolExecutor, ToolRegistry


class SimulatedProcessExit(RuntimeError):
    """Stand in for a process disappearing before the next checkpoint."""


class StepAwareProvider(ModelProvider):
    def __init__(self, *, crash_before_step: int | None = None) -> None:
        self._crash_before_step = crash_before_step
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        step = _current_step(request)
        if step == self._crash_before_step:
            raise SimulatedProcessExit(f"simulated exit before step {step}")

        if step <= 4:
            action: dict[str, object] = {
                "type": "tool_call",
                "name": "calculator",
                "arguments": {"expression": f"{step} + {step}"},
            }
        else:
            action = {
                "type": "final_answer",
                "answer": "Recovered and completed after four tool calls.",
            }
        return ModelResponse(
            response_id=f"checkpoint-response-{step}",
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
    state = json.loads(state_message.split("\n", 1)[1])
    return int(state["current_step"])


def _loop(
    provider: ModelProvider,
    store: JsonCheckpointStore,
) -> ToolAgentLoop:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return ToolAgentLoop(
        provider,
        model="checkpoint-demo-model",
        max_steps=6,
        tool_executor=ToolExecutor(registry),
        context_builder=ContextBuilder(max_context_chars=4_000),
        checkpoint_store=store,
    )


class JsonCheckpointStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkpoint_round_trip_preserves_recovery_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JsonCheckpointStore(directory)
            provider = StepAwareProvider()

            result = await _loop(provider, store).run(
                "Calculate four small expressions.",
                task_id="round-trip-task",
            )
            loaded = store.load("round-trip-task")
            payload = json.loads(
                (Path(directory) / "round-trip-task.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(loaded.task_id, result.state.task_id)
            self.assertEqual(loaded.status, AgentStatus.FINISHED)
            self.assertEqual(loaded.step, 5)
            self.assertEqual(loaded.state.goal, result.state.goal)
            self.assertEqual(loaded.state.messages, result.state.messages)
            self.assertEqual(loaded.state.tool_calls, result.state.tool_calls)
            self.assertEqual(loaded.state.tool_results, result.state.tool_results)
            self.assertEqual(loaded.state.trajectory, result.state.trajectory)
            self.assertEqual(loaded.state.final_answer, result.final_answer)
            self.assertGreater(loaded.context.message_count, 0)
            self.assertLessEqual(
                loaded.context.total_chars,
                loaded.context.max_context_chars,
            )
            self.assertEqual(
                set(payload),
                {
                    "schema_version",
                    "saved_at",
                    "task_id",
                    "status",
                    "step",
                    "state",
                    "trajectory",
                    "context",
                },
            )
            self.assertEqual(payload["task_id"], "round-trip-task")
            self.assertEqual(payload["schema_version"], 2)
            self.assertEqual(payload["status"], "finished")
            self.assertEqual(payload["step"], 5)
            self.assertEqual(len(payload["trajectory"]), 18)
            self.assertEqual(
                sorted(path.name for path in Path(directory).iterdir()),
                ["round-trip-task.json"],
            )

    async def test_resume_restarts_from_last_completed_step(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JsonCheckpointStore(directory)
            crashing_provider = StepAwareProvider(crash_before_step=4)

            with self.assertRaisesRegex(SimulatedProcessExit, "before step 4"):
                await _loop(crashing_provider, store).run(
                    "Calculate four small expressions.",
                    task_id="resume-task",
                )

            durable = store.load("resume-task")
            self.assertEqual(durable.status, AgentStatus.RUNNING)
            self.assertEqual(durable.step, 3)
            self.assertEqual(len(durable.state.tool_calls), 3)
            self.assertEqual(len(durable.state.tool_results), 3)
            self.assertEqual(len(durable.state.trajectory), 12)

            resumed_provider = StepAwareProvider()
            result = await _loop(resumed_provider, store).resume("resume-task")

            self.assertEqual(_current_step(resumed_provider.requests[0]), 4)
            self.assertEqual(result.status, AgentStatus.FINISHED)
            self.assertEqual(result.steps_executed, 5)
            self.assertEqual(result.tool_calls_executed, 4)
            self.assertEqual(
                result.final_answer,
                "Recovered and completed after four tool calls.",
            )
            self.assertEqual(store.load("resume-task").status, AgentStatus.FINISHED)

    async def test_terminal_checkpoint_returns_without_calling_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JsonCheckpointStore(directory)
            await _loop(StepAwareProvider(), store).run(
                "Calculate four small expressions.",
                task_id="already-finished",
            )
            unused_provider = StepAwareProvider(crash_before_step=1)

            result = await _loop(unused_provider, store).resume("already-finished")

            self.assertEqual(result.status, AgentStatus.FINISHED)
            self.assertEqual(unused_provider.requests, [])

    def test_missing_and_unsafe_task_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JsonCheckpointStore(directory)
            with self.assertRaises(CheckpointNotFoundError):
                store.load("missing-task")
            with self.assertRaises(InvalidCheckpointTaskId):
                store.load("../outside")

    def test_corrupt_and_unsupported_files_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "broken.json"
            checkpoint.write_text("not-json", encoding="utf-8")
            store = JsonCheckpointStore(directory)
            with self.assertRaises(CheckpointCorruptError):
                store.load("broken")

            checkpoint.write_text(
                json.dumps({"schema_version": 999}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                CheckpointCorruptError,
                "unsupported checkpoint schema_version",
            ):
                store.load("broken")

    async def test_checkpoint_task_id_must_match_its_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JsonCheckpointStore(directory)
            await _loop(StepAwareProvider(), store).run(
                "Calculate four small expressions.",
                task_id="original-task",
            )
            original = Path(directory) / "original-task.json"
            mismatched = Path(directory) / "different-task.json"
            mismatched.write_bytes(original.read_bytes())

            with self.assertRaisesRegex(
                CheckpointCorruptError,
                "does not match its filename",
            ):
                store.load("different-task")

    async def test_waiting_checkpoint_requires_a_pending_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JsonCheckpointStore(directory)
            await _loop(StepAwareProvider(), store).run(
                "Calculate four small expressions.",
                task_id="invalid-waiting-task",
            )
            path = Path(directory) / "invalid-waiting-task.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["status"] = "waiting_approval"
            payload["state"]["status"] = "waiting_approval"
            payload["state"]["pending_tool_call"] = None
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(
                CheckpointCorruptError,
                "requires a pending_tool_call",
            ):
                store.load("invalid-waiting-task")


if __name__ == "__main__":
    unittest.main()
