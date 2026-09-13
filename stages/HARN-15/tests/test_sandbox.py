import sys
import tempfile
import unittest
from pathlib import Path

from harness import (
    LocalSandbox,
    Sandbox,
    SandboxBoundaryError,
    SandboxLimitError,
    SandboxProcessError,
    SandboxRequest,
    SandboxResult,
    ShellTool,
    ToolCall,
    ToolExecutor,
    ToolRegistry,
)


class RecordingSandbox(Sandbox):
    def __init__(self, *, error: Exception | None = None) -> None:
        self.requests: list[SandboxRequest] = []
        self._error = error

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return SandboxResult(
            cwd="C:/workspace",
            exit_code=0,
            stdout="sandbox output\n",
            stderr="",
        )


class SandboxContractTests(unittest.TestCase):
    def test_request_validates_all_process_controls(self) -> None:
        request = SandboxRequest(
            command="python --version",
            cwd="src",
            timeout_seconds=2,
            environment={"MODE": "test"},
            output_limit_bytes=512,
        )

        self.assertEqual(request.command, "python --version")
        self.assertEqual(request.timeout_seconds, 2.0)
        self.assertEqual(request.environment, {"MODE": "test"})
        with self.assertRaises(ValueError):
            SandboxRequest(command=" ")
        with self.assertRaises(ValueError):
            SandboxRequest(command="x", timeout_seconds=0)
        with self.assertRaises(ValueError):
            SandboxRequest(command="x", timeout_seconds=float("nan"))
        with self.assertRaises(ValueError):
            SandboxRequest(command="x", environment={"BAD=NAME": "value"})
        with self.assertRaises(TypeError):
            SandboxRequest(command="x", output_limit_bytes=True)

    def test_result_has_a_json_compatible_shape(self) -> None:
        result = SandboxResult(
            cwd="/workspace",
            exit_code=7,
            stdout="out",
            stderr="err",
            timed_out=True,
            output_truncated=True,
        )

        self.assertEqual(
            result.as_dict(),
            {
                "cwd": "/workspace",
                "exit_code": 7,
                "stdout": "out",
                "stderr": "err",
                "timed_out": True,
                "output_truncated": True,
            },
        )


class LocalSandboxTests(unittest.IsolatedAsyncioTestCase):
    def _sandbox(self, root: str, **overrides: object) -> LocalSandbox:
        options: dict[str, object] = {
            "default_timeout_seconds": 0.5,
            "max_timeout_seconds": 1.0,
            "default_output_limit_bytes": 1_024,
            "max_output_limit_bytes": 2_048,
            "base_environment": {"PYTHONIOENCODING": "utf-8"},
            "shell_argv": (sys.executable, "-c"),
        }
        options.update(overrides)
        return LocalSandbox(root, **options)  # type: ignore[arg-type]

    async def test_applies_cwd_and_environment(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            child = Path(root, "child")
            child.mkdir()
            sandbox = self._sandbox(root)

            result = await sandbox.execute(
                SandboxRequest(
                    command=(
                        "import os,pathlib;"
                        "print(pathlib.Path.cwd().name);"
                        "print(os.environ['HARN_MODE'])"
                    ),
                    cwd="child",
                    environment={"HARN_MODE": "sandboxed"},
                )
            )

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.stdout.splitlines(), ["child", "sandboxed"])
            self.assertEqual(Path(result.cwd), child.resolve())
            self.assertFalse(result.timed_out)
            self.assertFalse(result.output_truncated)

    async def test_rejects_cwd_escape_and_resource_limit_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            sandbox = self._sandbox(root)

            with self.assertRaises(SandboxBoundaryError):
                await sandbox.execute(SandboxRequest(command="pass", cwd=".."))
            with self.assertRaises(SandboxLimitError):
                await sandbox.execute(
                    SandboxRequest(command="pass", timeout_seconds=2)
                )
            with self.assertRaises(SandboxLimitError):
                await sandbox.execute(
                    SandboxRequest(command="pass", output_limit_bytes=4_096)
                )

    async def test_timeout_kills_process_and_preserves_bounded_output(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            sandbox = self._sandbox(
                root,
                default_timeout_seconds=0.05,
            )

            result = await sandbox.execute(
                SandboxRequest(
                    command=(
                        "import time;print('started',flush=True);time.sleep(2)"
                    )
                )
            )

            self.assertTrue(result.timed_out)
            self.assertEqual(result.stdout.splitlines(), ["started"])

    async def test_drains_but_truncates_both_output_streams(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            sandbox = self._sandbox(
                root,
                default_output_limit_bytes=12,
            )

            result = await sandbox.execute(
                SandboxRequest(
                    command=(
                        "import sys;"
                        "sys.stdout.write('x'*40);"
                        "sys.stderr.write('e'*40)"
                    )
                )
            )

            self.assertEqual(result.stdout, "x" * 12)
            self.assertEqual(result.stderr, "e" * 12)
            self.assertTrue(result.output_truncated)


class ShellToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_executor_reaches_process_only_through_sandbox(self) -> None:
        sandbox = RecordingSandbox()
        registry = ToolRegistry()
        registry.register(ShellTool(sandbox))

        result = await ToolExecutor(registry).execute(
            ToolCall(
                name="shell",
                arguments={
                    "command": "git status --short",
                    "cwd": "repo",
                    "timeout_seconds": 3,
                    "environment": {"MODE": "demo"},
                    "output_limit_bytes": 900,
                },
            )
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(len(sandbox.requests), 1)
        self.assertEqual(sandbox.requests[0].command, "git status --short")
        self.assertEqual(sandbox.requests[0].cwd, "repo")
        self.assertEqual(sandbox.requests[0].environment, {"MODE": "demo"})
        self.assertEqual(result.result["stdout"], "sandbox output\n")  # type: ignore[index]

    async def test_tool_normalizes_validation_and_sandbox_errors(self) -> None:
        tool = ShellTool(RecordingSandbox())
        registry = ToolRegistry()
        registry.register(tool)
        executor = ToolExecutor(registry)

        invalid = await executor.execute(
            ToolCall(name="shell", arguments={"command": "x", "unknown": 1})
        )
        self.assertEqual(invalid.error, "shell received an unsupported argument")

        failing_registry = ToolRegistry()
        failing_registry.register(
            ShellTool(RecordingSandbox(error=SandboxProcessError("spawn failed")))
        )
        failed = await ToolExecutor(failing_registry).execute(
            ToolCall(name="shell", arguments={"command": "x"})
        )
        self.assertEqual(failed.error, "spawn failed")

    def test_process_api_is_not_imported_by_tools(self) -> None:
        tool_sources = Path("harness", "tools").glob("*.py")
        for source in tool_sources:
            self.assertNotIn("import subprocess", source.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
