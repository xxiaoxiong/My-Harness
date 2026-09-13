"""Versioned JSON checkpoint persistence with atomic file replacement."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from harness.model import MessageRole, ModelMessage
from harness.runtime.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    Checkpoint,
    CheckpointContext,
    CheckpointCorruptError,
    CheckpointNotFoundError,
    CheckpointStore,
    InvalidCheckpointTaskId,
)
from harness.state import AgentState, AgentStatus, TrajectoryEvent, TrajectoryEventKind
from harness.tools import ToolCall, ToolResult

_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class JsonCheckpointStore(CheckpointStore):
    """Store one latest-checkpoint JSON file per task ID."""

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)

    @property
    def directory(self) -> Path:
        return self._directory

    def save(self, checkpoint: Checkpoint) -> None:
        if not isinstance(checkpoint, Checkpoint):
            raise TypeError("checkpoint must be a Checkpoint")
        path = self._path_for(checkpoint.task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        payload = _checkpoint_to_payload(checkpoint)

        try:
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def load(self, task_id: str) -> Checkpoint:
        path = self._path_for(task_id)
        if not path.is_file():
            raise CheckpointNotFoundError(f"checkpoint not found: {task_id}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            checkpoint = _checkpoint_from_payload(payload)
            if checkpoint.task_id != task_id:
                raise CheckpointCorruptError(
                    "checkpoint task_id does not match its filename"
                )
            return checkpoint
        except CheckpointCorruptError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise CheckpointCorruptError(
                f"checkpoint is invalid: {task_id}"
            ) from error

    def _path_for(self, task_id: str) -> Path:
        if not isinstance(task_id, str) or not _SAFE_TASK_ID.fullmatch(task_id):
            raise InvalidCheckpointTaskId(
                "task_id must be 1-128 safe filename characters"
            )
        return self._directory / f"{task_id}.json"


def _checkpoint_to_payload(checkpoint: Checkpoint) -> dict[str, Any]:
    state = checkpoint.state
    return {
        "schema_version": checkpoint.schema_version,
        "saved_at": checkpoint.saved_at.isoformat(),
        "task_id": checkpoint.task_id,
        "status": checkpoint.status.value,
        "step": checkpoint.step,
        "state": {
            "task_id": state.task_id,
            "goal": state.goal,
            "current_step": state.current_step,
            "status": state.status.value,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in state.messages
            ],
            "tool_calls": [
                {"name": call.name, "arguments": dict(call.arguments)}
                for call in state.tool_calls
            ],
            "tool_results": [
                {
                    "name": result.name,
                    "arguments": dict(result.arguments),
                    "result": result.result,
                    "error": result.error,
                }
                for result in state.tool_results
            ],
            "created_at": state.created_at.isoformat(),
            "updated_at": state.updated_at.isoformat(),
            "final_answer": state.final_answer,
            "pending_tool_call": (
                {
                    "name": state.pending_tool_call.name,
                    "arguments": dict(state.pending_tool_call.arguments),
                }
                if state.pending_tool_call is not None
                else None
            ),
        },
        "trajectory": [
            {
                "sequence": event.sequence,
                "kind": event.kind.value,
                "occurred_at": event.occurred_at.isoformat(),
                "details": dict(event.details),
            }
            for event in state.trajectory
        ],
        "context": {
            "message_count": checkpoint.context.message_count,
            "total_chars": checkpoint.context.total_chars,
            "max_context_chars": checkpoint.context.max_context_chars,
            "truncated": checkpoint.context.truncated,
            "dropped_messages": checkpoint.context.dropped_messages,
            "summary": checkpoint.context.summary,
            "summarized_messages": checkpoint.context.summarized_messages,
        },
    }


def _checkpoint_from_payload(value: Any) -> Checkpoint:
    root = _object(value, "checkpoint")
    schema_version = _integer(root.get("schema_version"), "schema_version")
    if schema_version != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointCorruptError(
            f"unsupported checkpoint schema_version: {schema_version}"
        )

    state_data = _object(root.get("state"), "state")
    messages = [
        _message(item, f"state.messages[{index}]")
        for index, item in enumerate(_array(state_data.get("messages"), "state.messages"))
    ]
    tool_calls = [
        _tool_call(item, f"state.tool_calls[{index}]")
        for index, item in enumerate(
            _array(state_data.get("tool_calls"), "state.tool_calls")
        )
    ]
    tool_results = [
        _tool_result(item, f"state.tool_results[{index}]")
        for index, item in enumerate(
            _array(state_data.get("tool_results"), "state.tool_results")
        )
    ]
    trajectory = [
        _trajectory_event(item, f"trajectory[{index}]")
        for index, item in enumerate(_array(root.get("trajectory"), "trajectory"))
    ]
    try:
        state = AgentState(
            task_id=_string(state_data.get("task_id"), "state.task_id"),
            goal=_string(state_data.get("goal"), "state.goal"),
            current_step=_integer(
                state_data.get("current_step"), "state.current_step"
            ),
            status=AgentStatus(_string(state_data.get("status"), "state.status")),
            messages=messages,
            tool_calls=tool_calls,
            tool_results=tool_results,
            created_at=_datetime(state_data.get("created_at"), "state.created_at"),
            updated_at=_datetime(state_data.get("updated_at"), "state.updated_at"),
            trajectory=trajectory,
            final_answer=_optional_string(
                state_data.get("final_answer"), "state.final_answer"
            ),
            pending_tool_call=_optional_tool_call(
                state_data.get("pending_tool_call"),
                "state.pending_tool_call",
            ),
        )
        context_data = _object(root.get("context"), "context")
        context = CheckpointContext(
            message_count=_integer(
                context_data.get("message_count"), "context.message_count"
            ),
            total_chars=_integer(
                context_data.get("total_chars"), "context.total_chars"
            ),
            max_context_chars=_integer(
                context_data.get("max_context_chars"),
                "context.max_context_chars",
            ),
            truncated=_boolean(context_data.get("truncated"), "context.truncated"),
            dropped_messages=_integer(
                context_data.get("dropped_messages"),
                "context.dropped_messages",
            ),
            summary=_optional_string(context_data.get("summary"), "context.summary"),
            summarized_messages=_integer(
                context_data.get("summarized_messages"),
                "context.summarized_messages",
            ),
        )
        checkpoint = Checkpoint(
            state=state,
            context=context,
            saved_at=_datetime(root.get("saved_at"), "saved_at"),
            schema_version=schema_version,
        )
    except (TypeError, ValueError) as error:
        raise CheckpointCorruptError(str(error)) from error

    if _string(root.get("task_id"), "task_id") != checkpoint.task_id:
        raise CheckpointCorruptError("top-level task_id does not match state")
    if _string(root.get("status"), "status") != checkpoint.status.value:
        raise CheckpointCorruptError("top-level status does not match state")
    if _integer(root.get("step"), "step") != checkpoint.step:
        raise CheckpointCorruptError("top-level step does not match state")
    if [event.sequence for event in trajectory] != list(
        range(1, len(trajectory) + 1)
    ):
        raise CheckpointCorruptError("trajectory sequence is not contiguous")
    return checkpoint


def _message(value: Any, path: str) -> ModelMessage:
    item = _object(value, path)
    try:
        role = MessageRole(_string(item.get("role"), f"{path}.role"))
    except ValueError as error:
        raise CheckpointCorruptError(f"{path}.role is unsupported") from error
    return ModelMessage(role, _string(item.get("content"), f"{path}.content"))


def _tool_call(value: Any, path: str) -> ToolCall:
    item = _object(value, path)
    return ToolCall(
        _string(item.get("name"), f"{path}.name"),
        _object(item.get("arguments"), f"{path}.arguments"),
    )


def _optional_tool_call(value: Any, path: str) -> ToolCall | None:
    if value is None:
        return None
    return _tool_call(value, path)


def _tool_result(value: Any, path: str) -> ToolResult:
    item = _object(value, path)
    return ToolResult(
        name=_string(item.get("name"), f"{path}.name"),
        arguments=_object(item.get("arguments"), f"{path}.arguments"),
        result=item.get("result"),
        error=_optional_string(item.get("error"), f"{path}.error"),
    )


def _trajectory_event(value: Any, path: str) -> TrajectoryEvent:
    item = _object(value, path)
    try:
        kind = TrajectoryEventKind(_string(item.get("kind"), f"{path}.kind"))
    except ValueError as error:
        raise CheckpointCorruptError(f"{path}.kind is unsupported") from error
    return TrajectoryEvent(
        sequence=_integer(item.get("sequence"), f"{path}.sequence"),
        kind=kind,
        occurred_at=_datetime(item.get("occurred_at"), f"{path}.occurred_at"),
        details=_object(item.get("details"), f"{path}.details"),
    )


def _object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CheckpointCorruptError(f"{path} must be an object")
    return value


def _array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise CheckpointCorruptError(f"{path} must be an array")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise CheckpointCorruptError(f"{path} must be a string")
    return value


def _optional_string(value: Any, path: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise CheckpointCorruptError(f"{path} must be a string or null")
    return value


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CheckpointCorruptError(f"{path} must be an integer")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise CheckpointCorruptError(f"{path} must be a boolean")
    return value


def _datetime(value: Any, path: str) -> datetime:
    text = _string(value, path)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise CheckpointCorruptError(f"{path} must be an ISO datetime") from error
    if parsed.tzinfo is None:
        raise CheckpointCorruptError(f"{path} must be timezone-aware")
    return parsed
