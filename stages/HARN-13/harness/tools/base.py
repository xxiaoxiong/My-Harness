"""Tool definition and implementation contracts introduced in HARN-04."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass

from harness.core.types import JsonValue


@dataclass(frozen=True, slots=True)
class ToolSchema:
    """Provider-neutral metadata that describes how a model calls a tool."""

    name: str
    description: str
    parameters: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("tool schema name must not be empty")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("tool schema description must not be empty")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("tool schema parameters must be an object")

        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "description", self.description.strip())
        object.__setattr__(self, "parameters", dict(self.parameters))

    def as_dict(self) -> dict[str, JsonValue]:
        """Return the JSON-compatible definition shown to a model."""

        return {
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters),
        }


class ToolError(RuntimeError):
    """An expected tool failure that can safely be observed by the model."""


class Tool(ABC):
    """Implementation contract kept separate from a tool's schema data."""

    @property
    @abstractmethod
    def schema(self) -> ToolSchema:
        """Describe this tool without executing it."""

    @abstractmethod
    async def execute(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        """Execute one validated-by-the-tool arguments object."""
