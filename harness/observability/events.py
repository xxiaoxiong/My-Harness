"""Provider-neutral structured trace events for one Agent Task tree."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, unique
from types import MappingProxyType
from typing import Any
from uuid import uuid4


@unique
class TraceEventKind(str, Enum):
    TASK_STATE_TRANSITION = "task.state_transition"
    STEP_STARTED = "agent.step.started"
    STEP_COMPLETED = "agent.step.completed"
    MODEL_CALL_STARTED = "model.call.started"
    MODEL_CALL_COMPLETED = "model.call.completed"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    TOKEN_USAGE = "model.token_usage"
    TTFT = "model.ttft"
    LATENCY = "latency"
    RETRY = "task.retry"
    CONTEXT_SIZE = "model.context_size"
    ERROR = "error"


@unique
class FailureLayer(str, Enum):
    """The layer most likely responsible for a recorded failure."""

    MODEL = "model"
    PROMPT = "prompt"
    CONTEXT = "context"
    TOOL = "tool"
    HARNESS = "harness"
    RUNTIME = "runtime"
    INFRASTRUCTURE = "infrastructure"


@dataclass(frozen=True, slots=True)
class TraceIdentity:
    """Stable correlation identifiers shared by every event for one Task."""

    task_id: str
    trace_id: str
    session_id: str

    def __post_init__(self) -> None:
        for name in ("task_id", "trace_id", "session_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, value.strip())


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One immutable structured observation in a Task trace."""

    kind: TraceEventKind
    identity: TraceIdentity
    details: Mapping[str, Any] = field(default_factory=dict)
    step: int | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TraceEventKind):
            raise TypeError("kind must be a TraceEventKind")
        if not isinstance(self.identity, TraceIdentity):
            raise TypeError("identity must be a TraceIdentity")
        if self.step is not None:
            if isinstance(self.step, bool) or not isinstance(self.step, int):
                raise TypeError("step must be an integer or null")
            if self.step < 0:
                raise ValueError("step must not be negative")
        if not isinstance(self.details, Mapping):
            raise TypeError("details must be a mapping")
        if not all(isinstance(key, str) for key in self.details):
            raise TypeError("detail names must be strings")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))
        object.__setattr__(self, "event_id", self.event_id.strip())

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable structured log record."""

        return {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "task_id": self.identity.task_id,
            "trace_id": self.identity.trace_id,
            "session_id": self.identity.session_id,
            "step": self.step,
            "occurred_at": self.occurred_at.isoformat(),
            "details": dict(self.details),
        }
