"""Tool execution through a registry-owned implementation boundary."""

from __future__ import annotations

from harness.tools.base import ToolError, ToolSchema
from harness.tools.registry import ToolNotFoundError, ToolRegistry
from harness.tools.types import ToolCall, ToolResult


class ToolExecutor:
    """Resolve calls through a registry and normalize expected failures."""

    def __init__(self, registry: ToolRegistry) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry")
        self._registry = registry

    def list_schemas(self) -> tuple[ToolSchema, ...]:
        """Expose definitions without exposing implementations to the Agent."""

        return tuple(tool.schema for tool in self._registry.list_tools())

    async def execute(self, call: ToolCall) -> ToolResult:
        """Execute one call and preserve its name, arguments, result, or error."""

        try:
            tool = self._registry.get(call.name)
        except ToolNotFoundError as error:
            return ToolResult(
                name=call.name,
                arguments=call.arguments,
                error=str(error),
            )

        try:
            result = await tool.execute(call.arguments)
        except ToolError as error:
            return ToolResult(
                name=call.name,
                arguments=call.arguments,
                error=str(error),
            )

        return ToolResult(
            name=call.name,
            arguments=call.arguments,
            result=result,
        )
