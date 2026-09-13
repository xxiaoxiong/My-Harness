"""Checkpoint records and storage contract for recoverable Agent runs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime

from harness.context import ContextBuildResult
from harness.state import AgentState, AgentStatus

CHECKPOINT_SCHEMA_VERSION = 2


class CheckpointError(RuntimeError):
    """Base error for checkpoint persistence and recovery."""


class CheckpointNotFoundError(CheckpointError):
    """Raised when a task has no saved checkpoint."""


class CheckpointCorruptError(CheckpointError):
    """Raised when persisted data cannot reconstruct a trusted checkpoint."""


class InvalidCheckpointTaskId(ValueError):
    """Raised when a task ID cannot be mapped to a safe checkpoint filename."""


@dataclass(frozen=True, slots=True)
class CheckpointContext:
    """Metadata for the exact Context used by the checkpointed step."""

    message_count: int
    total_chars: int
    max_context_chars: int
    truncated: bool
    dropped_messages: int
    summary: str | None
    summarized_messages: int

    def __post_init__(self) -> None:
        integer_fields = (
            self.message_count,
            self.total_chars,
            self.max_context_chars,
            self.dropped_messages,
            self.summarized_messages,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_fields):
            raise TypeError("checkpoint context counts must be integers")
        if self.message_count <= 0:
            raise ValueError("checkpoint context message_count must be positive")
        if self.total_chars < 0 or self.dropped_messages < 0:
            raise ValueError("checkpoint context counts must not be negative")
        if self.max_context_chars <= 0:
            raise ValueError("checkpoint context max_context_chars must be positive")
        if self.total_chars > self.max_context_chars:
            raise ValueError("checkpoint context exceeds max_context_chars")
        if self.summarized_messages < 0:
            raise ValueError("summarized_messages must not be negative")
        if not isinstance(self.truncated, bool):
            raise TypeError("checkpoint context truncated must be a boolean")
        if self.summary is not None and not isinstance(self.summary, str):
            raise TypeError("checkpoint context summary must be a string or null")

    @classmethod
    def from_build_result(cls, result: ContextBuildResult) -> CheckpointContext:
        """Capture compact metadata without storing a second model input."""

        return cls(
            message_count=len(result.messages),
            total_chars=result.total_chars,
            max_context_chars=result.max_context_chars,
            truncated=result.truncated,
            dropped_messages=result.dropped_messages,
            summary=result.summary,
            summarized_messages=result.summarized_messages,
        )


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """One durable recovery point after a completed Agent step."""

    state: AgentState
    context: CheckpointContext
    saved_at: datetime
    schema_version: int = CHECKPOINT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.state, AgentState):
            raise TypeError("checkpoint state must be an AgentState")
        if not isinstance(self.context, CheckpointContext):
            raise TypeError("checkpoint context must be CheckpointContext")
        if self.saved_at.tzinfo is None:
            raise ValueError("checkpoint saved_at must be timezone-aware")
        if self.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(
                f"checkpoint schema_version must be {CHECKPOINT_SCHEMA_VERSION}"
            )

    @property
    def task_id(self) -> str:
        return self.state.task_id

    @property
    def status(self) -> AgentStatus:
        return self.state.status

    @property
    def step(self) -> int:
        return self.state.current_step

    @classmethod
    def capture(
        cls,
        state: AgentState,
        context: ContextBuildResult,
    ) -> Checkpoint:
        """Create a checkpoint from current state and its latest model input."""

        return cls(
            state=state,
            context=CheckpointContext.from_build_result(context),
            saved_at=datetime.now(UTC),
        )


class CheckpointStore(ABC):
    """Persistence boundary used by Agent Runtime."""

    @abstractmethod
    def save(self, checkpoint: Checkpoint) -> None:
        """Atomically persist the latest checkpoint for its task."""

    @abstractmethod
    def load(self, task_id: str) -> Checkpoint:
        """Load the latest checkpoint for a task."""
