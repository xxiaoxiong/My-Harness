"""Priority Task scheduling plus bounded retry, timeout, and cancellation."""

from __future__ import annotations

import asyncio
import heapq
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from math import isfinite

from harness.model import ModelProviderError
from harness.runtime.task import AgentTask, TaskStatus
from harness.runtime.task_store import TaskStore
from harness.runtime.worker import AgentWorker

RetryPredicate = Callable[[Exception], bool]
AsyncSleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RetryNotification:
    """One retry decision exposed without coupling Runtime to telemetry."""

    task: AgentTask
    error: Exception
    delay_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.task, AgentTask):
            raise TypeError("task must be an AgentTask")
        if not isinstance(self.error, Exception):
            raise TypeError("error must be an Exception")
        _nonnegative_finite(self.delay_seconds, "delay_seconds")
        object.__setattr__(self, "delay_seconds", float(self.delay_seconds))


RetryObserver = Callable[[RetryNotification], None]


def _default_retryable(error: Exception) -> bool:
    return isinstance(error, (ModelProviderError, TimeoutError))


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bound attempts and calculate exponential retry delays."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.1
    max_delay_seconds: float = 5.0
    retryable: RetryPredicate = field(default=_default_retryable, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(
            self.max_attempts,
            int,
        ):
            raise TypeError("max_attempts must be an integer")
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        _nonnegative_finite(self.base_delay_seconds, "base_delay_seconds")
        _nonnegative_finite(self.max_delay_seconds, "max_delay_seconds")
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("base_delay_seconds must not exceed max_delay_seconds")
        if not callable(self.retryable):
            raise TypeError("retryable must be callable")

    def should_retry(self, error: Exception, *, attempts: int) -> bool:
        if not isinstance(error, Exception):
            raise TypeError("error must be an Exception")
        if isinstance(attempts, bool) or not isinstance(attempts, int):
            raise TypeError("attempts must be an integer")
        return attempts < self.max_attempts and self.retryable(error)

    def delay_after(self, attempts: int) -> float:
        """Return the delay after the given failed attempt count."""

        if isinstance(attempts, bool) or not isinstance(attempts, int):
            raise TypeError("attempts must be an integer")
        if attempts <= 0:
            raise ValueError("attempts must be positive")
        return min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2 ** (attempts - 1)),
        )


class TaskScheduler:
    """Drain a priority queue through a bounded set of Agent Workers."""

    def __init__(
        self,
        store: TaskStore,
        worker: AgentWorker,
        *,
        max_concurrency: int = 1,
        retry_policy: RetryPolicy | None = None,
        task_timeout_seconds: float | None = None,
        sleeper: AsyncSleeper = asyncio.sleep,
        retry_observer: RetryObserver | None = None,
    ) -> None:
        if not isinstance(store, TaskStore):
            raise TypeError("store must be a TaskStore")
        if not isinstance(worker, AgentWorker):
            raise TypeError("worker must be an AgentWorker")
        if worker.store is not store:
            raise ValueError("scheduler and worker must share the same TaskStore")
        if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int):
            raise TypeError("max_concurrency must be an integer")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        if retry_policy is not None and not isinstance(retry_policy, RetryPolicy):
            raise TypeError("retry_policy must be a RetryPolicy or null")
        if task_timeout_seconds is not None:
            _positive_finite(task_timeout_seconds, "task_timeout_seconds")
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        if retry_observer is not None and not callable(retry_observer):
            raise TypeError("retry_observer must be callable or null")

        self._store = store
        self._worker = worker
        self._max_concurrency = max_concurrency
        self._retry_policy = retry_policy or RetryPolicy()
        self._task_timeout_seconds = task_timeout_seconds
        self._sleeper = sleeper
        self._retry_observer = retry_observer
        self._queue: list[tuple[int, int, str]] = []
        self._queued: set[str] = set()
        self._running: dict[str, asyncio.Task[None]] = {}
        self._sequence = 0
        self._draining = False

    @property
    def queued_task_ids(self) -> tuple[str, ...]:
        ordered = sorted(self._queue)
        return tuple(
            task_id for _, _, task_id in ordered if task_id in self._queued
        )

    @property
    def running_task_ids(self) -> tuple[str, ...]:
        return tuple(self._running)

    def submit(
        self,
        goal: str,
        *,
        priority: int = 0,
        task_id: str | None = None,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
    ) -> AgentTask:
        """Create and enqueue work, deduplicating an explicit submission key."""

        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("task goal must not be empty")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise TypeError("priority must be an integer")
        normalized_goal = goal.strip()
        normalized_trace_id = _optional_id(trace_id, "trace_id")
        normalized_session_id = _optional_id(session_id, "session_id")
        if idempotency_key is not None:
            normalized_key = _submission_key(idempotency_key)
            existing = next(
                (
                    task
                    for task in self._store.list_tasks()
                    if task.idempotency_key == normalized_key
                ),
                None,
            )
            if existing is not None:
                if existing.goal != normalized_goal or existing.priority != priority:
                    raise TaskSubmissionConflictError(
                        "idempotency key was already used for different Task input"
                    )
                if (
                    normalized_trace_id is not None
                    and existing.trace_id != normalized_trace_id
                ):
                    raise TaskSubmissionConflictError(
                        "idempotency key was already used with a different trace_id"
                    )
                if (
                    normalized_session_id is not None
                    and existing.session_id != normalized_session_id
                ):
                    raise TaskSubmissionConflictError(
                        "idempotency key was already used with a different session_id"
                    )
                if existing.status is TaskStatus.PENDING:
                    self.enqueue(existing.task_id)
                return existing

        task = AgentTask.create(
            normalized_goal,
            task_id=task_id,
            priority=priority,
            idempotency_key=idempotency_key,
            trace_id=normalized_trace_id,
            session_id=normalized_session_id,
        )
        self._store.create(task)
        self.enqueue(task.task_id)
        return task

    def enqueue(self, task_id: str) -> None:
        task = self._store.get(task_id)
        if task.status is not TaskStatus.PENDING:
            raise InvalidScheduledTaskState(
                f"scheduler can enqueue only pending tasks: {task.status.value}"
            )
        if task.task_id in self._queued or task.task_id in self._running:
            return
        heapq.heappush(
            self._queue,
            (-task.priority, self._sequence, task.task_id),
        )
        self._sequence += 1
        self._queued.add(task.task_id)

    async def run_until_idle(self) -> tuple[AgentTask, ...]:
        """Run queued work until no queued or active Task remains."""

        if self._draining:
            raise SchedulerAlreadyRunningError("scheduler is already running")
        self._draining = True
        try:
            while self._queue or self._running:
                self._launch_available()
                if not self._running:
                    continue
                done, _ = await asyncio.wait(
                    tuple(self._running.values()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task_id, handle in tuple(self._running.items()):
                    if handle not in done:
                        continue
                    self._running.pop(task_id)
                    try:
                        await handle
                    except asyncio.CancelledError:
                        pass
            return self._store.list_tasks()
        finally:
            self._draining = False

    async def cancel(self, task_id: str) -> AgentTask:
        """Cancel queued, active, waiting, or suspended work."""

        task = self._store.get(task_id)
        if task.status is TaskStatus.CANCELLED:
            return task
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            raise InvalidScheduledTaskState(
                f"scheduler cannot cancel terminal task: {task.status.value}"
            )
        self._queued.discard(task.task_id)
        handle = self._running.get(task.task_id)
        if handle is not None:
            handle.cancel()
            await asyncio.gather(handle, return_exceptions=True)
            return self._store.get(task.task_id)
        return self._worker.cancel(task.task_id)

    def _launch_available(self) -> None:
        while len(self._running) < self._max_concurrency and self._queue:
            _, _, task_id = heapq.heappop(self._queue)
            if task_id not in self._queued:
                continue
            self._queued.remove(task_id)
            if self._store.get(task_id).status is not TaskStatus.PENDING:
                continue
            self._running[task_id] = asyncio.create_task(
                self._run_task(task_id)
            )

    async def _run_task(self, task_id: str) -> None:
        try:
            self._worker.begin(task_id)
            while True:
                try:
                    attempt = self._worker.run_attempt(task_id)
                    if self._task_timeout_seconds is None:
                        await attempt
                    else:
                        await asyncio.wait_for(
                            attempt,
                            timeout=self._task_timeout_seconds,
                        )
                    return
                except Exception as error:
                    task = self._store.get(task_id)
                    if not self._retry_policy.should_retry(
                        error,
                        attempts=task.attempts,
                    ):
                        self._worker.fail(task_id, error)
                        return
                    delay = self._retry_policy.delay_after(task.attempts)
                    if self._retry_observer is not None:
                        self._retry_observer(
                            RetryNotification(
                                task=task,
                                error=error,
                                delay_seconds=delay,
                            )
                        )
                    await self._sleeper(delay)
        except asyncio.CancelledError:
            task = self._store.get(task_id)
            if not task.terminal:
                self._worker.cancel(task_id)
            raise


class SchedulerError(RuntimeError):
    """Base Scheduler failure."""


class SchedulerAlreadyRunningError(SchedulerError):
    """Raised when one Scheduler is drained by two callers concurrently."""


class InvalidScheduledTaskState(SchedulerError):
    """Raised when queue or cancellation state is incompatible."""


class TaskSubmissionConflictError(SchedulerError):
    """Raised when a submission key is reused with different input."""


def _submission_key(key: str) -> str:
    if not isinstance(key, str) or not key.strip():
        raise ValueError("idempotency_key must not be empty")
    return key.strip()


def _optional_id(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string or null")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value.strip()


def _positive_finite(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    if value <= 0 or not isfinite(value):
        raise ValueError(f"{name} must be positive and finite")


def _nonnegative_finite(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    if value < 0 or not isfinite(value):
        raise ValueError(f"{name} must be nonnegative and finite")
