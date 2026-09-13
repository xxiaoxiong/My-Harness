"""Policy contracts separating requested actions from runtime permission."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, unique

from harness.tools import ToolCall


@unique
class PermissionDecision(str, Enum):
    """The three outcomes a Runtime understands before Tool execution."""

    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """Task and Tool facts supplied to a PolicyEngine."""

    task_id: str
    goal: str
    step: int
    tool_call: ToolCall

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("permission task_id must not be empty")
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise ValueError("permission goal must not be empty")
        if isinstance(self.step, bool) or not isinstance(self.step, int):
            raise TypeError("permission step must be an integer")
        if self.step <= 0:
            raise ValueError("permission step must be positive")
        if not isinstance(self.tool_call, ToolCall):
            raise TypeError("permission tool_call must be a ToolCall")


class InvalidPermissionDecision(TypeError):
    """Raised when a PolicyEngine violates its return contract."""


class PolicyEngine(ABC):
    """Decide whether one model-requested Tool Call may proceed."""

    @abstractmethod
    async def decide(self, request: PermissionRequest) -> PermissionDecision:
        """Return ALLOW, REQUIRE_APPROVAL, or DENY."""


class AllowAllPolicyEngine(PolicyEngine):
    """Default policy preserving unrestricted Tool execution."""

    async def decide(self, request: PermissionRequest) -> PermissionDecision:
        if not isinstance(request, PermissionRequest):
            raise TypeError("request must be a PermissionRequest")
        return PermissionDecision.ALLOW
