import json
import logging
import unittest

from harness.context import ContextBuilder
from harness.hooks import (
    Hook,
    HookContext,
    HookExecutionError,
    HookManager,
    LoggingHook,
    MetricsHook,
)
from harness.model import (
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
)
from harness.runtime import ToolAgentLoop
from harness.state import AgentStatus
from harness.tools import CalculatorTool, ToolExecutor, ToolRegistry


class SequenceProvider(ModelProvider):
    def __init__(self, *actions: dict[str, object]) -> None:
        self._actions = list(actions)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if not self._actions:
            raise AssertionError("provider was called unexpectedly")
        action = self._actions.pop(0)
        return ModelResponse(
            response_id=f"hook-response-{len(self._actions)}",
            model=request.model,
            message=ModelMessage(
                MessageRole.ASSISTANT,
                json.dumps(action, separators=(",", ":")),
            ),
            finish_reason="stop",
            usage=None,
            latency_ms=0.0,
        )


class FailingProvider(ModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        raise RuntimeError("provider unavailable")


class RecordingHook(Hook):
    def __init__(self, label: str = "") -> None:
        self._label = label
        self.events: list[tuple[str, HookContext]] = []

    def _record(self, name: str, context: HookContext) -> None:
        self.events.append((f"{self._label}{name}", context))

    async def before_step(self, context: HookContext) -> None:
        self._record("before_step", context)

    async def after_step(self, context: HookContext) -> None:
        self._record("after_step", context)

    async def before_model_call(self, context: HookContext) -> None:
        self._record("before_model_call", context)

    async def after_model_call(self, context: HookContext) -> None:
        self._record("after_model_call", context)

    async def before_tool_call(self, context: HookContext) -> None:
        self._record("before_tool_call", context)

    async def after_tool_call(self, context: HookContext) -> None:
        self._record("after_tool_call", context)

    async def on_error(self, context: HookContext) -> None:
        self._record("on_error", context)


class FailingToolHook(Hook):
    async def before_tool_call(self, context: HookContext) -> None:
        raise RuntimeError("hook failed")


class FailingErrorHook(Hook):
    async def on_error(self, context: HookContext) -> None:
        raise RuntimeError("error observer failed")


def _tool_call(expression: str = "2 + 3") -> dict[str, object]:
    return {
        "type": "tool_call",
        "name": "calculator",
        "arguments": {"expression": expression},
    }


def _final() -> dict[str, object]:
    return {"type": "final_answer", "answer": "Done."}


def _loop(provider: ModelProvider, hooks: HookManager) -> ToolAgentLoop:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return ToolAgentLoop(
        provider,
        model="hook-demo-model",
        max_steps=3,
        tool_executor=ToolExecutor(registry),
        context_builder=ContextBuilder(max_context_chars=4_000),
        hook_manager=hooks,
    )


class HookLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_lifecycle_events_have_ordered_structured_context(self) -> None:
        recorder = RecordingHook()
        result = await _loop(
            SequenceProvider(_tool_call(), _final()),
            HookManager([recorder]),
        ).run("Calculate once.", task_id="hook-task")

        self.assertEqual(result.status, AgentStatus.FINISHED)
        self.assertEqual(
            [name for name, _ in recorder.events],
            [
                "before_step",
                "before_model_call",
                "after_model_call",
                "before_tool_call",
                "after_tool_call",
                "after_step",
                "before_step",
                "before_model_call",
                "after_model_call",
                "after_step",
            ],
        )
        contexts = [context for _, context in recorder.events]
        self.assertTrue(all(context.task_id == "hook-task" for context in contexts))
        self.assertEqual([context.step for context in contexts[:6]], [1] * 6)
        self.assertEqual([context.step for context in contexts[6:]], [2] * 4)
        self.assertIsNotNone(contexts[1].request)
        self.assertIsNotNone(contexts[2].response)
        self.assertEqual(contexts[3].tool_call.name, "calculator")  # type: ignore[union-attr]
        self.assertEqual(contexts[4].tool_result.result, 5)  # type: ignore[union-attr]
        self.assertEqual(contexts[-1].status, AgentStatus.FINISHED)

    async def test_hooks_run_sequentially_in_registration_order(self) -> None:
        observed: list[str] = []

        class OrderedHook(Hook):
            def __init__(self, label: str) -> None:
                self._label = label

            async def before_step(self, context: HookContext) -> None:
                observed.append(self._label)

        manager = HookManager()
        manager.register(OrderedHook("first"))
        manager.register(OrderedHook("second"))

        await _loop(SequenceProvider(_final()), manager).run("Finish.")

        self.assertEqual(observed, ["first", "second"])

    async def test_runtime_errors_emit_on_error_and_update_metrics(self) -> None:
        recorder = RecordingHook()
        metrics = MetricsHook()

        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            await _loop(
                FailingProvider(),
                HookManager([recorder, metrics]),
            ).run("Fail once.", task_id="error-task")

        self.assertEqual(
            [name for name, _ in recorder.events],
            ["before_step", "before_model_call", "on_error"],
        )
        error_context = recorder.events[-1][1]
        self.assertEqual(error_context.phase, "model_call")
        self.assertIsInstance(error_context.error, RuntimeError)
        snapshot = metrics.snapshot
        self.assertEqual(snapshot.steps_started, 1)
        self.assertEqual(snapshot.steps_completed, 0)
        self.assertEqual(snapshot.model_calls_started, 1)
        self.assertEqual(snapshot.model_calls_completed, 0)
        self.assertEqual(snapshot.errors, 1)

    async def test_hook_failures_are_named_and_observed_by_on_error(self) -> None:
        recorder = RecordingHook()

        with self.assertRaisesRegex(
            HookExecutionError,
            "FailingToolHook.before_tool_call failed",
        ):
            await _loop(
                SequenceProvider(_tool_call()),
                HookManager([FailingToolHook(), recorder]),
            ).run("Calculate once.", task_id="hook-error-task")

        self.assertEqual(recorder.events[-1][0], "on_error")
        self.assertEqual(recorder.events[-1][1].phase, "before_tool_call")
        self.assertIsInstance(recorder.events[-1][1].error, HookExecutionError)

    async def test_on_error_failure_does_not_hide_original_or_skip_later_hooks(
        self,
    ) -> None:
        recorder = RecordingHook()

        with self.assertRaisesRegex(RuntimeError, "provider unavailable") as raised:
            await _loop(
                FailingProvider(),
                HookManager([FailingErrorHook(), recorder]),
            ).run("Fail once.", task_id="double-error-task")

        self.assertEqual(recorder.events[-1][0], "on_error")
        self.assertTrue(
            any("on_error hook also failed" in note for note in raised.exception.__notes__)
        )

    async def test_logging_and_metrics_hooks_observe_without_changing_result(self) -> None:
        logger = logging.getLogger("harness.tests.hook")
        logger.setLevel(logging.INFO)
        logger.propagate = True
        metrics = MetricsHook()

        with self.assertLogs(logger, level="INFO") as captured:
            result = await _loop(
                SequenceProvider(_tool_call("1 / 0"), _final()),
                HookManager([LoggingHook(logger), metrics]),
            ).run("Observe a tool error.", task_id="observability-task")

        self.assertEqual(result.status, AgentStatus.FINISHED)
        self.assertIn("before_model_call", "\n".join(captured.output))
        self.assertIn("after_tool_call", "\n".join(captured.output))
        snapshot = metrics.snapshot
        self.assertEqual(snapshot.steps_started, 2)
        self.assertEqual(snapshot.steps_completed, 2)
        self.assertEqual(snapshot.model_calls_started, 2)
        self.assertEqual(snapshot.model_calls_completed, 2)
        self.assertEqual(snapshot.tool_calls_started, 1)
        self.assertEqual(snapshot.tool_calls_completed, 1)
        self.assertEqual(snapshot.errors, 0)
        self.assertGreaterEqual(snapshot.model_elapsed_ms, 0.0)
        self.assertGreaterEqual(snapshot.tool_elapsed_ms, 0.0)


if __name__ == "__main__":
    unittest.main()
