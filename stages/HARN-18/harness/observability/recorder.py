"""Trace recording ports plus memory and structured-log adapters."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable

from harness.core import get_logger
from harness.observability.events import TraceEvent, TraceEventKind, TraceIdentity


class TraceRecorder(ABC):
    """Destination-independent boundary for structured trace events."""

    @abstractmethod
    def record(self, event: TraceEvent) -> None:
        """Persist or export one event."""


class InMemoryTraceRecorder(TraceRecorder):
    """Deterministic process-local recorder useful for learning and tests."""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    def record(self, event: TraceEvent) -> None:
        _require_event(event)
        self._events.append(event)

    def for_identity(self, identity: TraceIdentity) -> tuple[TraceEvent, ...]:
        if not isinstance(identity, TraceIdentity):
            raise TypeError("identity must be a TraceIdentity")
        return tuple(event for event in self._events if event.identity == identity)


class StructuredLogTraceRecorder(TraceRecorder):
    """Emit one compact JSON object per event through standard logging."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        if logger is not None and not isinstance(logger, logging.Logger):
            raise TypeError("logger must be a logging.Logger or null")
        self._logger = logger or get_logger("observability.trace")

    def record(self, event: TraceEvent) -> None:
        _require_event(event)
        self._logger.info(
            json.dumps(
                event.as_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        )


class CompositeTraceRecorder(TraceRecorder):
    """Fan out events to several adapters in registration order."""

    def __init__(self, recorders: Iterable[TraceRecorder]) -> None:
        self._recorders = tuple(recorders)
        if not self._recorders:
            raise ValueError("recorders must not be empty")
        if not all(isinstance(recorder, TraceRecorder) for recorder in self._recorders):
            raise TypeError("recorders must contain only TraceRecorder values")

    def record(self, event: TraceEvent) -> None:
        _require_event(event)
        for recorder in self._recorders:
            recorder.record(event)


class TraceEmitter:
    """Bind correlation identity once and create consistently shaped events."""

    def __init__(self, identity: TraceIdentity, recorder: TraceRecorder) -> None:
        if not isinstance(identity, TraceIdentity):
            raise TypeError("identity must be a TraceIdentity")
        if not isinstance(recorder, TraceRecorder):
            raise TypeError("recorder must be a TraceRecorder")
        self._identity = identity
        self._recorder = recorder

    @property
    def identity(self) -> TraceIdentity:
        return self._identity

    def emit(
        self,
        kind: TraceEventKind,
        *,
        step: int | None = None,
        details: dict[str, object] | None = None,
    ) -> TraceEvent:
        event = TraceEvent(
            kind=kind,
            identity=self._identity,
            step=step,
            details={} if details is None else details,
        )
        self._recorder.record(event)
        return event


def _require_event(event: TraceEvent) -> None:
    if not isinstance(event, TraceEvent):
        raise TypeError("event must be a TraceEvent")
