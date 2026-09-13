"""Task persistence port and deterministic in-memory implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod

from harness.runtime.task import AgentTask, TaskStatus


class TaskStoreError(RuntimeError):
    """Base Task Store failure."""


class TaskNotFoundError(TaskStoreError):
    """Raised when a Task ID does not exist."""


class DuplicateTaskError(TaskStoreError):
    """Raised when create would replace an existing Task."""


class TaskStore(ABC):
    """Storage boundary shared by disconnected Clients and Workers."""

    @abstractmethod
    def create(self, task: AgentTask) -> None:
        """Store a new Task without replacing an existing ID."""

    @abstractmethod
    def get(self, task_id: str) -> AgentTask:
        """Load the latest record for one Task."""

    @abstractmethod
    def update(self, task: AgentTask) -> None:
        """Replace the latest record for an existing Task."""

    @abstractmethod
    def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
    ) -> tuple[AgentTask, ...]:
        """List Tasks in creation order, optionally filtered by status."""


class InMemoryTaskStore(TaskStore):
    """Minimal process-local store; durable stores can implement the same port."""

    def __init__(self) -> None:
        self._tasks: dict[str, AgentTask] = {}

    def create(self, task: AgentTask) -> None:
        _require_task(task)
        if task.task_id in self._tasks:
            raise DuplicateTaskError(f"task already exists: {task.task_id}")
        self._tasks[task.task_id] = task

    def get(self, task_id: str) -> AgentTask:
        normalized_id = _task_id(task_id)
        try:
            return self._tasks[normalized_id]
        except KeyError as error:
            raise TaskNotFoundError(f"task not found: {normalized_id}") from error

    def update(self, task: AgentTask) -> None:
        _require_task(task)
        if task.task_id not in self._tasks:
            raise TaskNotFoundError(f"task not found: {task.task_id}")
        self._tasks[task.task_id] = task

    def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
    ) -> tuple[AgentTask, ...]:
        if status is not None and not isinstance(status, TaskStatus):
            raise TypeError("status must be a TaskStatus or null")
        tasks = tuple(self._tasks.values())
        if status is None:
            return tasks
        return tuple(task for task in tasks if task.status is status)


def _require_task(task: AgentTask) -> None:
    if not isinstance(task, AgentTask):
        raise TypeError("task must be an AgentTask")


def _task_id(task_id: str) -> str:
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must not be empty")
    return task_id.strip()
