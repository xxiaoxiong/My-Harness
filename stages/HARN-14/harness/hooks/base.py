"""Lifecycle Hook contracts and structured runtime event context."""

from __future__ import annotations

from dataclasses import dataclass

from harness.model import ModelRequest, ModelResponse
from harness.state import AgentStatus
from harness.tools import ToolCall, ToolResult


@dataclass(frozen=True, slots=True)
class HookContext:
    """Immutable metadata delivered to one lifecycle callback."""

    task_id: str
    goal: str
    step: int
    status: AgentStatus
    phase: str
    request: ModelRequest | None = None
    response: ModelResponse | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    error: Exception | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("hook task_id must not be empty")
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise ValueError("hook goal must not be empty")
        if isinstance(self.step, bool) or not isinstance(self.step, int):
            raise TypeError("hook step must be an integer")
        if self.step < 0:
            raise ValueError("hook step must not be negative")
        if not isinstance(self.status, AgentStatus):
            raise TypeError("hook status must be an AgentStatus")
        if not isinstance(self.phase, str) or not self.phase.strip():
            raise ValueError("hook phase must not be empty")


class Hook:
    """Override only the asynchronous lifecycle callbacks you need."""

    async def before_model_call(self, context: HookContext) -> None:
        pass

    async def after_model_call(self, context: HookContext) -> None:
        pass

    async def before_tool_call(self, context: HookContext) -> None:
        pass

    async def after_tool_call(self, context: HookContext) -> None:
        pass

    async def on_error(self, context: HookContext) -> None:
        pass

    async def before_step(self, context: HookContext) -> None:
        pass

    async def after_step(self, context: HookContext) -> None:
        pass
