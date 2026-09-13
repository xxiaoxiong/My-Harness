import json
import unittest
from collections.abc import Mapping, Sequence

from harness import (
    CalculatorTool,
    DuplicateToolError,
    Harness,
    MCPCallResult,
    MCPClient,
    MCPClientAdapter,
    MCPClientError,
    MCPProtocolError,
    MCPToolAdapter,
    MCPToolDefinition,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolExecutor,
    ToolRegistry,
)
from harness.core import JsonValue


class FakeMCPClient(MCPClient):
    def __init__(
        self,
        definitions: Sequence[MCPToolDefinition],
        *,
        result: MCPCallResult | None = None,
        error: MCPClientError | None = None,
    ) -> None:
        self.definitions = definitions
        self.result = result or MCPCallResult({"remote": True})
        self.error = error
        self.list_calls = 0
        self.tool_calls: list[tuple[str, dict[str, JsonValue]]] = []

    async def list_tools(self) -> Sequence[MCPToolDefinition]:
        self.list_calls += 1
        return self.definitions

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
    ) -> MCPCallResult:
        self.tool_calls.append((name, dict(arguments)))
        if self.error is not None:
            raise self.error
        return self.result


class MixedToolProvider(ModelProvider):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        state_message = next(
            message.content
            for message in request.messages
            if message.content.startswith("Current State:\n")
        )
        step = json.loads(state_message.split("\n", 1)[1])["current_step"]
        actions: dict[int, dict[str, object]] = {
            1: {
                "type": "tool_call",
                "name": "calculator",
                "arguments": {"expression": "6 * 7"},
            },
            2: {
                "type": "tool_call",
                "name": "remote_echo",
                "arguments": {"text": "hello"},
            },
        }
        action = actions.get(
            step,
            {
                "type": "final_answer",
                "answer": "Local and MCP observations used the same loop.",
            },
        )
        return ModelResponse(
            response_id=f"mixed-response-{step}",
            model=request.model,
            message=ModelMessage(
                MessageRole.ASSISTANT,
                json.dumps(action, separators=(",", ":")),
            ),
            finish_reason="stop",
            usage=None,
            latency_ms=0.0,
        )


def _definition(name: str = "remote_echo") -> MCPToolDefinition:
    return MCPToolDefinition(
        name=name,
        description="Echo text on a remote MCP server.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )


class MCPTypesTests(unittest.TestCase):
    def test_tool_definition_normalizes_provider_neutral_schema(self) -> None:
        definition = MCPToolDefinition(
            name=" remote_echo ",
            input_schema={"type": "object"},
            description=" ",
        )

        self.assertEqual(definition.name, "remote_echo")
        self.assertIsNone(definition.description)
        self.assertEqual(definition.input_schema, {"type": "object"})
        with self.assertRaises(ValueError):
            MCPToolDefinition(name=" ", input_schema={})
        with self.assertRaises(TypeError):
            MCPCallResult(content=None, is_error="yes")  # type: ignore[arg-type]


class MCPAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovers_schema_and_delegates_remote_call(self) -> None:
        client = FakeMCPClient(
            [_definition()],
            result=MCPCallResult({"echo": "hello"}),
        )
        adapter = MCPClientAdapter(client, server_name="demo-server")

        tools = await adapter.discover_tools()
        result = await tools[0].execute({"text": "hello"})

        self.assertEqual(len(tools), 1)
        self.assertIsInstance(tools[0], MCPToolAdapter)
        self.assertEqual(tools[0].schema.name, "remote_echo")
        self.assertEqual(tools[0].server_name, "demo-server")
        self.assertEqual(result, {"echo": "hello"})
        self.assertEqual(client.list_calls, 1)
        self.assertEqual(client.tool_calls, [("remote_echo", {"text": "hello"})])

    async def test_local_and_mcp_tools_share_registry_and_executor(self) -> None:
        client = FakeMCPClient(
            [_definition()],
            result=MCPCallResult({"echo": "remote"}),
        )
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        remote_tools = await MCPClientAdapter(
            client,
            server_name="demo-server",
        ).register_tools(registry)
        executor = ToolExecutor(registry)

        local = await executor.execute(
            ToolCall("calculator", {"expression": "20 + 22"})
        )
        remote = await executor.execute(
            ToolCall("remote_echo", {"text": "remote"})
        )

        self.assertEqual(local.result, 42)
        self.assertEqual(remote.result, {"echo": "remote"})
        self.assertEqual(
            [schema.name for schema in executor.list_schemas()],
            ["calculator", "remote_echo"],
        )
        self.assertIs(registry.get("remote_echo"), remote_tools[0])

    async def test_remote_and_transport_errors_become_tool_observations(self) -> None:
        remote_error_client = FakeMCPClient(
            [_definition()],
            result=MCPCallResult({"message": "not available"}, is_error=True),
        )
        transport_error_client = FakeMCPClient(
            [_definition()],
            error=MCPClientError("connection closed"),
        )

        async def execute(client: MCPClient) -> str | None:
            registry = ToolRegistry()
            await MCPClientAdapter(
                client,
                server_name="demo-server",
            ).register_tools(registry)
            result = await ToolExecutor(registry).execute(
                ToolCall("remote_echo", {"text": "hello"})
            )
            return result.error

        self.assertIn("not available", await execute(remote_error_client))
        self.assertIn("connection closed", await execute(transport_error_client))

    async def test_invalid_discovery_and_call_results_are_rejected(self) -> None:
        duplicate_client = FakeMCPClient(
            [_definition(), _definition()],
        )
        with self.assertRaisesRegex(MCPProtocolError, "duplicate"):
            await MCPClientAdapter(
                duplicate_client,
                server_name="bad-server",
            ).discover_tools()

        class InvalidResultClient(FakeMCPClient):
            async def call_tool(  # type: ignore[override]
                self,
                name: str,
                arguments: Mapping[str, JsonValue],
            ) -> object:
                return {"not": "an MCPCallResult"}

        invalid_client = InvalidResultClient([_definition()])
        registry = ToolRegistry()
        await MCPClientAdapter(
            invalid_client,
            server_name="bad-server",
        ).register_tools(registry)
        result = await ToolExecutor(registry).execute(
            ToolCall("remote_echo", {"text": "hello"})
        )
        self.assertIn("must return an MCPCallResult", result.error)

    async def test_registration_preflights_local_name_collisions(self) -> None:
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        client = FakeMCPClient(
            [_definition("new_remote"), _definition("calculator")]
        )

        with self.assertRaisesRegex(DuplicateToolError, "existing tool"):
            await MCPClientAdapter(
                client,
                server_name="conflicting-server",
            ).register_tools(registry)

        self.assertEqual(
            [tool.schema.name for tool in registry.list_tools()],
            ["calculator"],
        )

    async def test_agent_loop_needs_no_mcp_specific_branch(self) -> None:
        client = FakeMCPClient(
            [_definition()],
            result=MCPCallResult({"echo": "hello"}),
        )
        adapter = MCPClientAdapter(client, server_name="demo-server")
        harness = Harness()
        harness.register_tool(CalculatorTool())
        for tool in await adapter.discover_tools():
            harness.register_tool(tool)
        provider = MixedToolProvider()

        result = await harness.create_agent_loop(
            provider,
            model="mcp-demo-model",
            max_steps=4,
            max_context_chars=8_000,
        ).run("Use one local and one remote tool.", task_id="mcp-mixed")

        self.assertEqual(result.final_answer, "Local and MCP observations used the same loop.")
        self.assertEqual(
            [tool_result.name for tool_result in result.state.tool_results],
            ["calculator", "remote_echo"],
        )
        definitions_message = provider.requests[0].messages[2].content
        self.assertIn('"name":"calculator"', definitions_message)
        self.assertIn('"name":"remote_echo"', definitions_message)


if __name__ == "__main__":
    unittest.main()
