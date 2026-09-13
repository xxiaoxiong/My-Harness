"""Human-readable projection of a flat trace into Task -> Step children."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from harness.observability.events import TraceEvent, TraceEventKind

_MODEL_EVENTS = {
    TraceEventKind.MODEL_CALL_STARTED,
    TraceEventKind.MODEL_CALL_COMPLETED,
    TraceEventKind.TOKEN_USAGE,
    TraceEventKind.TTFT,
    TraceEventKind.CONTEXT_SIZE,
}
_TOOL_EVENTS = {TraceEventKind.TOOL_CALL, TraceEventKind.TOOL_RESULT}


def format_trace_tree(events: Iterable[TraceEvent]) -> dict[str, Any]:
    """Group one correlated event stream as Task -> Step -> Model + Tool."""

    trace = tuple(events)
    if not trace:
        raise ValueError("events must not be empty")
    if not all(isinstance(event, TraceEvent) for event in trace):
        raise TypeError("events must contain only TraceEvent values")
    identity = trace[0].identity
    if any(event.identity != identity for event in trace):
        raise ValueError("events must belong to one trace identity")

    task_events: list[dict[str, Any]] = []
    steps: dict[int, dict[str, Any]] = {}
    for event in trace:
        record = event.as_dict()
        if event.step is None:
            task_events.append(record)
            continue
        node = steps.setdefault(
            event.step,
            {"step": event.step, "events": [], "model": [], "tools": []},
        )
        component = event.details.get("component")
        if event.kind in _MODEL_EVENTS or (
            event.kind is TraceEventKind.LATENCY
            and component in {"model", "model_hook_span"}
        ):
            node["model"].append(record)
        elif event.kind in _TOOL_EVENTS or (
            event.kind is TraceEventKind.LATENCY and component == "tool"
        ):
            node["tools"].append(record)
        else:
            node["events"].append(record)

    return {
        "task_id": identity.task_id,
        "trace_id": identity.trace_id,
        "session_id": identity.session_id,
        "events": task_events,
        "steps": [steps[number] for number in sorted(steps)],
    }
