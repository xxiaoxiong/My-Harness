"""Run the HARN-02 agent loop with deterministic model decisions."""

import asyncio

from harness import (
    AgentLoop,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
)


class DemoDecisionProvider(ModelProvider):
    """Stand in for an LLM so the control flow can be studied offline."""

    def __init__(self) -> None:
        self._decisions = iter(("CONTINUE", "FINISH"))
        self.call_count = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.call_count += 1
        decision = next(self._decisions)
        print(f"LLM call {self.call_count} -> {decision}")
        return ModelResponse(
            response_id=f"demo-{self.call_count}",
            model=request.model,
            message=ModelMessage(MessageRole.ASSISTANT, decision),
            finish_reason="stop",
            usage=None,
            latency_ms=0.0,
        )


async def main() -> None:
    goal = "Understand how model output controls the next loop step"
    max_steps = 3
    provider = DemoDecisionProvider()
    loop = AgentLoop(provider, model="demo-model", max_steps=max_steps)

    print(f"Goal: {goal}")
    print(f"max_steps: {max_steps}")
    result = await loop.run(goal)

    print("\nAgentRunResult")
    print(f"  status:         {result.status.value}")
    print(f"  steps_executed: {result.steps_executed}")
    print(f"  last_decision:  {result.last_decision.value}")


if __name__ == "__main__":
    asyncio.run(main())
