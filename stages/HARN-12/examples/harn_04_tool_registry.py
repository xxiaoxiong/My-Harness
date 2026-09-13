"""Show definition, implementation, registry, and executor as separate layers."""

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


class RegistryAwareDemoProvider(ModelProvider):
    """Choose a registered definition, then answer from its observation."""

    def __init__(self) -> None:
        self.call_count = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.call_count += 1
        if self.call_count == 1:
            definitions_message = next(
                message
                for message in request.messages
                if message.content.startswith("Tool Definitions:\n")
            )
            definitions = json.loads(definitions_message.content.split("\n", 1)[1])
            selected_name = definitions[0]["name"]
            action = {
                "type": "tool_call",
                "name": selected_name,
                "arguments": {"expression": "144 / 12"},
            }
            print(f"Agent selected schema -> {selected_name}")
            print(f"LLM Tool Call -> {json.dumps(action, ensure_ascii=False)}")
        else:
            observation_message = next(
                message
                for message in reversed(request.messages)
                if '"type":"tool_result"' in message.content
            )
            observation = json.loads(observation_message.content)
            action = {
                "type": "final_answer",
                "answer": f"144 / 12 = {observation['result']}",
            }
            print(
                "Executor observation -> "
                f"name={observation['name']}, result={observation['result']}, "
                f"error={observation['error']}"
            )
            print(f"LLM Final Answer -> {action['answer']}")

        return ModelResponse(
            response_id=f"harn-04-demo-{self.call_count}",
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
    calculator = CalculatorTool()
    registry = ToolRegistry()
    registry.register(calculator)
    executor = ToolExecutor(registry)

    print(f"Tool Definition -> {calculator.schema.as_dict()}")
    print(f"Tool Implementation -> {type(calculator).__name__}")
    print(f"Registry Tools -> {[tool.schema.name for tool in registry.list_tools()]}")

    loop = ToolAgentLoop(
        RegistryAwareDemoProvider(),
        model="demo-model",
        max_steps=3,
        tool_executor=executor,
        context_builder=ContextBuilder(max_context_chars=4_000),
    )
    result = await loop.run("计算 144 / 12")

    print("\nToolAgentRunResult")
    print(f"  status:      {result.status.value}")
    print(f"  final_answer: {result.final_answer}")


if __name__ == "__main__":
    asyncio.run(main())
