"""HARN-09 Demo: interrupt delete_file and resume in another process."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from harness.context import ContextBuilder
from harness.model import (
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
)
from harness.runtime import JsonCheckpointStore, ToolAgentLoop
from harness.policy import PermissionDecision, StaticPolicyEngine
from harness.tools import (
    DELETE_FILE_NAME,
    DeleteFileTool,
    ToolExecutor,
    ToolRegistry,
)

CHECKPOINT_DIRECTORY = Path(".harness-checkpoints")
DEMO_FILE_ROOT = Path(".harness-approval-demo")


class InterruptDemoProvider(ModelProvider):
    """Ask for deletion on Step 1 and finish after observing its result."""

    def __init__(self, target_name: str) -> None:
        self._target_name = target_name

    async def generate(self, request: ModelRequest) -> ModelResponse:
        step = _current_step(request)
        if step == 1:
            action: dict[str, object] = {
                "type": "tool_call",
                "name": DELETE_FILE_NAME,
                "arguments": {"path": self._target_name},
            }
        else:
            action = {
                "type": "final_answer",
                "answer": "The approval decision was observed; the task is complete.",
            }
        return ModelResponse(
            response_id=f"harn-09-response-{step}",
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


def _target_name(task_id: str) -> str:
    return f"{task_id}-delete-me.txt"


def _build_loop(task_id: str) -> ToolAgentLoop:
    registry = ToolRegistry()
    registry.register(DeleteFileTool(DEMO_FILE_ROOT))
    return ToolAgentLoop(
        InterruptDemoProvider(_target_name(task_id)),
        model="interrupt-demo-model",
        max_steps=3,
        tool_executor=ToolExecutor(registry),
        context_builder=ContextBuilder(max_context_chars=4_000),
        checkpoint_store=JsonCheckpointStore(CHECKPOINT_DIRECTORY),
        policy_engine=StaticPolicyEngine(
            {DELETE_FILE_NAME: PermissionDecision.REQUIRE_APPROVAL}
        ),
    )


async def _start(task_id: str) -> None:
    DEMO_FILE_ROOT.mkdir(parents=True, exist_ok=True)
    target = DEMO_FILE_ROOT / _target_name(task_id)
    target.write_text("This file is deleted only after approval.\n", encoding="utf-8")

    result = await _build_loop(task_id).run(
        "Delete the disposable Demo file.",
        task_id=task_id,
    )
    print(
        f"Runtime returned: status={result.status.value}, "
        f"step={result.steps_executed}"
    )
    print(
        "Pending call: "
        f"{result.pending_tool_call.name} "  # type: ignore[union-attr]
        f"{dict(result.pending_tool_call.arguments)}"  # type: ignore[union-attr]
    )
    print(f"File still exists before approval: {target.exists()}")
    print(
        "Approve in a new process: "
        f"python -m examples.harn_09_interrupt_resume resume {task_id} --approve"
    )


async def _resume(task_id: str, *, approved: bool) -> None:
    target = DEMO_FILE_ROOT / _target_name(task_id)
    result = await _build_loop(task_id).resume(task_id, approve=approved)
    print(f"Decision: {'approved' if approved else 'rejected'}")
    print(
        f"Runtime returned: status={result.status.value}, "
        f"step={result.steps_executed}"
    )
    print(f"File exists after decision: {target.exists()}")
    print(f"Tool result: {result.last_tool_result}")
    print(f"Final answer: {result.final_answer}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Demonstrate a durable approval interrupt across processes."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="run until approval is required")
    start.add_argument("task_id", nargs="?", default="harn-09-demo")

    resume = commands.add_parser("resume", help="persist a decision and continue")
    resume.add_argument("task_id", nargs="?", default="harn-09-demo")
    decision = resume.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--reject", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "start":
        asyncio.run(_start(args.task_id))
    else:
        asyncio.run(_resume(args.task_id, approved=args.approve))


if __name__ == "__main__":
    main()
