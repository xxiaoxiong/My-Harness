"""HARN-13 Demo: Policy gates intent, then Sandbox bounds the process."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from harness import (
    Harness,
    JsonCheckpointStore,
    LocalSandbox,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    PermissionDecision,
    SHELL_NAME,
    ShellTool,
    StaticPolicyEngine,
)


class ShellWorkflowProvider(ModelProvider):
    def __init__(self, command: str) -> None:
        self._command = command

    async def generate(self, request: ModelRequest) -> ModelResponse:
        step = _current_step(request)
        if step == 1:
            action: dict[str, object] = {
                "type": "tool_call",
                "name": SHELL_NAME,
                "arguments": {
                    "command": self._command,
                    "cwd": ".",
                    "timeout_seconds": 2,
                    "environment": {"HARN_STAGE": "HARN-13"},
                    "output_limit_bytes": 1_024,
                },
            }
        else:
            action = {
                "type": "final_answer",
                "answer": "The bounded Shell observation was received.",
            }
        return ModelResponse(
            response_id=f"harn-13-response-{step}",
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


def _demo_command() -> str:
    if sys.platform == "win32":
        return (
            "$env:HARN_STAGE | Set-Content -Encoding utf8 marker.txt; "
            "Write-Output ($env:HARN_STAGE + '|' + "
            "(Split-Path -Leaf (Get-Location)))"
        )
    return (
        "printf '%s\\n' \"$HARN_STAGE\" > marker.txt; "
        "printf '%s|%s\\n' \"$HARN_STAGE\" \"$(basename \"$PWD\")\""
    )


def _build_harness(
    sandbox: LocalSandbox,
    decision: PermissionDecision,
) -> Harness:
    harness = Harness()
    harness.register_tool(ShellTool(sandbox))
    harness.register_policy(StaticPolicyEngine({SHELL_NAME: decision}))
    return harness


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="harn-13-") as directory:
        root = Path(directory)
        marker = root / "marker.txt"
        store = JsonCheckpointStore(root / "checkpoints")
        sandbox = LocalSandbox(
            root,
            default_timeout_seconds=2,
            max_timeout_seconds=5,
            default_output_limit_bytes=1_024,
            max_output_limit_bytes=4_096,
        )
        command = _demo_command()

        denied = await _build_harness(
            sandbox,
            PermissionDecision.DENY,
        ).create_agent_loop(
            ShellWorkflowProvider(command),
            model="sandbox-demo-model",
            max_steps=3,
            max_context_chars=8_000,
        ).run("Run a disposable local command.", task_id="harn-13-denied")
        print(
            "Policy DENY: "
            f"error={denied.last_tool_result.error!r}, marker={marker.exists()}"  # type: ignore[union-attr]
        )

        waiting = await _build_harness(
            sandbox,
            PermissionDecision.REQUIRE_APPROVAL,
        ).create_agent_loop(
            ShellWorkflowProvider(command),
            model="sandbox-demo-model",
            max_steps=3,
            max_context_chars=8_000,
            checkpoint_store=store,
        ).run("Run a disposable local command.", task_id="harn-13-approved")
        print(
            "Before approval: "
            f"status={waiting.status.value}, marker={marker.exists()}"
        )

        completed = await _build_harness(
            sandbox,
            PermissionDecision.REQUIRE_APPROVAL,
        ).create_agent_loop(
            ShellWorkflowProvider(command),
            model="sandbox-demo-model",
            max_steps=3,
            max_context_chars=8_000,
            checkpoint_store=store,
        ).resume("harn-13-approved", approve=True)
        process = completed.last_tool_result.result  # type: ignore[union-attr]
        print(
            "After approval:  "
            f"status={completed.status.value}, marker={marker.exists()}"
        )
        print(
            "Sandbox result:  "
            f"exit={process['exit_code']}, timeout={process['timed_out']}, "  # type: ignore[index]
            f"truncated={process['output_truncated']}, "  # type: ignore[index]
            f"stdout={process['stdout'].strip()!r}"  # type: ignore[index,union-attr]
        )


if __name__ == "__main__":
    asyncio.run(main())
