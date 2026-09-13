"""Small Logging and Metrics Hooks for the HARN-10 learning stage."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter

from harness.core import get_logger
from harness.hooks.base import Hook, HookContext


class LoggingHook(Hook):
    """Emit lifecycle events through the Harness logging boundary."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        if logger is not None and not isinstance(logger, logging.Logger):
            raise TypeError("logger must be a logging.Logger or null")
        self._logger = logger or get_logger("hooks.lifecycle")

    async def before_model_call(self, context: HookContext) -> None:
        self._logger.info(
            "before_model_call task_id=%s step=%s model=%s",
            context.task_id,
            context.step,
            context.request.model if context.request is not None else None,
        )

    async def after_model_call(self, context: HookContext) -> None:
        self._logger.info(
            "after_model_call task_id=%s step=%s response_id=%s",
            context.task_id,
            context.step,
            context.response.response_id if context.response is not None else None,
        )

    async def before_tool_call(self, context: HookContext) -> None:
        self._logger.info(
            "before_tool_call task_id=%s step=%s tool=%s",
            context.task_id,
            context.step,
            context.tool_call.name if context.tool_call is not None else None,
        )

    async def after_tool_call(self, context: HookContext) -> None:
        self._logger.info(
            "after_tool_call task_id=%s step=%s tool=%s succeeded=%s",
            context.task_id,
            context.step,
            context.tool_call.name if context.tool_call is not None else None,
            context.tool_result.succeeded
            if context.tool_result is not None
            else None,
        )

    async def on_error(self, context: HookContext) -> None:
        self._logger.error(
            "on_error task_id=%s step=%s phase=%s error=%s",
            context.task_id,
            context.step,
            context.phase,
            context.error,
        )

    async def before_step(self, context: HookContext) -> None:
        self._logger.info(
            "before_step task_id=%s step=%s",
            context.task_id,
            context.step,
        )

    async def after_step(self, context: HookContext) -> None:
        self._logger.info(
            "after_step task_id=%s step=%s status=%s",
            context.task_id,
            context.step,
            context.status.value,
        )


@dataclass(frozen=True, slots=True)
class HookMetrics:
    """Point-in-time counters and elapsed durations from MetricsHook."""

    steps_started: int
    steps_completed: int
    model_calls_started: int
    model_calls_completed: int
    tool_calls_started: int
    tool_calls_completed: int
    errors: int
    model_elapsed_ms: float
    tool_elapsed_ms: float


class MetricsHook(Hook):
    """Collect process-local lifecycle counters without changing Agent state."""

    def __init__(self) -> None:
        self._steps_started = 0
        self._steps_completed = 0
        self._model_calls_started = 0
        self._model_calls_completed = 0
        self._tool_calls_started = 0
        self._tool_calls_completed = 0
        self._errors = 0
        self._model_elapsed_ms = 0.0
        self._tool_elapsed_ms = 0.0
        self._model_started_at: dict[tuple[str, int], float] = {}
        self._tool_started_at: dict[tuple[str, int], float] = {}

    @property
    def snapshot(self) -> HookMetrics:
        return HookMetrics(
            steps_started=self._steps_started,
            steps_completed=self._steps_completed,
            model_calls_started=self._model_calls_started,
            model_calls_completed=self._model_calls_completed,
            tool_calls_started=self._tool_calls_started,
            tool_calls_completed=self._tool_calls_completed,
            errors=self._errors,
            model_elapsed_ms=self._model_elapsed_ms,
            tool_elapsed_ms=self._tool_elapsed_ms,
        )

    async def before_step(self, context: HookContext) -> None:
        self._steps_started += 1

    async def after_step(self, context: HookContext) -> None:
        self._steps_completed += 1

    async def before_model_call(self, context: HookContext) -> None:
        self._model_calls_started += 1
        self._model_started_at[_key(context)] = perf_counter()

    async def after_model_call(self, context: HookContext) -> None:
        self._model_calls_completed += 1
        started_at = self._model_started_at.pop(_key(context), None)
        if started_at is not None:
            self._model_elapsed_ms += (perf_counter() - started_at) * 1000

    async def before_tool_call(self, context: HookContext) -> None:
        self._tool_calls_started += 1
        self._tool_started_at[_key(context)] = perf_counter()

    async def after_tool_call(self, context: HookContext) -> None:
        self._tool_calls_completed += 1
        started_at = self._tool_started_at.pop(_key(context), None)
        if started_at is not None:
            self._tool_elapsed_ms += (perf_counter() - started_at) * 1000

    async def on_error(self, context: HookContext) -> None:
        self._errors += 1
        self._model_started_at.pop(_key(context), None)
        self._tool_started_at.pop(_key(context), None)


def _key(context: HookContext) -> tuple[str, int]:
    return context.task_id, context.step
