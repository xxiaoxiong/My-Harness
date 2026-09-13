"""Structured observability contracts and local adapters."""

from harness.observability.adapters import (
    TracingHook,
    TracingRetryObserver,
    TracingTaskStore,
    classify_failure,
)
from harness.observability.events import (
    FailureLayer,
    TraceEvent,
    TraceEventKind,
    TraceIdentity,
)
from harness.observability.recorder import (
    CompositeTraceRecorder,
    InMemoryTraceRecorder,
    StructuredLogTraceRecorder,
    TraceEmitter,
    TraceRecorder,
)
from harness.observability.tree import format_trace_tree

__all__ = [
    "CompositeTraceRecorder",
    "FailureLayer",
    "InMemoryTraceRecorder",
    "StructuredLogTraceRecorder",
    "TraceEmitter",
    "TraceEvent",
    "TraceEventKind",
    "TraceIdentity",
    "TraceRecorder",
    "TracingHook",
    "TracingRetryObserver",
    "TracingTaskStore",
    "classify_failure",
    "format_trace_tree",
]
