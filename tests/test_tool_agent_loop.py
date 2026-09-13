import json
import unittest

from harness.context import ContextBuilder
from harness.model import (
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
)
from harness.runtime import InvalidAgentAction, ToolAgentLoop
from harness.state import AgentStatus, TrajectoryEventKind
from harness.tools import CalculatorTool, ToolExecutor, ToolRegistry


class JsonSequenceProvider(ModelProvider):
    def __init__(self, *outputs: dict[str, object] | str) -> None:
        self._outputs = list(outputs)
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._outputs:
            raise AssertionError("the loop made more model calls than expected")

        value = self._outputs.pop(0)
        content = value if isinstance(value, str) else json.dumps(value)
        return ModelResponse(
            response_id=f"tool-response-{len(self.requests)}",
            model=request.model,
            message=ModelMessage(MessageRole.ASSISTANT, content),
            finish_reason="stop",
            usage=None,
            latency_ms=0.0,
        )


class ToolAgentLoopTests(unittest.IsolatedAsyncioTestCase):
    def _loop(
        self,
        provider: ModelProvider,
        *,
        max_steps: int,
    ) -> ToolAgentLoop:
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        return ToolAgentLoop(
            provider,
            model="demo-model",
            max_steps=max_steps,
            tool_executor=ToolExecutor(registry),
            context_builder=ContextBuilder(max_context_chars=4_000),
        )

    async def test_tool_result_is_observed_before_the_final_answer(self) -> None:
        provider = JsonSequenceProvider(
            {
                "type": "tool_call",
                "name": "calculator",
                "arguments": {"expression": "2 + 3 * 4"},
            },
            {"type": "final_answer", "answer": "The result is 14."},
        )
        loop = self._loop(provider, max_steps=3)

        result = await loop.run("Calculate 2 + 3 * 4", task_id="task-calculate")

        self.assertEqual(result.status, AgentStatus.FINISHED)
        self.assertEqual(result.steps_executed, 2)
        self.assertEqual(result.tool_calls_executed, 1)
        self.assertEqual(result.final_answer, "The result is 14.")
        self.assertIsNotNone(result.last_tool_result)
        self.assertEqual(result.last_tool_result.result, 14)  # type: ignore[union-attr]
        self.assertEqual(result.state.task_id, "task-calculate")
        self.assertEqual(len(result.state.messages), 3)
        self.assertEqual(result.state.tool_calls[0].name, "calculator")
        self.assertEqual(result.state.tool_results[0].result, 14)
        self.assertEqual(
            [event.kind for event in result.state.trajectory],
            [
                TrajectoryEventKind.MODEL_CALL,
                TrajectoryEventKind.TOOL_CALL,
                TrajectoryEventKind.PERMISSION_DECISION,
                TrajectoryEventKind.TOOL_RESULT,
                TrajectoryEventKind.MODEL_CALL,
                TrajectoryEventKind.FINAL_ANSWER,
            ],
        )

        first_request, second_request = provider.requests
        self.assertEqual(len(first_request.messages), 4)
        self.assertIn('"name":"calculator"', first_request.messages[2].content)
        self.assertIn('"parameters"', first_request.messages[2].content)
        self.assertEqual(len(second_request.messages), 6)
        observation = json.loads(second_request.messages[3].content)
        self.assertEqual(
            observation,
            {
                "arguments": {"expression": "2 + 3 * 4"},
                "error": None,
                "name": "calculator",
                "result": 14,
                "type": "tool_result",
            },
        )

    async def test_tool_error_is_returned_to_the_model_for_recovery(self) -> None:
        provider = JsonSequenceProvider(
            {
                "type": "tool_call",
                "name": "calculator",
                "arguments": {"expression": "1 / 0"},
            },
            {
                "type": "final_answer",
                "answer": "The expression is undefined because it divides by zero.",
            },
        )
        loop = self._loop(provider, max_steps=3)

        result = await loop.run("Calculate 1 / 0")

        observation = json.loads(provider.requests[1].messages[3].content)
        self.assertEqual(observation["error"], "division by zero")
        self.assertIsNone(observation["result"])
        self.assertEqual(result.status, AgentStatus.FINISHED)

    async def test_max_steps_stops_after_a_tool_call(self) -> None:
        provider = JsonSequenceProvider(
            {
                "type": "tool_call",
                "name": "calculator",
                "arguments": {"expression": "40 + 2"},
            }
        )
        loop = self._loop(provider, max_steps=1)

        result = await loop.run("Calculate 40 + 2")

        self.assertEqual(result.status, AgentStatus.MAX_STEPS_REACHED)
        self.assertEqual(result.steps_executed, 1)
        self.assertEqual(result.tool_calls_executed, 1)
        self.assertIsNone(result.final_answer)
        self.assertEqual(result.last_tool_result.result, 42)  # type: ignore[union-attr]
        self.assertEqual(
            result.state.trajectory[-1].kind,
            TrajectoryEventKind.MAX_STEPS_REACHED,
        )

    async def test_invalid_json_action_is_rejected_before_tool_execution(self) -> None:
        loop = self._loop(JsonSequenceProvider("calculator(2 + 2)"), max_steps=2)

        with self.assertRaisesRegex(InvalidAgentAction, "valid JSON"):
            await loop.run("Calculate 2 + 2")

    async def test_action_schema_rejects_extra_fields(self) -> None:
        loop = self._loop(
            JsonSequenceProvider(
                {
                    "type": "tool_call",
                    "name": "calculator",
                    "arguments": {"expression": "2 + 2"},
                    "surprise": True,
                }
            ),
            max_steps=2,
        )

        with self.assertRaisesRegex(InvalidAgentAction, "contain only"):
            await loop.run("Calculate 2 + 2")


if __name__ == "__main__":
    unittest.main()
