"""Adapters that translate Harness lifecycle boundaries into trace events."""

from __future__ import annotations

from time import perf_counter

from harness.hooks import Hook, HookContext, HookExecutionError
from harness.model import ModelProviderError
from harness.observability.events import (
    FailureLayer,
    TraceEventKind,
    TraceIdentity,
)
from harness.observability.recorder import TraceEmitter, TraceRecorder
from harness.runtime import AgentTask, RetryNotification, TaskStatus, TaskStore


class TracingHook(Hook):
    """Record the Agent Step -> Model + Tool subtree through lifecycle Hooks."""

    def __init__(self, identity: TraceIdentity, recorder: TraceRecorder) -> None:
        self._emitter = TraceEmitter(identity, recorder)
        self._step_started: dict[int, float] = {}
        self._model_started: dict[int, float] = {}
        self._tool_started: dict[int, float] = {}

    async def before_step(self, context: HookContext) -> None:
        self._step_started[context.step] = perf_counter()
        self._emitter.emit(
            TraceEventKind.STEP_STARTED,
            step=context.step,
            details={"status": context.status.value},
        )

    async def after_step(self, context: HookContext) -> None:
        self._emitter.emit(
            TraceEventKind.STEP_COMPLETED,
            step=context.step,
            details={"status": context.status.value},
        )
        self._emit_elapsed("agent_step", context.step, self._step_started)

    async def before_model_call(self, context: HookContext) -> None:
        request = context.request
        if request is None:
            return
        self._model_started[context.step] = perf_counter()
        self._emitter.emit(
            TraceEventKind.MODEL_CALL_STARTED,
            step=context.step,
            details={"model": request.model},
        )
        self._emitter.emit(
            TraceEventKind.CONTEXT_SIZE,
            step=context.step,
            details={
                "message_count": len(request.messages),
                "character_count": sum(
                    len(message.content) for message in request.messages
                ),
            },
        )

    async def after_model_call(self, context: HookContext) -> None:
        response = context.response
        if response is None:
            return
        self._emitter.emit(
            TraceEventKind.MODEL_CALL_COMPLETED,
            step=context.step,
            details={
                "response_id": response.response_id,
                "model": response.model,
                "finish_reason": response.finish_reason,
            },
        )
        usage = response.usage
        self._emitter.emit(
            TraceEventKind.TOKEN_USAGE,
            step=context.step,
            details={
                "prompt_tokens": usage.prompt_tokens if usage else None,
                "completion_tokens": usage.completion_tokens if usage else None,
                "total_tokens": usage.total_tokens if usage else None,
            },
        )
        self._emitter.emit(
            TraceEventKind.TTFT,
            step=context.step,
            details={
                "milliseconds": response.ttft_ms,
                "available": response.ttft_ms is not None,
            },
        )
        self._emitter.emit(
            TraceEventKind.LATENCY,
            step=context.step,
            details={
                "component": "model",
                "milliseconds": response.latency_ms,
                "source": "provider",
            },
        )
        self._emit_elapsed("model_hook_span", context.step, self._model_started)

    async def before_tool_call(self, context: HookContext) -> None:
        self._tool_started[context.step] = perf_counter()

    async def on_tool_call(self, context: HookContext) -> None:
        call = context.tool_call
        if call is None:
            return
        self._emitter.emit(
            TraceEventKind.TOOL_CALL,
            step=context.step,
            details={"name": call.name, "arguments": dict(call.arguments)},
        )

    async def after_tool_call(self, context: HookContext) -> None:
        self._emit_elapsed("tool", context.step, self._tool_started)

    async def on_tool_result(self, context: HookContext) -> None:
        result = context.tool_result
        if result is None:
            return
        self._emitter.emit(
            TraceEventKind.TOOL_RESULT,
            step=context.step,
            details={
                "name": result.name,
                "succeeded": result.succeeded,
                "result": result.result,
                "error": result.error,
            },
        )
        if not result.succeeded:
            self._emitter.emit(
                TraceEventKind.ERROR,
                step=context.step,
                details={
                    "layer": FailureLayer.TOOL.value,
                    "phase": context.phase,
                    "error_type": "ToolResultError",
                    "message": result.error,
                },
            )

    async def on_error(self, context: HookContext) -> None:
        error = context.error
        if error is None:
            return
        self._model_started.pop(context.step, None)
        self._tool_started.pop(context.step, None)
        self._step_started.pop(context.step, None)
        self._emitter.emit(
            TraceEventKind.ERROR,
            step=context.step,
            details={
                "layer": classify_failure(error, phase=context.phase).value,
                "phase": context.phase,
                "error_type": type(error).__name__,
                "message": str(error),
            },
        )

    def _emit_elapsed(
        self,
        component: str,
        step: int,
        started: dict[int, float],
    ) -> None:
        started_at = started.pop(step, None)
        if started_at is None:
            return
        self._emitter.emit(
            TraceEventKind.LATENCY,
            step=step,
            details={
                "component": component,
                "milliseconds": (perf_counter() - started_at) * 1000,
                "source": "harness",
            },
        )


class TracingTaskStore(TaskStore):
    """Task Store decorator that records every persisted state transition."""

    def __init__(self, delegate: TaskStore, recorder: TraceRecorder) -> None:
        if not isinstance(delegate, TaskStore):
            raise TypeError("delegate must be a TaskStore")
        if not isinstance(recorder, TraceRecorder):
            raise TypeError("recorder must be a TraceRecorder")
        self._delegate = delegate
        self._recorder = recorder

    def create(self, task: AgentTask) -> None:
        self._delegate.create(task)
        self._emit_transition(task, previous=None)

    def get(self, task_id: str) -> AgentTask:
        return self._delegate.get(task_id)

    def update(self, task: AgentTask) -> None:
        previous = self._delegate.get(task.task_id)
        self._delegate.update(task)
        if previous.status is not task.status:
            self._emit_transition(task, previous=previous.status)

    def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
    ) -> tuple[AgentTask, ...]:
        return self._delegate.list_tasks(status=status)

    def _emit_transition(
        self,
        task: AgentTask,
        *,
        previous: TaskStatus | None,
    ) -> None:
        TraceEmitter(_identity(task), self._recorder).emit(
            TraceEventKind.TASK_STATE_TRANSITION,
            details={
                "from": previous.value if previous is not None else None,
                "to": task.status.value,
                "attempts": task.attempts,
            },
        )


class TracingRetryObserver:
    """Translate Scheduler retry decisions into correlated events."""

    def __init__(self, recorder: TraceRecorder) -> None:
        if not isinstance(recorder, TraceRecorder):
            raise TypeError("recorder must be a TraceRecorder")
        self._recorder = recorder

    def __call__(self, notification: RetryNotification) -> None:
        if not isinstance(notification, RetryNotification):
            raise TypeError("notification must be a RetryNotification")
        task = notification.task
        TraceEmitter(_identity(task), self._recorder).emit(
            TraceEventKind.RETRY,
            details={
                "failed_attempt": task.attempts,
                "next_attempt": task.attempts + 1,
                "delay_seconds": notification.delay_seconds,
                "error_type": type(notification.error).__name__,
                "message": str(notification.error),
                "layer": classify_failure(notification.error).value,
            },
        )


def classify_failure(error: Exception, *, phase: str | None = None) -> FailureLayer:
    """Classify the most likely failure layer using typed errors and phase."""

    if not isinstance(error, Exception):
        raise TypeError("error must be an Exception")
    normalized_phase = "" if phase is None else phase.strip().lower()
    if isinstance(error, HookExecutionError) or "hook" in normalized_phase:
        return FailureLayer.HARNESS
    if isinstance(error, ModelProviderError) or "model" in normalized_phase:
        return FailureLayer.MODEL
    if normalized_phase == "parse_action":
        return FailureLayer.PROMPT
    if normalized_phase == "context_build":
        return FailureLayer.CONTEXT
    if "tool" in normalized_phase or normalized_phase == "policy_decision":
        return FailureLayer.TOOL
    if isinstance(error, (ConnectionError, OSError, TimeoutError)):
        return FailureLayer.INFRASTRUCTURE
    return FailureLayer.RUNTIME


def _identity(task: AgentTask) -> TraceIdentity:
    return TraceIdentity(
        task_id=task.task_id,
        trace_id=task.trace_id,
        session_id=task.session_id,
    )
