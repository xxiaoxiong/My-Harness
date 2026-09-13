import asyncio
import json
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path

from harness import (
    AgentTask,
    AgentWorker,
    CodingSkill,
    GIT_DIFF_NAME,
    GitToolError,
    Harness,
    InMemoryGitBackend,
    InMemoryIdempotencyStore,
    InMemoryTaskStore,
    InMemoryTraceRecorder,
    JsonCheckpointStore,
    MCPCallResult,
    MCPClient,
    MCPClientAdapter,
    MCPToolDefinition,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    PermissionDecision,
    PermissionRequest,
    READ_FILE_NAME,
    ReadFileTool,
    RetryPolicy,
    SEARCH_FILES_NAME,
    SHELL_NAME,
    Sandbox,
    SandboxGitBackend,
    SandboxRequest,
    SandboxResult,
    SearchFilesTool,
    TaskScheduler,
    TaskStatus,
    ToolCall,
    TraceEventKind,
    TraceIdentity,
    TracingHook,
    TracingRetryObserver,
    TracingTaskStore,
    WRITE_FILE_NAME,
    WorkspaceToolError,
    WriteFileTool,
)
from harness.core import JsonValue
from harness.model import ModelTransportError


FIRST_FIX = """def average(values):
    return sum(values) / len(values)
"""
FINAL_FIX = """def average(values):
    return sum(values) / len(values) if values else 0.0
"""


class SequencedSandbox(Sandbox):
    def __init__(self) -> None:
        self.requests: list[SandboxRequest] = []

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        self.requests.append(request)
        execution = len(self.requests)
        if execution == 1:
            return SandboxResult(
                cwd=request.cwd,
                exit_code=1,
                stdout="FAILED: average([]) raised ZeroDivisionError",
                stderr="",
            )
        return SandboxResult(
            cwd=request.cwd,
            exit_code=0,
            stdout="OK: 2 tests passed",
            stderr="",
        )


class FixedSandbox(Sandbox):
    def __init__(self, results: Sequence[SandboxResult]) -> None:
        self._results = list(results)
        self.requests: list[SandboxRequest] = []

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        self.requests.append(request)
        return self._results.pop(0)


class HintMCPClient(MCPClient):
    def __init__(self) -> None:
        self.calls = 0

    async def list_tools(self) -> Sequence[MCPToolDefinition]:
        return [
            MCPToolDefinition(
                name="project_hint",
                description="Return a project-specific completion hint.",
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
        self.calls += 1
        return MCPCallResult(content="Review the diff before answering.")


class CodingProvider(ModelProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            raise ModelTransportError("temporary coding-model disconnect")
        step = _step(request)
        if step == 1:
            action = _call(SEARCH_FILES_NAME, {"query": "def average"})
        elif step == 2:
            action = _call(READ_FILE_NAME, {"path": "calculator.py"})
        elif step == 3:
            action = _call(
                WRITE_FILE_NAME,
                {"path": "calculator.py", "content": FIRST_FIX},
            )
        elif step == 4:
            action = _call(
                SHELL_NAME,
                {
                    "command": "python -m unittest -v",
                    "environment": {"CODING_TEST_RUN": "first"},
                },
            )
        elif step == 5:
            _require_observation(request, "FAILED")
            action = _call(
                WRITE_FILE_NAME,
                {"path": "calculator.py", "content": FINAL_FIX},
            )
        elif step == 6:
            action = _call(
                SHELL_NAME,
                {
                    "command": "python -m unittest -v",
                    "environment": {"CODING_TEST_RUN": "second"},
                },
            )
        elif step == 7:
            _require_observation(request, "2 tests passed")
            action = _call(GIT_DIFF_NAME, {"staged": False})
        elif step == 8:
            action = _call("project_hint", {})
        else:
            action = {
                "type": "final_answer",
                "answer": "Fixed average(), covered the empty case, and tests pass.",
            }
        return ModelResponse(
            response_id=f"coding-{self.calls}",
            model=request.model,
            message=ModelMessage(
                MessageRole.ASSISTANT,
                json.dumps(action, separators=(",", ":")),
            ),
            finish_reason="stop",
            usage=None,
            latency_ms=1.0,
            ttft_ms=0.25,
        )


def _step(request: ModelRequest) -> int:
    current = next(
        message.content
        for message in request.messages
        if message.content.startswith("Current State:\n")
    )
    return int(json.loads(current.split("\n", 1)[1])["current_step"])


def _call(name: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {"type": "tool_call", "name": name, "arguments": arguments}


def _require_observation(request: ModelRequest, expected: str) -> None:
    if not any(expected in message.content for message in request.messages):
        raise AssertionError(f"model did not observe {expected!r}")


class WorkspaceToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_write_and_search_are_workspace_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "one.py").write_text(
                "def target():\n    return 1\n",
                encoding="utf-8",
            )
            (root / ".git").mkdir()
            (root / ".git" / "hidden.py").write_text(
                "def target(): pass\n",
                encoding="utf-8",
            )
            reader = ReadFileTool(root, max_chars=12)
            writer = WriteFileTool(root)
            searcher = SearchFilesTool(root)

            read = await reader.execute({"path": "src/one.py"})
            self.assertEqual(read["content"], "def target()")
            self.assertTrue(read["truncated"])

            written = await writer.execute(
                {"path": "src/two.py", "content": "def target():\n    return 2\n"}
            )
            self.assertEqual(written["path"], "src/two.py")
            self.assertEqual(
                (root / "src" / "two.py").read_text(encoding="utf-8"),
                "def target():\n    return 2\n",
            )

            found = await searcher.execute(
                {"query": "def target", "path": ".", "pattern": "*.py"}
            )
            self.assertEqual(
                [match["path"] for match in found["matches"]],
                ["src/one.py", "src/two.py"],
            )
            with self.assertRaises(WorkspaceToolError):
                await reader.execute({"path": "../outside.txt"})
            with self.assertRaises(WorkspaceToolError):
                await writer.execute({"path": "../outside.txt", "content": "x"})
            with self.assertRaises(WorkspaceToolError):
                await searcher.execute({"query": "x", "pattern": "../*.py"})


class CodingCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_coding_skill_installs_exact_capabilities_and_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = SequencedSandbox()
            harness = Harness()
            harness.load_plugin(
                CodingSkill(
                    directory,
                    sandbox=sandbox,
                    git_backend=InMemoryGitBackend(),
                )
            )

            self.assertEqual(
                [tool.schema.name for tool in harness.tools],
                [
                    SEARCH_FILES_NAME,
                    READ_FILE_NAME,
                    WRITE_FILE_NAME,
                    SHELL_NAME,
                    GIT_DIFF_NAME,
                ],
            )
            policy = harness.policies[0]
            for name, expected in (
                (WRITE_FILE_NAME, PermissionDecision.REQUIRE_APPROVAL),
                (SHELL_NAME, PermissionDecision.REQUIRE_APPROVAL),
                (READ_FILE_NAME, PermissionDecision.ALLOW),
            ):
                decision = await policy.decide(
                    PermissionRequest(
                        task_id="task",
                        goal="fix",
                        step=1,
                        tool_call=ToolCall(name, {}),
                    )
                )
                self.assertIs(decision, expected)

    async def test_sandbox_git_backend_is_observable_and_read_only(self) -> None:
        sandbox = FixedSandbox(
            [
                SandboxResult(".", 0, "## main\n M app.py\n", ""),
                SandboxResult(".", 0, "diff --git a/app.py b/app.py\n", ""),
            ]
        )
        backend = SandboxGitBackend(sandbox)

        status = await backend.status()
        diff = await backend.diff(staged=False)

        self.assertEqual(status["branch"], "main")
        self.assertEqual(status["changed_files"], [" M app.py"])
        self.assertIn("diff --git", diff["diff"])
        self.assertEqual(
            [request.command for request in sandbox.requests],
            ["git status --short --branch", "git diff --no-ext-diff"],
        )
        with self.assertRaises(GitToolError):
            await backend.commit("must not run")


class MiniCodingAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_system_fixes_bug_with_resume_mcp_and_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "calculator.py").write_text(
                "def average(values):\n    return sum(values)\n",
                encoding="utf-8",
            )
            checkpoints = JsonCheckpointStore(root / ".checkpoints")
            sandbox = SequencedSandbox()
            git_backend = InMemoryGitBackend(
                changed_files=["calculator.py"],
                unstaged_diff="- return sum(values)\n+ handle empty values\n",
            )
            provider = CodingProvider()
            mcp_client = HintMCPClient()
            mcp_tools = await MCPClientAdapter(
                mcp_client,
                server_name="project-context",
            ).discover_tools()
            traces = InMemoryTraceRecorder()
            task_store = TracingTaskStore(InMemoryTaskStore(), traces)
            idempotency = InMemoryIdempotencyStore()

            def loop_factory(task: AgentTask):
                harness = Harness(idempotency_store=idempotency)
                harness.load_plugin(
                    CodingSkill(
                        root,
                        sandbox=sandbox,
                        git_backend=git_backend,
                    )
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
                    model="mini-coding-agent",
                    max_steps=12,
                    max_context_chars=30_000,
                    checkpoint_store=checkpoints,
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
                sleeper=lambda _: asyncio.sleep(0),
                retry_observer=TracingRetryObserver(traces),
            )
            submitted = scheduler.submit(
                "Fix the average bug and run the tests",
                task_id="coding-task",
                trace_id="coding-trace",
                session_id="coding-session",
            )

            await scheduler.run_until_idle()
            approvals: list[str] = []
            task = task_store.get(submitted.task_id)
            while task.status is TaskStatus.WAITING:
                pending = checkpoints.load(task.task_id).state.pending_tool_call
                self.assertIsNotNone(pending)
                approvals.append(pending.name)
                task = await worker.resume(task.task_id, approve=True)

            self.assertIs(task.status, TaskStatus.COMPLETED, task.error)
            self.assertEqual(
                task.final_answer,
                "Fixed average(), covered the empty case, and tests pass.",
            )
            self.assertEqual(
                approvals,
                [WRITE_FILE_NAME, SHELL_NAME, WRITE_FILE_NAME, SHELL_NAME],
            )
            self.assertEqual(
                (root / "calculator.py").read_text(encoding="utf-8"),
                FINAL_FIX,
            )
            state = checkpoints.load(task.task_id).state
            self.assertEqual(
                [call.name for call in state.tool_calls],
                [
                    SEARCH_FILES_NAME,
                    READ_FILE_NAME,
                    WRITE_FILE_NAME,
                    SHELL_NAME,
                    WRITE_FILE_NAME,
                    SHELL_NAME,
                    GIT_DIFF_NAME,
                    "project_hint",
                ],
            )
            shell_results = [
                result.result
                for result in state.tool_results
                if result.name == SHELL_NAME
            ]
            self.assertEqual(
                [result["exit_code"] for result in shell_results],
                [1, 0],
            )
            self.assertEqual(mcp_client.calls, 1)
            self.assertTrue(
                any(event.kind is TraceEventKind.RETRY for event in traces.events)
            )
            transitions = [
                event.details["to"]
                for event in traces.events
                if event.kind is TraceEventKind.TASK_STATE_TRANSITION
            ]
            self.assertEqual(
                transitions,
                [
                    "pending",
                    "running",
                    "waiting",
                    "running",
                    "waiting",
                    "running",
                    "waiting",
                    "running",
                    "waiting",
                    "running",
                    "completed",
                ],
            )


if __name__ == "__main__":
    unittest.main()
