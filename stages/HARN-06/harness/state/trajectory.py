"""Append-only execution events that describe where an Agent has been."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, unique

from harness.core.types import JsonValue


@unique
class TrajectoryEventKind(str, Enum):
    """Kinds of execution events recorded by the HARN-05 runtime."""

    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FINAL_ANSWER = "final_answer"
    MAX_STEPS_REACHED = "max_steps_reached"


@dataclass(frozen=True, slots=True)
class TrajectoryEvent:
    """One ordered fact from an Agent run."""

    sequence: int
    kind: TrajectoryEventKind
    occurred_at: datetime
    details: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("trajectory sequence must be an integer")
        if self.sequence <= 0:
            raise ValueError("trajectory sequence must be greater than zero")
        if not isinstance(self.kind, TrajectoryEventKind):
            raise TypeError("trajectory kind must be a TrajectoryEventKind")
        if self.occurred_at.tzinfo is None:
            raise ValueError("trajectory time must be timezone-aware")
        if not isinstance(self.details, Mapping):
            raise TypeError("trajectory details must be an object")

        object.__setattr__(self, "details", dict(self.details))
