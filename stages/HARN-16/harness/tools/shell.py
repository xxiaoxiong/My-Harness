"""Shell Tool whose process execution is delegated to a Sandbox."""

from __future__ import annotations

from collections.abc import Mapping

from harness.core import JsonValue
from harness.sandbox import Sandbox, SandboxError, SandboxRequest
from harness.tools.base import Tool, ToolError, ToolSchema

SHELL_NAME = "shell"


class ShellToolError(ToolError):
    """An invalid or rejected Shell Tool request."""


class ShellTool(Tool):
    """Translate model arguments into the process-neutral Sandbox API."""

    def __init__(self, sandbox: Sandbox) -> None:
        if not isinstance(sandbox, Sandbox):
            raise TypeError("sandbox must be a Sandbox")
        self._sandbox = sandbox

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=SHELL_NAME,
            description=(
                "Run a shell command through the configured Sandbox with an "
                "explicit working directory and resource limits."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Command interpreted by the Sandbox shell.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory below the Sandbox root.",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                    },
                    "environment": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                    "output_limit_bytes": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Maximum captured bytes for each output stream.",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        )

    async def execute(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        if not isinstance(arguments, Mapping):
            raise ShellToolError("shell arguments must be an object")
        allowed = {
            "command",
            "cwd",
            "timeout_seconds",
            "environment",
            "output_limit_bytes",
        }
        if not set(arguments).issubset(allowed):
            raise ShellToolError("shell received an unsupported argument")

        command = arguments.get("command")
        cwd = arguments.get("cwd", ".")
        timeout = arguments.get("timeout_seconds")
        environment = arguments.get("environment", {})
        output_limit = arguments.get("output_limit_bytes")
        if not isinstance(command, str) or not command.strip():
            raise ShellToolError("shell command must be a nonempty string")
        if not isinstance(cwd, str) or not cwd.strip():
            raise ShellToolError("shell cwd must be a nonempty string")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        ):
            raise ShellToolError("shell timeout_seconds must be a number")
        if not isinstance(environment, Mapping) or not all(
            isinstance(name, str) and isinstance(value, str)
            for name, value in environment.items()
        ):
            raise ShellToolError("shell environment must contain string values")
        if output_limit is not None and (
            isinstance(output_limit, bool) or not isinstance(output_limit, int)
        ):
            raise ShellToolError("shell output_limit_bytes must be an integer")

        try:
            request = SandboxRequest(
                command=command,
                cwd=cwd,
                timeout_seconds=timeout,
                environment=environment,
                output_limit_bytes=output_limit,
            )
            result = await self._sandbox.execute(request)
        except (SandboxError, TypeError, ValueError) as error:
            raise ShellToolError(str(error)) from error
        return result.as_dict()
