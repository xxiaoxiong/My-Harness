"""Plugin, Skill, and Prompt Fragment extension contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.extensions.harness import Harness


@dataclass(frozen=True, slots=True)
class PromptFragment:
    """One named instruction block contributed by a Plugin."""

    name: str
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("prompt fragment name must not be empty")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("prompt fragment content must not be empty")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "content", self.content.strip())


class Plugin(ABC):
    """Extension mechanism that injects capabilities into a Harness."""

    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    def setup(self, harness: Harness) -> None:
        """Register this Plugin's capabilities before the Harness is sealed."""


class Skill(Plugin, ABC):
    """A task-domain capability bundle loaded through the Plugin mechanism."""


class DuplicatePluginError(ValueError):
    """Raised when a Plugin name is already loaded."""


class DuplicatePromptFragmentError(ValueError):
    """Raised when a Prompt Fragment name is already registered."""


class HarnessSealedError(RuntimeError):
    """Raised when extensions are added after Runtime construction."""
