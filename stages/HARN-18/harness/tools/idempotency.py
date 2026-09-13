"""Tool-result idempotency boundary for retry and replay safety."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass

from harness.tools.types import ToolCall, ToolResult


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    key: str
    call: ToolCall
    result: ToolResult

    def __post_init__(self) -> None:
        _key(self.key)
        if not isinstance(self.call, ToolCall):
            raise TypeError("idempotency call must be a ToolCall")
        if not isinstance(self.result, ToolResult):
            raise TypeError("idempotency result must be a ToolResult")
        if self.result.name != self.call.name:
            raise ValueError("idempotency result must match its Tool Call")
        object.__setattr__(self, "key", self.key.strip())


class IdempotencyStoreError(RuntimeError):
    """Base failure for Tool idempotency storage."""


class IdempotencyConflictError(IdempotencyStoreError):
    """Raised when one key is reused for a different Tool Call or result."""


class IdempotencyStore(ABC):
    @abstractmethod
    def get(self, key: str) -> IdempotencyRecord | None:
        """Return the completed Tool record for a key, when present."""

    @abstractmethod
    def save(self, record: IdempotencyRecord) -> None:
        """Persist a completed Tool record without conflicting overwrite."""


class InMemoryIdempotencyStore(IdempotencyStore):
    def __init__(self) -> None:
        self._records: dict[str, IdempotencyRecord] = {}

    def get(self, key: str) -> IdempotencyRecord | None:
        return self._records.get(_key(key))

    def save(self, record: IdempotencyRecord) -> None:
        if not isinstance(record, IdempotencyRecord):
            raise TypeError("record must be an IdempotencyRecord")
        existing = self._records.get(record.key)
        if existing is not None and existing != record:
            raise IdempotencyConflictError(
                f"idempotency key has a different completed call: {record.key}"
            )
        self._records[record.key] = record

    def __len__(self) -> int:
        return len(self._records)


def tool_call_idempotency_key(task_id: str, call: ToolCall) -> str:
    """Derive one stable key for an exact Tool Call within a Task."""

    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("idempotency task_id must not be empty")
    if not isinstance(call, ToolCall):
        raise TypeError("call must be a ToolCall")
    canonical = json.dumps(
        {
            "task_id": task_id.strip(),
            "tool": call.name,
            "arguments": dict(call.arguments),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _key(key: str) -> str:
    if not isinstance(key, str) or not key.strip():
        raise ValueError("idempotency key must not be empty")
    return key.strip()
