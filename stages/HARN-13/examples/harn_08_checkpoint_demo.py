"""HARN-08 Demo: crash after Step 3 and recover from a JSON checkpoint."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
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
from harness.tools import CalculatorTool, ToolExecutor, ToolRegistry


class SimulatedProcessExit(RuntimeError):
    """Simulate losing the process before Step 4 can finish."""


class CheckpointDemoProvider(ModelProvider):
    """Deterministic provider whose behavior is derived from persisted state."""

    def __init__(self, *, crash_before_step: int | None = None) -> None:
        self._crash_before_step = crash_before_step

    async def generate(self, request: ModelRequest) -> ModelResponse:
        step = _current_step(request)
        if step == self._crash_before_step:
            raise SimulatedProcessExit(f"simulated exit before Step {step}")

        if step <= 4:
            action: dict[str, object] = {
                "type": "tool_call",
                "name": "calculator",
                "arguments": {"expression": f"{step} + {step}"},
            }
        else:
            action = {
                "type": "final_answer",
                "answer": "Recovered from Step 3 and completed at Step 5.",
            }
        return ModelResponse(
            response_id=f"harn-08-response-{step}",
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


def _build_loop(
    store: JsonCheckpointStore,
    provider: ModelProvider,
) -> ToolAgentLoop:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return ToolAgentLoop(
        provider,
        model="checkpoint-demo-model",
        max_steps=6,
        tool_executor=ToolExecutor(registry),
        context_builder=ContextBuilder(max_context_chars=4_000),
        checkpoint_store=store,
    )


async def _start(task_id: str, checkpoint_dir: Path) -> None:
    store = JsonCheckpointStore(checkpoint_dir)
    loop = _build_loop(store, CheckpointDemoProvider(crash_before_step=4))

    print(f"Starting task: {task_id}")
    try:
        await loop.run(
            "Run four calculator steps, then answer.",
            task_id=task_id,
        )
    except SimulatedProcessExit as error:
        checkpoint = store.load(task_id)
        print(error)
        print(
            "Durable checkpoint: "
            f"step={checkpoint.step}, status={checkpoint.status.value}, "
            f"tool_results={len(checkpoint.state.tool_results)}"
        )
        print(f"Checkpoint file: {checkpoint_dir / f'{task_id}.json'}")
        print(f"Resume with: python resume.py {task_id}")


async def _resume(task_id: str, checkpoint_dir: Path) -> None:
    store = JsonCheckpointStore(checkpoint_dir)
    before = store.load(task_id)
    print(
        "Loaded checkpoint: "
        f"step={before.step}, status={before.status.value}, "
        f"trajectory_events={len(before.state.trajectory)}"
    )

    result = await _build_loop(store, CheckpointDemoProvider()).resume(task_id)
    print(
        "Resumed task: "
        f"step={result.steps_executed}, status={result.status.value}, "
        f"tool_results={len(result.state.tool_results)}"
    )
    print(f"Final answer: {result.final_answer}")


def _parser(*, resume: bool) -> argparse.ArgumentParser:
    description = (
        "Resume a HARN-08 task from its latest checkpoint."
        if resume
        else "Run the HARN-08 task until the simulated process exit."
    )
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("task_id", nargs="?", default="harn-08-demo")
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=Path(".harness-checkpoints"),
    )
    return parser


def start_main(argv: Sequence[str] | None = None) -> None:
    args = _parser(resume=False).parse_args(argv)
    asyncio.run(_start(args.task_id, args.checkpoint_dir))


def resume_main(argv: Sequence[str] | None = None) -> None:
    args = _parser(resume=True).parse_args(argv)
    asyncio.run(_resume(args.task_id, args.checkpoint_dir))


if __name__ == "__main__":
    start_main()
