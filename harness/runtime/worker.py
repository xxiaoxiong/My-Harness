"""Worker that executes stored Tasks independently from their Clients."""

from __future__ import annotations

from collections.abc import Callable

from harness.runtime.task import AgentTask, TaskStatus
from harness.runtime.task_store import TaskStore
from harness.runtime.tool_agent_loop import ToolAgentLoop, ToolAgentRunResult
from harness.state import AgentStatus

AgentLoopFactory = Callable[[AgentTask], ToolAgentLoop]


class AgentWorker:
    """Run or resume one explicit Task ID from a shared Task Store."""

    def __init__(
        self,
        store: TaskStore,
        loop_factory: AgentLoopFactory,
    ) -> None:
        if not isinstance(store, TaskStore):
            raise TypeError("store must be a TaskStore")
        if not callable(loop_factory):
            raise TypeError("loop_factory must be callable")
        self._store = store
        self._loop_factory = loop_factory

    async def execute(self, task_id: str) -> AgentTask:
        """Start a pending Task and persist its externally visible outcome."""

        task = self._store.get(task_id)
        if task.status is not TaskStatus.PENDING:
            raise InvalidWorkerTaskState(
                f"worker can execute only pending tasks: {task.status.value}"
            )
        running = task.mark_running()
        self._store.update(running)
        return await self._invoke(running, resume_approval=None)

    async def resume(self, task_id: str, *, approve: bool) -> AgentTask:
        """Resolve a waiting approval and continue from its Agent checkpoint."""

        if not isinstance(approve, bool):
            raise TypeError("approve must be a boolean")
        task = self._store.get(task_id)
        if task.status is not TaskStatus.WAITING:
            raise InvalidWorkerTaskState(
                f"worker can resume only waiting tasks: {task.status.value}"
            )
        running = task.mark_running()
        self._store.update(running)
        return await self._invoke(running, resume_approval=approve)

    async def _invoke(
        self,
        running: AgentTask,
        *,
        resume_approval: bool | None,
    ) -> AgentTask:
        try:
            loop = self._loop_factory(running)
            if not isinstance(loop, ToolAgentLoop):
                raise TypeError("loop_factory must return a ToolAgentLoop")
            if resume_approval is None:
                result = await loop.run(
                    running.goal,
                    task_id=running.task_id,
                )
            else:
                result = await loop.resume(
                    running.task_id,
                    approve=resume_approval,
                )
            outcome = _task_from_result(running, result)
        except Exception as error:
            message = str(error).strip() or type(error).__name__
            outcome = running.mark_failed(message)
        self._store.update(outcome)
        return outcome


class InvalidWorkerTaskState(RuntimeError):
    """Raised when a Worker operation does not match the stored lifecycle."""


def _task_from_result(
    running: AgentTask,
    result: ToolAgentRunResult,
) -> AgentTask:
    if result.status is AgentStatus.FINISHED:
        if result.final_answer is None:
            raise RuntimeError("finished Agent result has no final answer")
        return running.mark_completed(result.final_answer)
    if result.status is AgentStatus.WAITING_APPROVAL:
        return running.mark_waiting()
    if result.status is AgentStatus.MAX_STEPS_REACHED:
        return running.mark_suspended()
    raise RuntimeError(f"Agent returned unexpected status: {result.status.value}")
