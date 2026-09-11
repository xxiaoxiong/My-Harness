"""Reason-act-observe loop using the hard-coded HARN-03 calculator."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from harness.model import MessageRole, ModelMessage, ModelProvider, ModelRequest
from harness.runtime.agent_loop import AgentRunStatus
from harness.tools import ToolCall, ToolResult, execute_tool_call


@dataclass(frozen=True, slots=True)
class ToolAgentRunResult:
    """Outcome of a bounded tool-agent run."""

    goal: str
    status: AgentRunStatus
    steps_executed: int
    tool_calls_executed: int
    final_answer: str | None
    last_tool_result: ToolResult | None


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
    """Let the model call a calculator and observe the result before answering."""

    _INSTRUCTION = """You are controlled by a calculator agent loop.
Reply with one JSON object and no surrounding prose.
To call the only available tool:
{"type":"tool_call","name":"calculator","arguments":{"expression":"2 + 2"}}
When the goal is complete:
{"type":"final_answer","answer":"your answer"}
Use the calculator for arithmetic. A tool result, including any error, will be
returned as the next user message."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        model: str,
        max_steps: int,
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

        self._provider = provider
        self._model = normalized_model
        self._max_steps = max_steps

    async def run(self, goal: str) -> ToolAgentRunResult:
        """Run until the model answers or the model-call limit is reached."""

        if not isinstance(goal, str):
            raise TypeError("goal must be a string")
        normalized_goal = goal.strip()
        if not normalized_goal:
            raise ValueError("goal must not be empty")

        messages = [
            ModelMessage(MessageRole.DEVELOPER, self._INSTRUCTION),
            ModelMessage(MessageRole.USER, f"Goal:\n{normalized_goal}"),
        ]
        tool_calls_executed = 0
        last_tool_result: ToolResult | None = None

        for step in range(1, self._max_steps + 1):
            response = await self._provider.generate(
                ModelRequest(model=self._model, messages=tuple(messages))
            )
            action = _parse_action(response.message.content)

            if isinstance(action, _FinalAnswer):
                return ToolAgentRunResult(
                    goal=normalized_goal,
                    status=AgentRunStatus.FINISHED,
                    steps_executed=step,
                    tool_calls_executed=tool_calls_executed,
                    final_answer=action.answer,
                    last_tool_result=last_tool_result,
                )

            last_tool_result = execute_tool_call(action)
            tool_calls_executed += 1
            if step < self._max_steps:
                messages.extend(
                    (
                        ModelMessage(
                            MessageRole.ASSISTANT,
                            response.message.content,
                        ),
                        ModelMessage(
                            MessageRole.USER,
                            _serialize_tool_result(last_tool_result),
                        ),
                    )
                )

        return ToolAgentRunResult(
            goal=normalized_goal,
            status=AgentRunStatus.MAX_STEPS_REACHED,
            steps_executed=self._max_steps,
            tool_calls_executed=tool_calls_executed,
            final_answer=None,
            last_tool_result=last_tool_result,
        )


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


def _serialize_tool_result(result: ToolResult) -> str:
    return json.dumps(
        {
            "type": "tool_result",
            "name": result.name,
            "arguments": result.arguments,
            "result": result.result,
            "error": result.error,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
