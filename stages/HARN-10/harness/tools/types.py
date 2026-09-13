"""Small data objects shared by the HARN-03 tool feedback loop."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from harness.core.types import JsonValue


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A model-requested tool name and its JSON-compatible arguments."""

    name: str
    arguments: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("tool name must be a string")
        normalized_name = self.name.strip()
        if not normalized_name:
            raise ValueError("tool name must not be empty")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("tool arguments must be an object")
        if not all(isinstance(key, str) for key in self.arguments):
            raise TypeError("tool argument names must be strings")

        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "arguments", dict(self.arguments))


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The observation produced by executing one tool call."""

    name: str
    arguments: Mapping[str, JsonValue]
    result: JsonValue = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("tool result name must not be empty")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("tool result arguments must be an object")
        if self.error is not None:
            if not isinstance(self.error, str):
                raise TypeError("tool result error must be a string or null")
            if not self.error.strip():
                raise ValueError("tool result error must not be empty")

        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "arguments", dict(self.arguments))

    @property
    def succeeded(self) -> bool:
        """Whether execution completed without a tool error."""

        return self.error is None
