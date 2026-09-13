"""MCP Client port implemented by an SDK or transport integration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence

from harness.core import JsonValue
from harness.mcp.types import MCPCallResult, MCPToolDefinition


class MCPClientError(RuntimeError):
    """Transport or protocol failure reported by an MCP Client."""


class MCPProtocolError(MCPClientError):
    """The MCP Client returned data that violates the adapter contract."""


class MCPClient(ABC):
    """Minimal MCP operations needed by the Harness.

    Concrete implementations may use stdio, HTTP, or an MCP SDK. Those details
    stay behind this port and never enter the Agent Loop.
    """

    @abstractmethod
    async def list_tools(self) -> Sequence[MCPToolDefinition]:
        """Discover the Tool definitions exposed by one MCP server."""

    @abstractmethod
    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
    ) -> MCPCallResult:
        """Call one remote Tool and normalize its content/error envelope."""
