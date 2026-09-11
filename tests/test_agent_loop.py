import unittest

from harness.model import (
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
)
from harness.runtime import (
    AgentDecision,
    AgentLoop,
    AgentRunStatus,
    InvalidAgentDecision,
)


class SequenceProvider(ModelProvider):
    """Return scripted decisions while retaining every request for assertions."""

    def __init__(self, *outputs: str) -> None:
        self._outputs = list(outputs)
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._outputs:
            raise AssertionError("the loop made more model calls than expected")

        output = self._outputs.pop(0)
        return ModelResponse(
            response_id=f"response-{len(self.requests)}",
            model=request.model,
            message=ModelMessage(MessageRole.ASSISTANT, output),
            finish_reason="stop",
            usage=None,
            latency_ms=0.0,
        )


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_continue_is_fed_back_before_finish(self) -> None:
        provider = SequenceProvider("CONTINUE", "FINISH")
        loop = AgentLoop(provider, model=" demo-model ", max_steps=4)

        result = await loop.run(" Explain the loop ")

        self.assertEqual(result.goal, "Explain the loop")
        self.assertEqual(result.status, AgentRunStatus.FINISHED)
        self.assertEqual(result.steps_executed, 2)
        self.assertEqual(result.last_decision, AgentDecision.FINISH)
        self.assertEqual(len(provider.requests), 2)

        first_request, second_request = provider.requests
        self.assertEqual(first_request.model, "demo-model")
        self.assertEqual(len(first_request.messages), 2)
        self.assertIn("Step: 1 of at most 4", first_request.messages[-1].content)
        self.assertEqual(len(second_request.messages), 4)
        self.assertEqual(
            second_request.messages[-2],
            ModelMessage(MessageRole.ASSISTANT, "CONTINUE"),
        )
        self.assertIn("Step: 2 of at most 4", second_request.messages[-1].content)

    async def test_max_steps_stops_repeated_continue_decisions(self) -> None:
        provider = SequenceProvider("CONTINUE", "CONTINUE", "FINISH")
        loop = AgentLoop(provider, model="demo-model", max_steps=2)

        result = await loop.run("A goal that is not done yet")

        self.assertEqual(result.status, AgentRunStatus.MAX_STEPS_REACHED)
        self.assertEqual(result.steps_executed, 2)
        self.assertEqual(result.last_decision, AgentDecision.CONTINUE)
        self.assertEqual(len(provider.requests), 2)

    async def test_surrounding_whitespace_does_not_change_a_decision(self) -> None:
        loop = AgentLoop(
            SequenceProvider("\nFINISH \t"),
            model="demo-model",
            max_steps=1,
        )

        result = await loop.run("Finish now")

        self.assertEqual(result.status, AgentRunStatus.FINISHED)

    async def test_invalid_model_output_is_rejected(self) -> None:
        loop = AgentLoop(
            SequenceProvider("FINISH."),
            model="demo-model",
            max_steps=1,
        )

        with self.assertRaises(InvalidAgentDecision) as raised:
            await loop.run("Reject prose")

        self.assertEqual(raised.exception.output, "FINISH.")

    async def test_empty_goal_is_rejected_before_calling_provider(self) -> None:
        provider = SequenceProvider("FINISH")
        loop = AgentLoop(provider, model="demo-model", max_steps=1)

        with self.assertRaisesRegex(ValueError, "goal"):
            await loop.run("   ")

        self.assertEqual(provider.requests, [])

    def test_constructor_requires_a_nonempty_model_and_positive_max_steps(self) -> None:
        provider = SequenceProvider("FINISH")

        with self.assertRaisesRegex(ValueError, "model"):
            AgentLoop(provider, model=" ", max_steps=1)
        with self.assertRaisesRegex(ValueError, "max_steps"):
            AgentLoop(provider, model="demo-model", max_steps=0)
        with self.assertRaisesRegex(TypeError, "max_steps"):
            AgentLoop(provider, model="demo-model", max_steps=True)


if __name__ == "__main__":
    unittest.main()
