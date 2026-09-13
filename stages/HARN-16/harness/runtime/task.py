"""Task-level lifecycle that outlives one Agent invocation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum, unique
from uuid import uuid4


@unique
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.WAITING,
            TaskStatus.SUSPENDED,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.WAITING: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.SUSPENDED: frozenset({TaskStatus.CANCELLED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class AgentTask:
    """Immutable Task Store record around a resumable Agent execution."""

    task_id: str
    goal: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    priority: int = 0
    attempts: int = 0
    idempotency_key: str | None = None
    final_answer: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise ValueError("task goal must not be empty")
        if not isinstance(self.status, TaskStatus):
            raise TypeError("task status must be a TaskStatus")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("task timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("task updated_at must not be before created_at")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("task priority must be an integer")
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise TypeError("task attempts must be an integer")
        if self.attempts < 0:
            raise ValueError("task attempts must not be negative")
        if self.idempotency_key is not None and (
            not isinstance(self.idempotency_key, str)
            or not self.idempotency_key.strip()
        ):
            raise ValueError("task idempotency_key must be nonempty or null")
        if self.final_answer is not None and (
            not isinstance(self.final_answer, str) or not self.final_answer.strip()
        ):
            raise ValueError("task final_answer must be nonempty or null")
        if self.error is not None and (
            not isinstance(self.error, str) or not self.error.strip()
        ):
            raise ValueError("task error must be nonempty or null")
        if self.status is TaskStatus.COMPLETED:
            if self.final_answer is None or self.error is not None:
                raise ValueError(
                    "completed task requires final_answer and no error"
                )
        elif self.status is TaskStatus.FAILED:
            if self.error is None or self.final_answer is not None:
                raise ValueError("failed task requires error and no final_answer")
        elif self.final_answer is not None or self.error is not None:
            raise ValueError(
                "only completed or failed tasks may contain terminal data"
            )

        object.__setattr__(self, "task_id", self.task_id.strip())
        object.__setattr__(self, "goal", self.goal.strip())
        if self.idempotency_key is not None:
            object.__setattr__(
                self,
                "idempotency_key",
                self.idempotency_key.strip(),
            )
        if self.final_answer is not None:
            object.__setattr__(self, "final_answer", self.final_answer.strip())
        if self.error is not None:
            object.__setattr__(self, "error", self.error.strip())

    @classmethod
    def create(
        cls,
        goal: str,
        *,
        task_id: str | None = None,
        priority: int = 0,
        idempotency_key: str | None = None,
    ) -> AgentTask:
        now = datetime.now(UTC)
        return cls(
            task_id=task_id if task_id is not None else uuid4().hex,
            goal=goal,
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
            priority=priority,
            idempotency_key=idempotency_key,
        )

    @property
    def terminal(self) -> bool:
        return self.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }

    def mark_running(self) -> AgentTask:
        return self._transition(TaskStatus.RUNNING)

    def mark_waiting(self) -> AgentTask:
        return self._transition(TaskStatus.WAITING)

    def mark_suspended(self) -> AgentTask:
        return self._transition(TaskStatus.SUSPENDED)

    def mark_completed(self, final_answer: str) -> AgentTask:
        return self._transition(
            TaskStatus.COMPLETED,
            final_answer=final_answer,
        )

    def mark_failed(self, error: str) -> AgentTask:
        return self._transition(TaskStatus.FAILED, error=error)

    def mark_cancelled(self) -> AgentTask:
        return self._transition(TaskStatus.CANCELLED)

    def record_attempt(self) -> AgentTask:
        if self.status is not TaskStatus.RUNNING:
            raise InvalidTaskTransition(
                f"cannot record attempt for {self.status.value} task"
            )
        return replace(
            self,
            attempts=self.attempts + 1,
            updated_at=datetime.now(UTC),
        )

    def _transition(
        self,
        target: TaskStatus,
        *,
        final_answer: str | None = None,
        error: str | None = None,
    ) -> AgentTask:
        if target not in _TRANSITIONS[self.status]:
            raise InvalidTaskTransition(
                f"cannot transition task from {self.status.value} to {target.value}"
            )
        return replace(
            self,
            status=target,
            updated_at=datetime.now(UTC),
            final_answer=final_answer,
            error=error,
        )


class InvalidTaskTransition(RuntimeError):
    """Raised when Task lifecycle ordering would become ambiguous."""
