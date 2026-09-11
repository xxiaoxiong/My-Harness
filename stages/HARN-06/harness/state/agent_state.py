"""The system-of-record state for one HARN-05 Agent run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, unique
from uuid import uuid4

from harness.core.types import JsonValue
from harness.model import ModelMessage, ModelResponse
from harness.state.trajectory import TrajectoryEvent, TrajectoryEventKind
from harness.tools import ToolCall, ToolResult


@unique
class AgentStatus(str, Enum):
    """Lifecycle status stored as part of Agent state."""

    RUNNING = "running"
    FINISHED = "finished"
    MAX_STEPS_REACHED = "max_steps_reached"


@dataclass(slots=True)
class AgentState:
    """Mutable truth for one run, distinct from context and trajectory."""

    task_id: str
    goal: str
    current_step: int
    status: AgentStatus
    messages: list[ModelMessage]
    tool_calls: list[ToolCall]
    tool_results: list[ToolResult]
    created_at: datetime
    updated_at: datetime
    trajectory: list[TrajectoryEvent] = field(default_factory=list)
    final_answer: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise ValueError("goal must not be empty")
        if isinstance(self.current_step, bool) or not isinstance(self.current_step, int):
            raise TypeError("current_step must be an integer")
        if self.current_step < 0:
            raise ValueError("current_step must not be negative")
        if not isinstance(self.status, AgentStatus):
            raise TypeError("status must be an AgentStatus")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("state timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be before created_at")

        self.task_id = self.task_id.strip()
        self.goal = self.goal.strip()
        self.messages = list(self.messages)
        self.tool_calls = list(self.tool_calls)
        self.tool_results = list(self.tool_results)
        self.trajectory = list(self.trajectory)

    @classmethod
    def start(
        cls,
        *,
        goal: str,
        messages: list[ModelMessage],
        task_id: str | None = None,
    ) -> AgentState:
        """Create a running state before the first model call."""

        now = _utc_now()
        return cls(
            task_id=task_id or uuid4().hex,
            goal=goal,
            current_step=0,
            status=AgentStatus.RUNNING,
            messages=messages,
            tool_calls=[],
            tool_results=[],
            created_at=now,
            updated_at=now,
        )

    def begin_model_step(self) -> None:
        """Advance the model-call counter while the run is active."""

        self._require_running()
        self.current_step += 1
        self._touch()

    def append_message(self, message: ModelMessage) -> None:
        """Add a message to the system state."""

        self._require_running()
        if not isinstance(message, ModelMessage):
            raise TypeError("message must be a ModelMessage")
        self.messages.append(message)
        self._touch()

    def record_model_call(
        self,
        response: ModelResponse,
        *,
        input_message_count: int,
    ) -> None:
        """Record one completed provider call without duplicating context."""

        self._require_running()
        self._append_event(
            TrajectoryEventKind.MODEL_CALL,
            {
                "step": self.current_step,
                "model": response.model,
                "response_id": response.response_id,
                "input_message_count": input_message_count,
            },
        )

    def record_tool_call(self, call: ToolCall) -> None:
        """Add a requested action to state and trajectory."""

        self._require_running()
        self.tool_calls.append(call)
        self._append_event(
            TrajectoryEventKind.TOOL_CALL,
            {"name": call.name, "arguments": dict(call.arguments)},
        )

    def record_tool_result(self, result: ToolResult) -> None:
        """Add an execution observation to state and trajectory."""

        self._require_running()
        self.tool_results.append(result)
        self._append_event(
            TrajectoryEventKind.TOOL_RESULT,
            {
                "name": result.name,
                "arguments": dict(result.arguments),
                "result": result.result,
                "error": result.error,
            },
        )

    def finish(self, answer: str) -> None:
        """Store the final answer and transition to FINISHED."""

        self._require_running()
        normalized_answer = answer.strip()
        if not normalized_answer:
            raise ValueError("final answer must not be empty")
        self.final_answer = normalized_answer
        self.status = AgentStatus.FINISHED
        self._append_event(
            TrajectoryEventKind.FINAL_ANSWER,
            {"answer": normalized_answer},
        )

    def reach_max_steps(self) -> None:
        """Transition a still-running state to its hard stop."""

        self._require_running()
        self.status = AgentStatus.MAX_STEPS_REACHED
        self._append_event(
            TrajectoryEventKind.MAX_STEPS_REACHED,
            {"step": self.current_step},
        )

    def _append_event(
        self,
        kind: TrajectoryEventKind,
        details: dict[str, JsonValue],
    ) -> None:
        now = _utc_now()
        self.trajectory.append(
            TrajectoryEvent(
                sequence=len(self.trajectory) + 1,
                kind=kind,
                occurred_at=now,
                details=details,
            )
        )
        self.updated_at = now

    def _require_running(self) -> None:
        if self.status is not AgentStatus.RUNNING:
            raise RuntimeError(f"agent state is not running: {self.status.value}")

    def _touch(self) -> None:
        self.updated_at = _utc_now()


def _utc_now() -> datetime:
    return datetime.now(UTC)
