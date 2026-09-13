"""Reason-act-observe loop using registry-backed tool execution."""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from harness.context import ContextBuilder, ContextBuildResult
from harness.hooks import HookContext, HookManager
from harness.model import ModelProvider, ModelRequest, ModelResponse
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

    @property
    def pending_tool_call(self) -> ToolCall | None:
        return self.state.pending_tool_call


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
        approval_required_tools: Collection[str] = (),
        hook_manager: HookManager | None = None,
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
        if hook_manager is not None and not isinstance(hook_manager, HookManager):
            raise TypeError("hook_manager must be a HookManager or null")
        if isinstance(approval_required_tools, str) or not isinstance(
            approval_required_tools, Collection
        ):
            raise TypeError("approval_required_tools must be a collection of names")
        if not all(
            isinstance(name, str) and name.strip()
            for name in approval_required_tools
        ):
            raise ValueError("approval-required tool names must not be empty")

        approval_names = frozenset(
            name.strip() for name in approval_required_tools
        )
        if approval_names and checkpoint_store is None:
            raise ValueError(
                "approval-required tools need a checkpoint_store"
            )

        self._provider = provider
        self._model = normalized_model
        self._max_steps = max_steps
        self._tool_executor = tool_executor
        self._context_builder = context_builder
        self._checkpoint_store = checkpoint_store
        self._approval_required_tools = approval_names
        self._hooks = hook_manager or HookManager()

    async def run(
        self,
        goal: str,
        *,
        task_id: str | None = None,
    ) -> ToolAgentRunResult:
        """Run until an answer, hard limit, or approval interrupt is reached."""

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

    async def resume(
        self,
        task_id: str,
        *,
        approve: bool | None = None,
    ) -> ToolAgentRunResult:
        """Load durable state and optionally resolve a pending approval."""

        if self._checkpoint_store is None:
            raise RuntimeError("resume requires a checkpoint_store")
        if approve is not None and not isinstance(approve, bool):
            raise TypeError("approve must be a boolean or null")
        checkpoint = self._checkpoint_store.load(task_id)
        if checkpoint.state.status is AgentStatus.WAITING_APPROVAL:
            return await self._resume_waiting(checkpoint, approve=approve)
        if approve is not None:
            raise ValueError("approval supplied for a task that is not waiting")
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
            attempted_step = state.current_step + 1
            phase = "before_step"
            request: ModelRequest | None = None
            response: ModelResponse | None = None
            tool_call: ToolCall | None = None
            tool_result: ToolResult | None = None
            try:
                await self._hooks.before_step(
                    self._hook_context(
                        state,
                        phase=phase,
                        step=attempted_step,
                    )
                )
                phase = "begin_step"
                state.begin_model_step()
                phase = "context_build"
                context = self._context_builder.build(
                    state,
                    tool_schemas=self._tool_executor.list_schemas(),
                )
                request = ModelRequest(
                    model=self._model,
                    messages=context.messages,
                )
                phase = "before_model_call"
                await self._hooks.before_model_call(
                    self._hook_context(state, phase=phase, request=request)
                )
                phase = "model_call"
                response = await self._provider.generate(request)
                phase = "after_model_call"
                await self._hooks.after_model_call(
                    self._hook_context(
                        state,
                        phase=phase,
                        request=request,
                        response=response,
                    )
                )
                phase = "record_model_response"
                state.record_model_call(
                    response,
                    input_message_count=len(context.messages),
                )
                state.append_message(response.message)
                phase = "parse_action"
                action = _parse_action(response.message.content)

                if isinstance(action, _FinalAnswer):
                    state.finish(action.answer)
                    phase = "checkpoint"
                    self._save_checkpoint(state, context)
                    phase = "after_step"
                    await self._hooks.after_step(
                        self._hook_context(state, phase=phase)
                    )
                    return ToolAgentRunResult(state=state)

                tool_call = action
                state.record_tool_call(tool_call)
                if tool_call.name in self._approval_required_tools:
                    state.interrupt_for_approval(tool_call)
                    phase = "checkpoint"
                    self._save_checkpoint(state, context)
                    phase = "after_step"
                    await self._hooks.after_step(
                        self._hook_context(
                            state,
                            phase=phase,
                            tool_call=tool_call,
                        )
                    )
                    return ToolAgentRunResult(state=state)

                phase = "before_tool_call"
                await self._hooks.before_tool_call(
                    self._hook_context(
                        state,
                        phase=phase,
                        tool_call=tool_call,
                    )
                )
                phase = "tool_call"
                tool_result = await self._tool_executor.execute(tool_call)
                phase = "after_tool_call"
                await self._hooks.after_tool_call(
                    self._hook_context(
                        state,
                        phase=phase,
                        tool_call=tool_call,
                        tool_result=tool_result,
                    )
                )
                phase = "record_tool_result"
                state.record_tool_result(tool_result)
                state.append_message(
                    self._context_builder.tool_result_message(tool_result)
                )

                if state.current_step >= self._max_steps:
                    state.reach_max_steps()
                phase = "checkpoint"
                self._save_checkpoint(state, context)
                phase = "after_step"
                await self._hooks.after_step(
                    self._hook_context(
                        state,
                        phase=phase,
                        tool_call=tool_call,
                        tool_result=tool_result,
                    )
                )
                if state.status is AgentStatus.MAX_STEPS_REACHED:
                    return ToolAgentRunResult(state=state)
            except Exception as error:
                await self._notify_error(
                    state,
                    error,
                    phase=phase,
                    step=attempted_step,
                    request=request,
                    response=response,
                    tool_call=tool_call,
                    tool_result=tool_result,
                )
                raise

        raise RuntimeError("agent loop exited without a terminal state")

    async def _resume_waiting(
        self,
        checkpoint: Checkpoint,
        *,
        approve: bool | None,
    ) -> ToolAgentRunResult:
        state = checkpoint.state
        if approve is None:
            return ToolAgentRunResult(state=state)

        phase = "resume"
        call: ToolCall | None = None
        tool_result: ToolResult | None = None
        try:
            call = state.resume_from_approval(approved=approve)
            if approve:
                phase = "before_tool_call"
                await self._hooks.before_tool_call(
                    self._hook_context(
                        state,
                        phase=phase,
                        tool_call=call,
                    )
                )
                phase = "tool_call"
                tool_result = await self._tool_executor.execute(call)
                phase = "after_tool_call"
                await self._hooks.after_tool_call(
                    self._hook_context(
                        state,
                        phase=phase,
                        tool_call=call,
                        tool_result=tool_result,
                    )
                )
            else:
                tool_result = ToolResult(
                    name=call.name,
                    arguments=call.arguments,
                    error="tool call rejected by user",
                )
            phase = "record_tool_result"
            state.record_tool_result(tool_result)
            state.append_message(
                self._context_builder.tool_result_message(tool_result)
            )

            if state.current_step >= self._max_steps:
                state.reach_max_steps()
            phase = "checkpoint"
            self._save_checkpoint_with_context(state, checkpoint)
            if state.status is AgentStatus.MAX_STEPS_REACHED:
                return ToolAgentRunResult(state=state)
        except Exception as error:
            await self._notify_error(
                state,
                error,
                phase=phase,
                tool_call=call,
                tool_result=tool_result,
            )
            raise
        return await self._run_state(state)

    def _hook_context(
        self,
        state: AgentState,
        *,
        phase: str,
        step: int | None = None,
        request: ModelRequest | None = None,
        response: ModelResponse | None = None,
        tool_call: ToolCall | None = None,
        tool_result: ToolResult | None = None,
        error: Exception | None = None,
    ) -> HookContext:
        return HookContext(
            task_id=state.task_id,
            goal=state.goal,
            step=state.current_step if step is None else step,
            status=state.status,
            phase=phase,
            request=request,
            response=response,
            tool_call=tool_call,
            tool_result=tool_result,
            error=error,
        )

    async def _notify_error(
        self,
        state: AgentState,
        error: Exception,
        *,
        phase: str,
        step: int | None = None,
        request: ModelRequest | None = None,
        response: ModelResponse | None = None,
        tool_call: ToolCall | None = None,
        tool_result: ToolResult | None = None,
    ) -> None:
        try:
            await self._hooks.on_error(
                self._hook_context(
                    state,
                    phase=phase,
                    step=step,
                    request=request,
                    response=response,
                    tool_call=tool_call,
                    tool_result=tool_result,
                    error=error,
                )
            )
        except Exception as hook_error:
            error.add_note(f"on_error hook also failed: {hook_error}")

    def _save_checkpoint(
        self,
        state: AgentState,
        context: ContextBuildResult,
    ) -> None:
        if self._checkpoint_store is not None:
            self._checkpoint_store.save(Checkpoint.capture(state, context))

    def _save_checkpoint_with_context(
        self,
        state: AgentState,
        previous: Checkpoint,
    ) -> None:
        if self._checkpoint_store is not None:
            self._checkpoint_store.save(
                Checkpoint(
                    state=state,
                    context=previous.context,
                    saved_at=datetime.now(UTC),
                )
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
