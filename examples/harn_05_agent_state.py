"""Contrast persistent run state, model context snapshots, and trajectory."""

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


class StateDemoProvider(ModelProvider):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            action = {
                "type": "tool_call",
                "name": "calculator",
                "arguments": {"expression": "21 * 2"},
            }
        else:
            observation_message = next(
                message
                for message in reversed(request.messages)
                if '"type":"tool_result"' in message.content
            )
            observation = json.loads(observation_message.content)
            action = {
                "type": "final_answer",
                "answer": f"21 * 2 = {observation['result']}",
            }

        return ModelResponse(
            response_id=f"state-demo-{len(self.requests)}",
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
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    provider = StateDemoProvider()
    loop = ToolAgentLoop(
        provider,
        model="demo-model",
        max_steps=3,
        tool_executor=ToolExecutor(registry),
        context_builder=ContextBuilder(max_context_chars=4_000),
    )

    run = await loop.run("计算 21 * 2", task_id="harn-05-demo")
    state = run.state

    print("Agent State (system truth)")
    print(f"  task_id:       {state.task_id}")
    print(f"  goal:          {state.goal}")
    print(f"  current_step:  {state.current_step}")
    print(f"  status:        {state.status.value}")
    print(f"  messages:      {len(state.messages)}")
    print(f"  tool_calls:    {len(state.tool_calls)}")
    print(f"  tool_results:  {len(state.tool_results)}")
    print(f"  created_at:    {state.created_at.isoformat()}")
    print(f"  updated_at:    {state.updated_at.isoformat()}")

    print("\nContext snapshots (one ModelRequest at a time)")
    for number, request in enumerate(provider.requests, start=1):
        print(
            f"  model_call={number} messages={len(request.messages)} "
            f"last_role={request.messages[-1].role.value}"
        )

    print("\nTrajectory (ordered execution path)")
    for event in state.trajectory:
        print(f"  {event.sequence}. {event.kind.value}: {dict(event.details)}")


if __name__ == "__main__":
    asyncio.run(main())
