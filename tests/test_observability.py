import asyncio
import io
import json
import logging
import unittest

from harness import (
    AgentTask,
    AgentWorker,
    CalculatorTool,
    ContextBuilder,
    FailureLayer,
    Hook,
    HookExecutionError,
    HookManager,
    InMemoryTaskStore,
    InMemoryTraceRecorder,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    PermissionDecision,
    RetryPolicy,
    StructuredLogTraceRecorder,
    StaticPolicyEngine,
    TaskScheduler,
    TaskStatus,
    TokenUsage,
    ToolAgentLoop,
    ToolExecutor,
    ToolRegistry,
    TraceEmitter,
    TraceEventKind,
    TraceIdentity,
    TracingHook,
    TracingRetryObserver,
    TracingTaskStore,
    classify_failure,
    format_trace_tree,
)
from harness.model import ModelTransportError


class TransientToolProvider(ModelProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            raise ModelTransportError("temporary upstream disconnect")
        if _current_step(request) == 1:
            action: dict[str, object] = {
                "type": "tool_call",
                "name": "calculator",
                "arguments": {"expression": "6 * 7"},
            }
        else:
            action = {"type": "final_answer", "answer": "The answer is 42."}
        return ModelResponse(
            response_id=f"observable-{self.calls}",
            model=request.model,
            message=ModelMessage(
                MessageRole.ASSISTANT,
                json.dumps(action, separators=(",", ":")),
            ),
            finish_reason="stop",
            usage=TokenUsage(20, 8, 28),
            latency_ms=12.5,
            ttft_ms=3.25,
        )


def _current_step(request: ModelRequest) -> int:
    state = next(
        message.content
        for message in request.messages
        if message.content.startswith("Current State:\n")
    )
    return int(json.loads(state.split("\n", 1)[1])["current_step"])


def _identity(task: AgentTask) -> TraceIdentity:
    return TraceIdentity(task.task_id, task.trace_id, task.session_id)


class IdentityAndRecorderTests(unittest.TestCase):
    def test_task_generates_correlation_ids_and_accepts_a_shared_session(self) -> None:
        first = AgentTask.create("first", session_id="conversation-7")
        second = AgentTask.create("second", session_id="conversation-7")

        self.assertTrue(first.task_id)
        self.assertTrue(first.trace_id)
        self.assertEqual(first.session_id, "conversation-7")
        self.assertNotEqual(first.task_id, second.task_id)
        self.assertNotEqual(first.trace_id, second.trace_id)
        self.assertEqual(first.session_id, second.session_id)

    def test_structured_log_recorder_emits_one_json_object(self) -> None:
        stream = io.StringIO()
        logger = logging.Logger("trace-test", level=logging.INFO)
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        identity = TraceIdentity("task-1", "trace-1", "session-1")

        StructuredLogTraceRecorder(logger).record(
            TraceEmitter(identity, InMemoryTraceRecorder()).emit(
                TraceEventKind.RETRY,
                details={"failed_attempt": 1},
            )
        )

        record = json.loads(stream.getvalue())
        self.assertEqual(record["kind"], "task.retry")
        self.assertEqual(record["trace_id"], "trace-1")
        self.assertEqual(record["details"]["failed_attempt"], 1)

    def test_model_response_validates_ttft(self) -> None:
        with self.assertRaises(ValueError):
            ModelResponse(
                response_id="response-1",
                model="demo",
                message=ModelMessage(MessageRole.ASSISTANT, "done"),
                finish_reason="stop",
                usage=None,
                latency_ms=1,
                ttft_ms=-1,
            )


class TaskTracingTests(unittest.TestCase):
    def test_store_records_only_persisted_state_transitions(self) -> None:
        recorder = InMemoryTraceRecorder()
        store = TracingTaskStore(InMemoryTaskStore(), recorder)
        task = AgentTask.create(
            "observe lifecycle",
            task_id="task-state",
            trace_id="trace-state",
            session_id="session-state",
        )

        store.create(task)
        running = task.mark_running()
        store.update(running)
        store.update(running.record_attempt())
        store.update(running.record_attempt().mark_completed("done"))

        transitions = [
            event
            for event in recorder.events
            if event.kind is TraceEventKind.TASK_STATE_TRANSITION
        ]
        self.assertEqual(
            [(event.details["from"], event.details["to"]) for event in transitions],
            [(None, "pending"), ("pending", "running"), ("running", "completed")],
        )
        self.assertTrue(all(event.identity == _identity(task) for event in transitions))

    def test_failure_classifier_covers_diagnostic_layers(self) -> None:
        self.assertIs(
            classify_failure(ModelTransportError("offline")),
            FailureLayer.MODEL,
        )
        self.assertIs(
            classify_failure(ValueError("bad JSON"), phase="parse_action"),
            FailureLayer.PROMPT,
        )
        self.assertIs(
            classify_failure(ValueError("budget"), phase="context_build"),
            FailureLayer.CONTEXT,
        )
        self.assertIs(
            classify_failure(ConnectionError("DNS")),
            FailureLayer.INFRASTRUCTURE,
        )
        hook_error = HookExecutionError("before_model_call", Hook(), ValueError("bad"))
        self.assertIs(
            classify_failure(hook_error, phase="before_model_call"),
            FailureLayer.HARNESS,
        )


class EndToEndObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_tree_records_retry_model_tool_metrics_and_state(self) -> None:
        recorder = InMemoryTraceRecorder()
        store = TracingTaskStore(InMemoryTaskStore(), recorder)
        provider = TransientToolProvider()
        registry = ToolRegistry()
        registry.register(CalculatorTool())

        def loop_factory(task: AgentTask) -> ToolAgentLoop:
            hook = TracingHook(_identity(task), recorder)
            return ToolAgentLoop(
                provider,
                model="observable-model",
                max_steps=3,
                tool_executor=ToolExecutor(registry),
                context_builder=ContextBuilder(max_context_chars=8_000),
                hook_manager=HookManager([hook]),
            )

        worker = AgentWorker(store, loop_factory)
        scheduler = TaskScheduler(
            store,
            worker,
            retry_policy=RetryPolicy(
                max_attempts=2,
                base_delay_seconds=0,
                max_delay_seconds=0,
            ),
            sleeper=lambda _: asyncio.sleep(0),
            retry_observer=TracingRetryObserver(recorder),
        )
        submitted = scheduler.submit(
            "Calculate six times seven",
            task_id="observable-task",
            trace_id="observable-trace",
            session_id="observable-session",
        )

        await scheduler.run_until_idle()

        completed = store.get(submitted.task_id)
        self.assertIs(completed.status, TaskStatus.COMPLETED)
        self.assertEqual(completed.attempts, 2)
        self.assertEqual(completed.final_answer, "The answer is 42.")
        kinds = {event.kind for event in recorder.events}
        self.assertTrue(set(TraceEventKind).issubset(kinds))

        retry = next(
            event for event in recorder.events if event.kind is TraceEventKind.RETRY
        )
        self.assertEqual(retry.details["failed_attempt"], 1)
        self.assertEqual(retry.details["layer"], "model")
        error = next(
            event for event in recorder.events if event.kind is TraceEventKind.ERROR
        )
        self.assertEqual(error.details["phase"], "model_call")
        self.assertEqual(error.details["layer"], "model")

        tool_result = next(
            event
            for event in recorder.events
            if event.kind is TraceEventKind.TOOL_RESULT
        )
        self.assertEqual(tool_result.details["result"], 42)
        token_usage = next(
            event
            for event in recorder.events
            if event.kind is TraceEventKind.TOKEN_USAGE
        )
        self.assertEqual(token_usage.details["total_tokens"], 28)
        ttft = next(
            event for event in recorder.events if event.kind is TraceEventKind.TTFT
        )
        self.assertEqual(ttft.details["milliseconds"], 3.25)

        tree = format_trace_tree(recorder.events)
        self.assertEqual(tree["task_id"], "observable-task")
        self.assertEqual([step["step"] for step in tree["steps"]], [1, 2])
        self.assertTrue(tree["steps"][0]["model"])
        self.assertTrue(tree["steps"][0]["tools"])
        self.assertTrue(tree["events"])

    async def test_denied_tool_intent_and_result_are_both_recorded(self) -> None:
        recorder = InMemoryTraceRecorder()
        provider = TransientToolProvider()
        provider.calls = 1
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        identity = TraceIdentity("denied-task", "denied-trace", "denied-session")
        loop = ToolAgentLoop(
            provider,
            model="observable-model",
            max_steps=3,
            tool_executor=ToolExecutor(registry),
            context_builder=ContextBuilder(max_context_chars=8_000),
            hook_manager=HookManager([TracingHook(identity, recorder)]),
            policy_engine=StaticPolicyEngine(
                {"calculator": PermissionDecision.DENY}
            ),
        )

        result = await loop.run("Do not execute this", task_id="denied-task")

        self.assertEqual(result.final_answer, "The answer is 42.")
        calls = [
            event for event in recorder.events if event.kind is TraceEventKind.TOOL_CALL
        ]
        results = [
            event
            for event in recorder.events
            if event.kind is TraceEventKind.TOOL_RESULT
        ]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].details["succeeded"])
        self.assertIn("denied", results[0].details["error"])
        self.assertTrue(
            any(
                event.kind is TraceEventKind.ERROR
                and event.details["layer"] == "tool"
                for event in recorder.events
            )
        )


if __name__ == "__main__":
    unittest.main()
