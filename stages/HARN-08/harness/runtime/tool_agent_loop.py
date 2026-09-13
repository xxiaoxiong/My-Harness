"""Reason-act-observe loop using registry-backed tool execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from harness.context import ContextBuilder, ContextBuildResult
from harness.model import ModelProvider, ModelRequest
from harness.runtime.checkpoint import Checkpoint, CheckpointStore
from harness.state import AgentState, AgentStatus
from harness.tools import ToolCall, ToolExecutor, ToolResult


@dataclass(frozen=True, slots=True)
class ToolAgentRunResult:
    """Expose the completed state with convenient compatibility properties."""

    state: AgentState

    @property
    def goal(self) -> str:
        return self.state.goal

    @property
    def status(self) -> AgentStatus:
        return self.state.status

    @property
    def steps_executed(self) -> int:
        return self.state.current_step

    @property
    def tool_calls_executed(self) -> int:
        return len(self.state.tool_calls)

    @property
    def final_answer(self) -> str | None:
        return self.state.final_answer

    @property
    def last_tool_result(self) -> ToolResult | None:
        return self.state.tool_results[-1] if self.state.tool_results else None


@dataclass(frozen=True, slots=True)
class _FinalAnswer:
    answer: str


class InvalidAgentAction(ValueError):
    """Raised when model output does not follow the HARN-03 JSON protocol."""

    def __init__(self, output: str, reason: str) -> None:
        self.output = output
        self.reason = reason
        super().__init__(f"invalid agent action: {reason}")


class ToolAgentLoop:
    """Let a model call registered tools without knowing their implementations."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        model: str,
        max_steps: int,
        tool_executor: ToolExecutor,
        context_builder: ContextBuilder,
        checkpoint_store: CheckpointStore | None = None,
    ) -> None:
        if not isinstance(model, str):
            raise TypeError("model must be a string")
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("model must not be empty")
        if isinstance(max_steps, bool) or not isinstance(max_steps, int):
            raise TypeError("max_steps must be an integer")
        if max_steps <= 0:
            raise ValueError("max_steps must be greater than zero")
        if not isinstance(tool_executor, ToolExecutor):
            raise TypeError("tool_executor must be a ToolExecutor")
        if not isinstance(context_builder, ContextBuilder):
            raise TypeError("context_builder must be a ContextBuilder")
        if checkpoint_store is not None and not isinstance(
            checkpoint_store, CheckpointStore
        ):
            raise TypeError("checkpoint_store must be a CheckpointStore or null")

        self._provider = provider
        self._model = normalized_model
        self._max_steps = max_steps
        self._tool_executor = tool_executor
        self._context_builder = context_builder
        self._checkpoint_store = checkpoint_store

    async def run(
        self,
        goal: str,
        *,
        task_id: str | None = None,
    ) -> ToolAgentRunResult:
        """Run until the model answers or the model-call limit is reached."""

        if not isinstance(goal, str):
            raise TypeError("goal must be a string")
        normalized_goal = goal.strip()
        if not normalized_goal:
            raise ValueError("goal must not be empty")

        state = AgentState.start(
            goal=normalized_goal,
            task_id=task_id,
            messages=[],
        )
        return await self._run_state(state)

    async def resume(self, task_id: str) -> ToolAgentRunResult:
        """Load the latest durable state for a task and continue it."""

        if self._checkpoint_store is None:
            raise RuntimeError("resume requires a checkpoint_store")
        checkpoint = self._checkpoint_store.load(task_id)
        if checkpoint.state.status is not AgentStatus.RUNNING:
            return ToolAgentRunResult(state=checkpoint.state)
        if checkpoint.state.current_step >= self._max_steps:
            checkpoint.state.reach_max_steps()
            self._checkpoint_store.save(
                Checkpoint(
                    state=checkpoint.state,
                    context=checkpoint.context,
                    saved_at=datetime.now(UTC),
                )
            )
            return ToolAgentRunResult(state=checkpoint.state)
        return await self._run_state(checkpoint.state)

    async def _run_state(self, state: AgentState) -> ToolAgentRunResult:
        while state.current_step < self._max_steps:
            state.begin_model_step()
            context = self._context_builder.build(
                state,
                tool_schemas=self._tool_executor.list_schemas(),
            )
            response = await self._provider.generate(
                ModelRequest(model=self._model, messages=context.messages)
            )
            state.record_model_call(
                response,
                input_message_count=len(context.messages),
            )
            state.append_message(response.message)
            action = _parse_action(response.message.content)

            if isinstance(action, _FinalAnswer):
                state.finish(action.answer)
                self._save_checkpoint(state, context)
                return ToolAgentRunResult(state=state)

            state.record_tool_call(action)
            tool_result = await self._tool_executor.execute(action)
            state.record_tool_result(tool_result)
            state.append_message(
                self._context_builder.tool_result_message(tool_result)
            )

            if state.current_step >= self._max_steps:
                state.reach_max_steps()
            self._save_checkpoint(state, context)
            if state.status is AgentStatus.MAX_STEPS_REACHED:
                return ToolAgentRunResult(state=state)

        raise RuntimeError("agent loop exited without a terminal state")

    def _save_checkpoint(
        self,
        state: AgentState,
        context: ContextBuildResult,
    ) -> None:
        if self._checkpoint_store is not None:
            self._checkpoint_store.save(Checkpoint.capture(state, context))


def _parse_action(output: str) -> ToolCall | _FinalAnswer:
    try:
        value: Any = json.loads(output)
    except json.JSONDecodeError as error:
        raise InvalidAgentAction(output, "output must be valid JSON") from error

    if not isinstance(value, dict):
        raise InvalidAgentAction(output, "output must be a JSON object")

    action_type = value.get("type")
    if action_type == "tool_call":
        if set(value) != {"type", "name", "arguments"}:
            raise InvalidAgentAction(
                output,
                "tool_call must contain only type, name, and arguments",
            )
        name = value["name"]
        arguments = value["arguments"]
        if not isinstance(name, str) or not name.strip():
            raise InvalidAgentAction(output, "tool_call name must be a string")
        if not isinstance(arguments, dict):
            raise InvalidAgentAction(output, "tool_call arguments must be an object")
        return ToolCall(name=name, arguments=arguments)

    if action_type == "final_answer":
        if set(value) != {"type", "answer"}:
            raise InvalidAgentAction(
                output,
                "final_answer must contain only type and answer",
            )
        answer = value["answer"]
        if not isinstance(answer, str) or not answer.strip():
            raise InvalidAgentAction(output, "final answer must be a nonempty string")
        return _FinalAnswer(answer.strip())

    raise InvalidAgentAction(
        output,
        "type must be 'tool_call' or 'final_answer'",
    )
