"""Small transport-neutral data objects used by the MCP adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from harness.core import JsonValue


@dataclass(frozen=True, slots=True)
class MCPToolDefinition:
    """One MCP server Tool definition before it enters the local Registry."""

    name: str
    input_schema: Mapping[str, JsonValue]
    description: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("MCP tool name must not be empty")
        if not isinstance(self.input_schema, Mapping):
            raise TypeError("MCP input_schema must be an object")
        if not all(isinstance(key, str) for key in self.input_schema):
            raise TypeError("MCP input_schema keys must be strings")
        if self.description is not None and not isinstance(self.description, str):
            raise TypeError("MCP tool description must be a string or null")

        description = (
            self.description.strip() if self.description is not None else ""
        )
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "input_schema", dict(self.input_schema))
        object.__setattr__(self, "description", description or None)


@dataclass(frozen=True, slots=True)
class MCPCallResult:
    """Normalized success or tool-level error returned by an MCP Client."""

    content: JsonValue
    is_error: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.is_error, bool):
            raise TypeError("MCP result is_error must be a boolean")
