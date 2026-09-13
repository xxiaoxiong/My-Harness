"""HARN-10 Demo: observe one Agent run with Logging and Metrics Hooks."""

from __future__ import annotations

import asyncio
import json

from harness import (
    CalculatorTool,
    ContextBuilder,
    HookManager,
    LoggingHook,
    MessageRole,
    MetricsHook,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ToolAgentLoop,
    ToolExecutor,
    ToolRegistry,
    configure_logging,
    load_config,
)


class HookDemoProvider(ModelProvider):
    def __init__(self) -> None:
        self._call_number = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self._call_number += 1
        if self._call_number == 1:
            action: dict[str, object] = {
                "type": "tool_call",
                "name": "calculator",
                "arguments": {"expression": "6 * 7"},
            }
        else:
            action = {
                "type": "final_answer",
                "answer": "The observed calculator result is 42.",
            }
        return ModelResponse(
            response_id=f"harn-10-response-{self._call_number}",
            model=request.model,
            message=ModelMessage(
                MessageRole.ASSISTANT,
                json.dumps(action, separators=(",", ":")),
            ),
            finish_reason="stop",
            usage=None,
            latency_ms=0.0,
        )


async def main() -> None:
    configure_logging(load_config())
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    metrics = MetricsHook()
    hooks = HookManager()
    hooks.register(LoggingHook())
    hooks.register(metrics)
    loop = ToolAgentLoop(
        HookDemoProvider(),
        model="hook-demo-model",
        max_steps=3,
        tool_executor=ToolExecutor(registry),
        context_builder=ContextBuilder(max_context_chars=4_000),
        hook_manager=hooks,
    )

    result = await loop.run("Calculate 6 * 7.", task_id="harn-10-demo")
    snapshot = metrics.snapshot
    print("\nAgent result")
    print(f"  status:       {result.status.value}")
    print(f"  final_answer: {result.final_answer}")
    print("\nMetricsHook snapshot")
    print(
        f"  steps:        {snapshot.steps_completed}/"
        f"{snapshot.steps_started} completed"
    )
    print(
        f"  model calls:  {snapshot.model_calls_completed}/"
        f"{snapshot.model_calls_started} completed"
    )
    print(
        f"  tool calls:   {snapshot.tool_calls_completed}/"
        f"{snapshot.tool_calls_started} completed"
    )
    print(f"  errors:       {snapshot.errors}")
    print(f"  model time:   {snapshot.model_elapsed_ms:.3f} ms")
    print(f"  tool time:    {snapshot.tool_elapsed_ms:.3f} ms")


if __name__ == "__main__":
    asyncio.run(main())
