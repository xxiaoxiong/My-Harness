"""HARN-14 Demo: local and MCP Tools use the same Agent execution path."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence

from harness import (
    CalculatorTool,
    Harness,
    MCPCallResult,
    MCPClient,
    MCPClientAdapter,
    MCPToolDefinition,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
)
from harness.core import JsonValue


class DemoMCPClient(MCPClient):
    """A deterministic stand-in for an SDK connected to an MCP server."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, JsonValue]]] = []

    async def list_tools(self) -> Sequence[MCPToolDefinition]:
        return (
            MCPToolDefinition(
                name="remote_uppercase",
                description="Uppercase text on the Demo MCP server.",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            ),
        )

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
    ) -> MCPCallResult:
        self.calls.append((name, dict(arguments)))
        if name != "remote_uppercase":
            return MCPCallResult(f"unknown remote tool: {name}", is_error=True)
        text = arguments.get("text")
        if not isinstance(text, str):
            return MCPCallResult("text must be a string", is_error=True)
        return MCPCallResult({"uppercase": text.upper()})


class MixedToolProvider(ModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        step = _current_step(request)
        actions: dict[int, dict[str, object]] = {
            1: {
                "type": "tool_call",
                "name": "calculator",
                "arguments": {"expression": "21 * 2"},
            },
            2: {
                "type": "tool_call",
                "name": "remote_uppercase",
                "arguments": {"text": "same agent loop"},
            },
        }
        action = actions.get(
            step,
            {
                "type": "final_answer",
                "answer": "Local result 42; remote result SAME AGENT LOOP.",
            },
        )
        return ModelResponse(
            response_id=f"harn-14-response-{step}",
            model=request.model,
            message=ModelMessage(
                MessageRole.ASSISTANT,
                json.dumps(action, separators=(",", ":")),
            ),
            finish_reason="stop",
            usage=None,
            latency_ms=0.0,
        )


def _current_step(request: ModelRequest) -> int:
    state_message = next(
        message.content
        for message in request.messages
        if message.content.startswith("Current State:\n")
    )
    return int(json.loads(state_message.split("\n", 1)[1])["current_step"])


async def main() -> None:
    client = DemoMCPClient()
    adapter = MCPClientAdapter(client, server_name="demo-mcp-server")
    remote_tools = await adapter.discover_tools()

    harness = Harness()
    harness.register_tool(CalculatorTool())
    for tool in remote_tools:
        harness.register_tool(tool)

    print(
        "Registered Tools: "
        + ", ".join(
            f"{tool.schema.name} ({type(tool).__name__})" for tool in harness.tools
        )
    )
    result = await harness.create_agent_loop(
        MixedToolProvider(),
        model="mcp-demo-model",
        max_steps=4,
        max_context_chars=8_000,
    ).run(
        "Use a local calculation and remote text transformation.",
        task_id="harn-14-demo",
    )

    for observation in result.state.tool_results:
        print(
            f"Observation: name={observation.name}, "
            f"result={observation.result}, error={observation.error}"
        )
    print(f"MCP Client calls: {client.calls}")
    print(f"Final answer: {result.final_answer}")


if __name__ == "__main__":
    asyncio.run(main())
