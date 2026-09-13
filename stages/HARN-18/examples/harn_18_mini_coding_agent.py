"""HARN-18 Demo: all Harness layers compose into a Mini Coding Agent."""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from harness import (
    AgentTask,
    AgentWorker,
    CodingSkill,
    GIT_DIFF_NAME,
    Harness,
    InMemoryIdempotencyStore,
    InMemoryTaskStore,
    InMemoryTraceRecorder,
    JsonCheckpointStore,
    LocalSandbox,
    MCPCallResult,
    MCPClient,
    MCPClientAdapter,
    MCPToolDefinition,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    READ_FILE_NAME,
    RetryPolicy,
    SEARCH_FILES_NAME,
    SHELL_NAME,
    SandboxGitBackend,
    SandboxRequest,
    TaskScheduler,
    TaskStatus,
    TokenUsage,
    TraceEventKind,
    TraceIdentity,
    TracingHook,
    TracingRetryObserver,
    TracingTaskStore,
    WRITE_FILE_NAME,
    format_trace_tree,
)
from harness.core import JsonValue
from harness.model import ModelTransportError

INITIAL_CODE = """def average(values):
    return sum(values)
"""
FIRST_FIX = """def average(values):
    return sum(values) / len(values)
"""
FINAL_FIX = """def average(values):
    return sum(values) / len(values) if values else 0.0
"""
TEST_CODE = """import unittest

from calculator import average


class AverageTests(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(average([2, 4]), 3)

    def test_empty_input(self):
        self.assertEqual(average([]), 0.0)


if __name__ == "__main__":
    unittest.main()
"""


class ProjectHintClient(MCPClient):
    """Tiny in-process MCP transport stand-in for an external project service."""

    async def list_tools(self) -> Sequence[MCPToolDefinition]:
        return [
            MCPToolDefinition(
                name="project_hint",
                description="Return the repository's final review convention.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            )
        ]

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
    ) -> MCPCallResult:
        return MCPCallResult(content="Run tests, inspect git_diff, then summarize.")


class CodingDemoProvider(ModelProvider):
    """Deterministic LLM stand-in that demonstrates a two-edit repair loop."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            raise ModelTransportError("simulated transient LLM disconnect")
        step = _current_step(request)
        if step == 1:
            action = _tool_call(SEARCH_FILES_NAME, {"query": "def average"})
        elif step == 2:
            action = _tool_call(READ_FILE_NAME, {"path": "calculator.py"})
        elif step == 3:
            action = _tool_call(
                WRITE_FILE_NAME,
                {"path": "calculator.py", "content": FIRST_FIX},
            )
        elif step == 4:
            action = _test_call("first")
        elif step == 5:
            _require_observed(request, "FAILED")
            action = _tool_call(
                WRITE_FILE_NAME,
                {"path": "calculator.py", "content": FINAL_FIX},
            )
        elif step == 6:
            action = _test_call("second")
        elif step == 7:
            _require_observed(request, "OK")
            action = _tool_call(GIT_DIFF_NAME, {"staged": False})
        elif step == 8:
            action = _tool_call("project_hint", {})
        else:
            action = {
                "type": "final_answer",
                "answer": (
                    "Fixed average() for normal and empty inputs; both tests pass "
                    "and the diff contains only the focused repair."
                ),
            }
        return ModelResponse(
            response_id=f"coding-demo-{self.calls}",
            model=request.model,
            message=ModelMessage(
                MessageRole.ASSISTANT,
                json.dumps(action, separators=(",", ":")),
            ),
            finish_reason="stop",
            usage=TokenUsage(80, 20, 100),
            latency_ms=2.0,
            ttft_ms=0.5,
        )


def _current_step(request: ModelRequest) -> int:
    state = next(
        message.content
        for message in request.messages
        if message.content.startswith("Current State:\n")
    )
    return int(json.loads(state.split("\n", 1)[1])["current_step"])


def _tool_call(
    name: str,
    arguments: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    return {"type": "tool_call", "name": name, "arguments": arguments}


def _test_call(run: str) -> dict[str, JsonValue]:
    return _tool_call(
        SHELL_NAME,
        {
            "command": "python -m unittest -v",
            "environment": {"CODING_TEST_RUN": run},
        },
    )


def _require_observed(request: ModelRequest, marker: str) -> None:
    if not any(marker in message.content for message in request.messages):
        raise RuntimeError(f"Agent did not observe expected test marker: {marker}")


async def _prepare_repository(root: Path, sandbox: LocalSandbox) -> None:
    (root / "calculator.py").write_text(INITIAL_CODE, encoding="utf-8")
    (root / "test_calculator.py").write_text(TEST_CODE, encoding="utf-8")
    commands = (
        "git init",
        "git config user.email demo@example.invalid",
        "git config user.name Harness-Demo",
        "git add calculator.py test_calculator.py",
        "git commit -m baseline",
    )
    for command in commands:
        result = await sandbox.execute(SandboxRequest(command=command))
        if result.exit_code != 0:
            raise RuntimeError(result.stderr or result.stdout)


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="harn-18-") as directory:
        root = Path(directory)
        sandbox = LocalSandbox(root, default_timeout_seconds=15)
        await _prepare_repository(root, sandbox)
        checkpoint_store = JsonCheckpointStore(root / ".harness-checkpoints")
        git_backend = SandboxGitBackend(sandbox)
        mcp_tools = await MCPClientAdapter(
            ProjectHintClient(),
            server_name="project-context",
        ).discover_tools()
        traces = InMemoryTraceRecorder()
        task_store = TracingTaskStore(InMemoryTaskStore(), traces)
        idempotency = InMemoryIdempotencyStore()
        provider = CodingDemoProvider()

        def loop_factory(task: AgentTask):
            harness = Harness(idempotency_store=idempotency)
            harness.load_plugin(
                CodingSkill(root, sandbox=sandbox, git_backend=git_backend)
            )
            for tool in mcp_tools:
                harness.register_tool(tool)
            harness.register_hook(
                TracingHook(
                    TraceIdentity(task.task_id, task.trace_id, task.session_id),
                    traces,
                )
            )
            return harness.create_agent_loop(
                provider,
                model="mini-coding-agent-demo",
                max_steps=12,
                max_context_chars=30_000,
                checkpoint_store=checkpoint_store,
            )

        worker = AgentWorker(task_store, loop_factory)
        scheduler = TaskScheduler(
            task_store,
            worker,
            retry_policy=RetryPolicy(
                max_attempts=2,
                base_delay_seconds=0,
                max_delay_seconds=0,
            ),
            retry_observer=TracingRetryObserver(traces),
        )
        submitted = scheduler.submit(
            "Fix the average() bug in this project and verify the repair",
            task_id="harn-18-coding-demo",
            trace_id="trace-harn-18-coding-demo",
            session_id="mini-coding-agent-session",
        )
        await scheduler.run_until_idle()

        task = task_store.get(submitted.task_id)
        while task.status is TaskStatus.WAITING:
            checkpoint = checkpoint_store.load(task.task_id)
            pending = checkpoint.state.pending_tool_call
            if pending is None:
                raise RuntimeError("waiting Task has no pending Tool Call")
            print(f"Approve from Checkpoint: {pending.name}")
            task = await worker.resume(task.task_id, approve=True)

        final_state = checkpoint_store.load(task.task_id).state
        tree = format_trace_tree(traces.events)
        shell_results = [
            result.result
            for result in final_state.tool_results
            if result.name == SHELL_NAME
        ]

        print("\nMini Coding Agent result")
        print(f"status={task.status.value}, attempts={task.attempts}")
        print(f"answer={task.final_answer}")
        print(f"tools={[call.name for call in final_state.tool_calls]}")
        print(
            "test exit codes="
            + str([result["exit_code"] for result in shell_results])
        )
        print(
            f"trace steps={len(tree['steps'])}, events={len(traces.events)}, "
            f"retries={sum(event.kind is TraceEventKind.RETRY for event in traces.events)}"
        )
        print("final calculator.py:\n" + (root / "calculator.py").read_text())


if __name__ == "__main__":
    asyncio.run(main())
