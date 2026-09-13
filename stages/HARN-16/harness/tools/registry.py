"""In-memory discovery of tool implementations by schema name."""

from __future__ import annotations

from harness.tools.base import Tool


class DuplicateToolError(ValueError):
    """Raised when a registry already contains the requested tool name."""


class ToolNotFoundError(LookupError):
    """Raised when a registry cannot resolve a tool name."""


class ToolRegistry:
    """Register, resolve, and list tool implementations."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register one tool and reject ambiguous duplicate names."""

        if not isinstance(tool, Tool):
            raise TypeError("tool must implement Tool")
        name = tool.schema.name
        if name in self._tools:
            raise DuplicateToolError(f"tool is already registered: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool:
        """Return a registered implementation by exact name."""

        if not isinstance(name, str) or not name.strip():
            raise ValueError("tool name must not be empty")
        normalized_name = name.strip()
        try:
            return self._tools[normalized_name]
        except KeyError as error:
            raise ToolNotFoundError(f"tool not found: {normalized_name}") from error

    def list_tools(self) -> tuple[Tool, ...]:
        """Return tools in deterministic registration order."""

        return tuple(self._tools.values())
