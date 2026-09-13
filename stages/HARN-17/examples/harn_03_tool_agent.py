"""Show the HARN-03 reason-act-observe loop without network access."""

import asyncio
import json

from harness import (
    CalculatorTool,
    ContextBuilder,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ToolAgentLoop,
    ToolExecutor,
    ToolRegistry,
)


class DemoToolCallingProvider(ModelProvider):
    """Script one calculator call followed by an answer using its result."""

    def __init__(self) -> None:
        self.call_count = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.call_count += 1
        if self.call_count == 1:
            action = {
                "type": "tool_call",
                "name": "calculator",
                "arguments": {"expression": "(12 + 8) * 3"},
            }
            print(f"LLM -> Tool Call: {json.dumps(action, ensure_ascii=False)}")
        else:
            observation_message = next(
                message
                for message in reversed(request.messages)
                if '"type":"tool_result"' in message.content
            )
            observation = json.loads(observation_message.content)
            print(f"Tool Executor -> {observation['name']}")
            print(
                "Tool Result -> "
                f"result={observation['result']}, error={observation['error']}"
            )
            action = {
                "type": "final_answer",
                "answer": f"(12 + 8) * 3 = {observation['result']}",
            }
            print(f"LLM -> Final Answer: {action['answer']}")

        return ModelResponse(
            response_id=f"harn-03-demo-{self.call_count}",
            model=request.model,
            message=ModelMessage(
                MessageRole.ASSISTANT,
                json.dumps(action, ensure_ascii=False),
            ),
            finish_reason="stop",
            usage=None,
            latency_ms=0.0,
        )


async def main() -> None:
    goal = "计算 (12 + 8) * 3"
    print(f"User -> {goal}")

    registry = ToolRegistry()
    registry.register(CalculatorTool())

    loop = ToolAgentLoop(
        DemoToolCallingProvider(),
        model="demo-model",
        max_steps=3,
        tool_executor=ToolExecutor(registry),
        context_builder=ContextBuilder(max_context_chars=4_000),
    )
    result = await loop.run(goal)

    print("\nToolAgentRunResult")
    print(f"  status:              {result.status.value}")
    print(f"  steps_executed:      {result.steps_executed}")
    print(f"  tool_calls_executed: {result.tool_calls_executed}")
    print(f"  final_answer:         {result.final_answer}")


if __name__ == "__main__":
    asyncio.run(main())
