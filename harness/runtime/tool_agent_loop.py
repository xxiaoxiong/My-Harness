"""Reason-act-observe loop using registry-backed tool execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from harness.context import ContextBuilder
from harness.model import ModelProvider, ModelRequest
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

        self._provider = provider
        self._model = normalized_model
        self._max_steps = max_steps
        self._tool_executor = tool_executor
        self._context_builder = context_builder

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

        for _ in range(self._max_steps):
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
                return ToolAgentRunResult(state=state)

            state.record_tool_call(action)
            tool_result = await self._tool_executor.execute(action)
            state.record_tool_result(tool_result)
            state.append_message(
                self._context_builder.tool_result_message(tool_result)
            )

        state.reach_max_steps()
        return ToolAgentRunResult(state=state)


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
