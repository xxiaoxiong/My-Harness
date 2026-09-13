"""HARN-11 Demo: Runtime policy decides whether Tool intent may execute."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

from harness import (
    ContextBuilder,
    JsonCheckpointStore,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    PermissionDecision,
    PermissionRequest,
    PolicyEngine,
    Tool,
    ToolAgentLoop,
    ToolAgentRunResult,
    ToolExecutor,
    ToolRegistry,
    ToolSchema,
    TrajectoryEventKind,
)
from harness.core import JsonValue


class DemoTool(Tool):
    """Record execution without performing real file, Git, or shell effects."""

    def __init__(self, name: str, executions: list[str]) -> None:
        self._schema = ToolSchema(
            name=name,
            description=f"No-side-effect Demo implementation of {name}.",
            parameters={"type": "object"},
        )
        self._executions = executions

    @property
    def schema(self) -> ToolSchema:
        return self._schema

    async def execute(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        self._executions.append(self._schema.name)
        return {"executed": self._schema.name, "arguments": dict(arguments)}


class DemoPolicyEngine(PolicyEngine):
    async def decide(self, request: PermissionRequest) -> PermissionDecision:
        if request.tool_call.name in {"read_file", "write_file"}:
            return PermissionDecision.ALLOW
        if request.tool_call.name == "git_push":
            return PermissionDecision.REQUIRE_APPROVAL
        if (
            request.tool_call.name == "shell"
            and request.tool_call.arguments.get("command") == "rm -rf demo"
        ):
            return PermissionDecision.DENY
        return PermissionDecision.DENY


class IntentProvider(ModelProvider):
    def __init__(self, name: str, arguments: dict[str, JsonValue]) -> None:
        self._name = name
        self._arguments = arguments

    async def generate(self, request: ModelRequest) -> ModelResponse:
        step = _current_step(request)
        if step == 1:
            action: dict[str, object] = {
                "type": "tool_call",
                "name": self._name,
                "arguments": self._arguments,
            }
        else:
            action = {
                "type": "final_answer",
                "answer": f"Runtime resolved the {self._name} request.",
            }
        return ModelResponse(
            response_id=f"policy-{self._name}-{step}",
            model=request.model,
            message=ModelMessage(
                MessageRole.ASSISTANT,
                json.dumps(action, separators=(",", ":")),
            ),
            finish_reason="stop",
            usage=None,
            latency_ms=0.0,
        )


def _current_step(request: ModelRequest) -> int:
    state_message = next(
        message.content
        for message in request.messages
        if message.content.startswith("Current State:\n")
    )
    state = json.loads(state_message.split("\n", 1)[1])
    return int(state["current_step"])


def _loop(
    registry: ToolRegistry,
    policy: PolicyEngine,
    name: str,
    arguments: dict[str, JsonValue],
) -> ToolAgentLoop:
    return ToolAgentLoop(
        IntentProvider(name, arguments),
        model="policy-demo-model",
        max_steps=3,
        tool_executor=ToolExecutor(registry),
        context_builder=ContextBuilder(max_context_chars=6_000),
        checkpoint_store=JsonCheckpointStore(".harness-checkpoints"),
        policy_engine=policy,
    )


def _decision(result: ToolAgentRunResult) -> str:
    state = result.state
    event = next(
        event
        for event in state.trajectory
        if event.kind is TrajectoryEventKind.PERMISSION_DECISION
    )
    return str(event.details["decision"])


async def main() -> None:
    executions: list[str] = []
    registry = ToolRegistry()
    for name in ("read_file", "write_file", "git_push", "shell"):
        registry.register(DemoTool(name, executions))
    policy = DemoPolicyEngine()
    scenarios = (
        ("read_file", {"path": "README.md"}),
        ("write_file", {"path": "notes.txt", "content": "demo"}),
        ("git_push", {"branch": "main"}),
        ("shell", {"command": "rm -rf demo"}),
    )

    for name, arguments in scenarios:
        task_id = f"harn-11-{name.replace('_', '-')}"
        before = len(executions)
        result = await _loop(registry, policy, name, arguments).run(
            f"Request {name}.",
            task_id=task_id,
        )
        print(
            f"{name:10} -> {_decision(result):16} "
            f"status={result.status.value:18} "
            f"executed={len(executions) > before}"
        )
        if result.status.value == "waiting_approval":
            result = await _loop(registry, policy, name, arguments).resume(
                task_id,
                approve=True,
            )
            print(
                f"{'approved':10} -> {'resume':16} "
                f"status={result.status.value:18} executed=True"
            )
        elif result.last_tool_result is not None and result.last_tool_result.error:
            print(f"{'':10}    observation={result.last_tool_result.error}")


if __name__ == "__main__":
    asyncio.run(main())
