"""Build one bounded model context from state and tool definitions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

from harness.context.summarizer import HistorySummarizer
from harness.model import MessageRole, ModelMessage
from harness.state import AgentState
from harness.tools import ToolResult, ToolSchema

DEFAULT_TOOL_AGENT_SYSTEM_PROMPT: Final = """You are controlled by a tool agent loop.
Reply with one JSON object and no surrounding prose.
To call an available tool:
{"type":"tool_call","name":"tool_name","arguments":{}}
When the goal is complete:
{"type":"final_answer","answer":"your answer"}
A tool result, including any error, will be provided in the history."""

_TRUNCATION_MARKER = "[truncated]"


@dataclass(frozen=True, slots=True)
class ContextBuildResult:
    """A model-ready context plus transparent budget metadata."""

    messages: tuple[ModelMessage, ...]
    total_chars: int
    max_context_chars: int
    truncated: bool
    dropped_messages: int
    summary: str | None
    summarized_messages: int


class ContextBuilder:
    """Own prompt composition and a simple character-based context budget."""

    def __init__(
        self,
        *,
        system_prompt: str = DEFAULT_TOOL_AGENT_SYSTEM_PROMPT,
        max_context_chars: int,
        summarizer: HistorySummarizer | None = None,
        recent_history_messages: int = 4,
        summary_max_chars: int = 400,
    ) -> None:
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("system_prompt must not be empty")
        if isinstance(max_context_chars, bool) or not isinstance(
            max_context_chars, int
        ):
            raise TypeError("max_context_chars must be an integer")
        if max_context_chars <= 0:
            raise ValueError("max_context_chars must be greater than zero")
        if summarizer is not None and not isinstance(summarizer, HistorySummarizer):
            raise TypeError("summarizer must be a HistorySummarizer or null")
        if (
            isinstance(recent_history_messages, bool)
            or not isinstance(recent_history_messages, int)
        ):
            raise TypeError("recent_history_messages must be an integer")
        if recent_history_messages <= 0:
            raise ValueError("recent_history_messages must be greater than zero")
        if isinstance(summary_max_chars, bool) or not isinstance(
            summary_max_chars, int
        ):
            raise TypeError("summary_max_chars must be an integer")
        if summary_max_chars <= 0:
            raise ValueError("summary_max_chars must be greater than zero")

        self._system_prompt = system_prompt.strip()
        self._max_context_chars = max_context_chars
        self._summarizer = summarizer
        self._recent_history_messages = recent_history_messages
        self._summary_max_chars = summary_max_chars

    @property
    def max_context_chars(self) -> int:
        return self._max_context_chars

    def build(
        self,
        state: AgentState,
        *,
        tool_schemas: tuple[ToolSchema, ...],
    ) -> ContextBuildResult:
        """Compose System, Goal, History, Tools, and Current State."""

        if not isinstance(state, AgentState):
            raise TypeError("state must be an AgentState")
        if not all(isinstance(schema, ToolSchema) for schema in tool_schemas):
            raise TypeError("tool_schemas must contain only ToolSchema values")

        system_message = ModelMessage(
            MessageRole.DEVELOPER,
            f"System Prompt:\n{self._system_prompt}",
        )
        goal_message = ModelMessage(
            MessageRole.USER,
            f"Goal:\n{state.goal}",
        )
        tools_message = ModelMessage(
            MessageRole.DEVELOPER,
            "Tool Definitions:\n"
            + json.dumps(
                [schema.as_dict() for schema in tool_schemas],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        state_message = ModelMessage(
            MessageRole.DEVELOPER,
            "Current State:\n"
            + json.dumps(
                {
                    "task_id": state.task_id,
                    "current_step": state.current_step,
                    "status": state.status.value,
                    "tool_calls": len(state.tool_calls),
                    "tool_results": len(state.tool_results),
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

        fixed_messages = (
            system_message,
            goal_message,
            tools_message,
            state_message,
        )
        fixed_chars = _count_chars(fixed_messages)
        if fixed_chars > self._max_context_chars:
            fitted = _fit_fixed_messages(
                fixed_messages,
                max_chars=self._max_context_chars,
            )
            return ContextBuildResult(
                messages=fitted,
                total_chars=_count_chars(fitted),
                max_context_chars=self._max_context_chars,
                truncated=True,
                dropped_messages=len(state.messages),
                summary=None,
                summarized_messages=0,
            )

        history_budget = self._max_context_chars - fixed_chars
        if (
            self._summarizer is not None
            and _count_chars(tuple(state.messages)) > history_budget
        ):
            summary_message, history, summarized_messages = _compact_history(
                tuple(state.messages),
                max_chars=history_budget,
                recent_history_messages=self._recent_history_messages,
                summary_max_chars=self._summary_max_chars,
                summarizer=self._summarizer,
            )
            messages = (
                system_message,
                goal_message,
                *((summary_message,) if summary_message is not None else ()),
                *history,
                tools_message,
                state_message,
            )
            summary = (
                summary_message.content.split("\n", 1)[1]
                if summary_message is not None
                else None
            )
            return ContextBuildResult(
                messages=messages,
                total_chars=_count_chars(messages),
                max_context_chars=self._max_context_chars,
                truncated=True,
                dropped_messages=summarized_messages,
                summary=summary,
                summarized_messages=summarized_messages if summary else 0,
            )

        history, history_truncated = _latest_history(
            tuple(state.messages),
            max_chars=history_budget,
        )
        messages = (
            system_message,
            goal_message,
            *history,
            tools_message,
            state_message,
        )
        return ContextBuildResult(
            messages=messages,
            total_chars=_count_chars(messages),
            max_context_chars=self._max_context_chars,
            truncated=history_truncated,
            dropped_messages=len(state.messages) - len(history),
            summary=None,
            summarized_messages=0,
        )

    @staticmethod
    def tool_result_message(result: ToolResult) -> ModelMessage:
        """Convert one observation into model-visible history."""

        if not isinstance(result, ToolResult):
            raise TypeError("result must be a ToolResult")
        return ModelMessage(
            MessageRole.USER,
            json.dumps(
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
            ),
        )


def _latest_history(
    messages: tuple[ModelMessage, ...],
    *,
    max_chars: int,
) -> tuple[tuple[ModelMessage, ...], bool]:
    selected_reversed: list[ModelMessage] = []
    remaining = max_chars
    truncated = False

    for message in reversed(messages):
        length = len(message.content)
        if length <= remaining:
            selected_reversed.append(message)
            remaining -= length
            continue
        if remaining > 0:
            selected_reversed.append(
                ModelMessage(
                    message.role,
                    _keep_tail(message.content, remaining),
                )
            )
            remaining = 0
        truncated = True
        break

    if len(selected_reversed) < len(messages):
        truncated = True
    return tuple(reversed(selected_reversed)), truncated


def _compact_history(
    messages: tuple[ModelMessage, ...],
    *,
    max_chars: int,
    recent_history_messages: int,
    summary_max_chars: int,
    summarizer: HistorySummarizer,
) -> tuple[ModelMessage | None, tuple[ModelMessage, ...], int]:
    summary_prefix = "History Summary:\n"
    summary_slot = min(
        len(summary_prefix) + summary_max_chars,
        max_chars // 3,
    )
    if summary_slot <= len(summary_prefix):
        history, _ = _latest_history(messages, max_chars=max_chars)
        return None, history, len(messages) - len(history)

    recent_budget = max_chars - summary_slot
    base_old_count = max(0, len(messages) - recent_history_messages)
    recent_candidates = messages[base_old_count:]
    recent, candidate_summary_count = _recent_history_for_compaction(
        recent_candidates,
        max_chars=recent_budget,
    )
    summarized_messages = base_old_count + candidate_summary_count
    summary = summarizer.summarize(
        messages[:summarized_messages],
        max_chars=summary_slot - len(summary_prefix),
    )
    summary_message = (
        ModelMessage(MessageRole.DEVELOPER, summary_prefix + summary)
        if summary
        else None
    )
    return summary_message, recent, summarized_messages


def _recent_history_for_compaction(
    messages: tuple[ModelMessage, ...],
    *,
    max_chars: int,
) -> tuple[tuple[ModelMessage, ...], int]:
    selected_reversed: list[ModelMessage] = []
    remaining = max_chars
    first_selected_index = len(messages)
    partial_first_message = False

    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if len(message.content) <= remaining:
            selected_reversed.append(message)
            first_selected_index = index
            remaining -= len(message.content)
            continue
        if remaining > 0:
            selected_reversed.append(
                ModelMessage(
                    message.role,
                    _keep_tail(message.content, remaining),
                )
            )
            first_selected_index = index
            partial_first_message = True
        break

    summarized_messages = first_selected_index + int(partial_first_message)
    return tuple(reversed(selected_reversed)), summarized_messages


def _fit_fixed_messages(
    messages: tuple[ModelMessage, ...],
    *,
    max_chars: int,
) -> tuple[ModelMessage, ...]:
    lengths = [len(message.content) for message in messages]
    allocations = [0] * len(messages)
    active = set(range(len(messages)))
    remaining = max_chars

    while active and remaining > 0:
        share = max(1, remaining // len(active))
        progressed = False
        for index in tuple(active):
            needed = lengths[index] - allocations[index]
            granted = min(needed, share, remaining)
            allocations[index] += granted
            remaining -= granted
            progressed = progressed or granted > 0
            if allocations[index] == lengths[index]:
                active.remove(index)
            if remaining == 0:
                break
        if not progressed:
            break

    return tuple(
        ModelMessage(message.role, _keep_head(message.content, allocation))
        for message, allocation in zip(messages, allocations, strict=True)
    )


def _keep_head(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= len(_TRUNCATION_MARKER):
        return _TRUNCATION_MARKER[:limit]
    return text[: limit - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def _keep_tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= len(_TRUNCATION_MARKER):
        return _TRUNCATION_MARKER[-limit:]
    return _TRUNCATION_MARKER + text[-(limit - len(_TRUNCATION_MARKER)) :]


def _count_chars(messages: tuple[ModelMessage, ...]) -> int:
    return sum(len(message.content) for message in messages)
