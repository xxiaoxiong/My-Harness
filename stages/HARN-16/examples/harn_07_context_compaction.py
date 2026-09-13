"""Run a long tool session and compact old context without deleting State."""

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
    SimpleHistorySummarizer,
    ToolAgentLoop,
    ToolExecutor,
    ToolRegistry,
)


class LongSessionProvider(ModelProvider):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self._expressions = ("1 + 1", "2 + 2", "3 + 3", "4 + 4")

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        call_number = len(self.requests)
        if call_number <= len(self._expressions):
            action = {
                "type": "tool_call",
                "name": "calculator",
                "arguments": {"expression": self._expressions[call_number - 1]},
            }
        else:
            latest_result_message = next(
                message
                for message in reversed(request.messages)
                if '"type":"tool_result"' in message.content
            )
            latest_result = json.loads(latest_result_message.content)["result"]
            action = {
                "type": "final_answer",
                "answer": f"Completed four calculations; the latest result is {latest_result}.",
            }

        return ModelResponse(
            response_id=f"compact-demo-{call_number}",
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
    max_context_chars = 1_300
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    provider = LongSessionProvider()
    builder = ContextBuilder(
        max_context_chars=max_context_chars,
        summarizer=SimpleHistorySummarizer(),
        recent_history_messages=2,
        summary_max_chars=160,
    )
    loop = ToolAgentLoop(
        provider,
        model="demo-model",
        max_steps=6,
        tool_executor=ToolExecutor(registry),
        context_builder=builder,
    )

    run = await loop.run("依次计算 1+1、2+2、3+3、4+4")

    print("Context per model call")
    for number, request in enumerate(provider.requests, start=1):
        context_chars = sum(len(message.content) for message in request.messages)
        has_summary = any(
            message.content.startswith("History Summary:\n")
            for message in request.messages
        )
        print(
            f"  call={number} chars={context_chars}/{max_context_chars} "
            f"summary={has_summary}"
        )

    print("\nFull Agent State remains")
    print(f"  status:            {run.state.status.value}")
    print(f"  state_messages:    {len(run.state.messages)}")
    print(f"  tool_calls:        {len(run.state.tool_calls)}")
    print(f"  tool_results:      {len(run.state.tool_results)}")
    print(f"  trajectory_events: {len(run.state.trajectory)}")
    print(f"  final_answer:      {run.state.final_answer}")


if __name__ == "__main__":
    asyncio.run(main())
