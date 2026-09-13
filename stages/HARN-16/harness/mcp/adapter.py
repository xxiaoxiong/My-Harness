"""Adapt remote MCP definitions and calls to ordinary Harness Tools."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from harness.core import JsonValue
from harness.mcp.client import MCPClient, MCPClientError, MCPProtocolError
from harness.mcp.types import MCPCallResult, MCPToolDefinition
from harness.tools import (
    DuplicateToolError,
    Tool,
    ToolError,
    ToolRegistry,
    ToolSchema,
)


class MCPToolError(ToolError):
    """An MCP transport or remote Tool error observable by the Agent."""


class MCPClientAdapter:
    """Discover an MCP Client's Tools and bridge calls into its transport."""

    def __init__(self, client: MCPClient, *, server_name: str) -> None:
        if not isinstance(client, MCPClient):
            raise TypeError("client must be an MCPClient")
        if not isinstance(server_name, str) or not server_name.strip():
            raise ValueError("server_name must not be empty")
        self._client = client
        self._server_name = server_name.strip()

    @property
    def server_name(self) -> str:
        return self._server_name

    async def discover_tools(self) -> tuple[MCPToolAdapter, ...]:
        definitions = await self._client.list_tools()
        if not isinstance(definitions, Sequence) or isinstance(
            definitions,
            (str, bytes, bytearray),
        ):
            raise MCPProtocolError("MCP list_tools must return a sequence")
        if not all(
            isinstance(definition, MCPToolDefinition)
            for definition in definitions
        ):
            raise MCPProtocolError(
                "MCP list_tools must return MCPToolDefinition values"
            )

        names = [definition.name for definition in definitions]
        duplicate_names = sorted(
            {name for name in names if names.count(name) > 1}
        )
        if duplicate_names:
            raise MCPProtocolError(
                "MCP server returned duplicate tool names: "
                + ", ".join(duplicate_names)
            )
        return tuple(MCPToolAdapter(self, definition) for definition in definitions)

    async def register_tools(
        self,
        registry: ToolRegistry,
    ) -> tuple[MCPToolAdapter, ...]:
        """Discover and register remote Tools through the normal Registry API."""

        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry")
        tools = await self.discover_tools()
        existing_names = {tool.schema.name for tool in registry.list_tools()}
        collisions = sorted(
            tool.schema.name for tool in tools if tool.schema.name in existing_names
        )
        if collisions:
            raise DuplicateToolError(
                "MCP tool conflicts with an existing tool: "
                + ", ".join(collisions)
            )
        for tool in tools:
            registry.register(tool)
        return tools

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
    ) -> MCPCallResult:
        result = await self._client.call_tool(name, arguments)
        if not isinstance(result, MCPCallResult):
            raise MCPProtocolError("MCP call_tool must return an MCPCallResult")
        return result


class MCPToolAdapter(Tool):
    """Expose one discovered MCP Tool through the local Tool contract."""

    def __init__(
        self,
        adapter: MCPClientAdapter,
        definition: MCPToolDefinition,
    ) -> None:
        if not isinstance(adapter, MCPClientAdapter):
            raise TypeError("adapter must be an MCPClientAdapter")
        if not isinstance(definition, MCPToolDefinition):
            raise TypeError("definition must be an MCPToolDefinition")
        self._adapter = adapter
        self._definition = definition

    @property
    def server_name(self) -> str:
        return self._adapter.server_name

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._definition.name,
            description=(
                self._definition.description
                or f"MCP tool from {self._adapter.server_name}."
            ),
            parameters=self._definition.input_schema,
        )

    async def execute(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        if not isinstance(arguments, Mapping):
            raise MCPToolError("MCP tool arguments must be an object")
        try:
            result = await self._adapter.call_tool(
                self._definition.name,
                arguments,
            )
        except MCPClientError as error:
            raise MCPToolError(
                f"MCP server {self.server_name!r} failed: {error}"
            ) from error
        if result.is_error:
            raise MCPToolError(
                f"MCP tool {self._definition.name!r} failed: "
                f"{_display_content(result.content)}"
            )
        return result.content


def _display_content(content: JsonValue) -> str:
    if isinstance(content, str) and content.strip():
        return content.strip()
    return json.dumps(content, ensure_ascii=False, separators=(",", ":"))
