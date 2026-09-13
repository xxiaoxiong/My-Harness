"""Local process Sandbox with explicit cwd, environment, time, and output bounds."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from math import isfinite
from pathlib import Path

from harness.sandbox.base import (
    Sandbox,
    SandboxBoundaryError,
    SandboxLimitError,
    SandboxProcessError,
    SandboxRequest,
    SandboxResult,
)


class LocalSandbox(Sandbox):
    """Run a system shell below one configured working-directory root.

    This local implementation supplies a controlled process boundary, but it is
    not an OS security container. A future DockerSandbox can implement the same
    contract with filesystem and network isolation.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        default_timeout_seconds: float = 10.0,
        max_timeout_seconds: float = 60.0,
        default_output_limit_bytes: int = 20_000,
        max_output_limit_bytes: int = 100_000,
        base_environment: Mapping[str, str] | None = None,
        shell_argv: Sequence[str] | None = None,
    ) -> None:
        root_path = Path(root).resolve()
        if not root_path.is_dir():
            raise ValueError("sandbox root must be an existing directory")
        _validate_positive_number(
            default_timeout_seconds,
            "default_timeout_seconds",
        )
        _validate_positive_number(max_timeout_seconds, "max_timeout_seconds")
        if default_timeout_seconds > max_timeout_seconds:
            raise ValueError(
                "default_timeout_seconds must not exceed max_timeout_seconds"
            )
        _validate_positive_integer(
            default_output_limit_bytes,
            "default_output_limit_bytes",
        )
        _validate_positive_integer(
            max_output_limit_bytes,
            "max_output_limit_bytes",
        )
        if default_output_limit_bytes > max_output_limit_bytes:
            raise ValueError(
                "default_output_limit_bytes must not exceed max_output_limit_bytes"
            )

        if base_environment is None:
            environment = dict(os.environ)
        else:
            environment = _validated_environment(base_environment)
        argv = tuple(shell_argv) if shell_argv is not None else _default_shell_argv()
        if not argv or not all(
            isinstance(argument, str) and argument for argument in argv
        ):
            raise ValueError("shell_argv must contain nonempty strings")

        self._root = root_path
        self._default_timeout_seconds = float(default_timeout_seconds)
        self._max_timeout_seconds = float(max_timeout_seconds)
        self._default_output_limit_bytes = default_output_limit_bytes
        self._max_output_limit_bytes = max_output_limit_bytes
        self._base_environment = environment
        self._shell_argv = argv

    @property
    def root(self) -> Path:
        return self._root

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        if not isinstance(request, SandboxRequest):
            raise TypeError("request must be a SandboxRequest")
        cwd = self._resolve_cwd(request.cwd)
        timeout = self._resolve_timeout(request.timeout_seconds)
        output_limit = self._resolve_output_limit(request.output_limit_bytes)
        environment = dict(self._base_environment)
        environment.update(request.environment)

        try:
            process = await asyncio.create_subprocess_exec(
                *self._shell_argv,
                request.command,
                cwd=str(cwd),
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise SandboxProcessError(f"could not start sandbox process: {error}") from error

        stdout_task = asyncio.create_task(
            _read_bounded(process.stdout, output_limit)
        )
        stderr_task = asyncio.create_task(
            _read_bounded(process.stderr, output_limit)
        )
        timed_out = False
        try:
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except TimeoutError:
                timed_out = True
                _kill_if_running(process)
                await process.wait()
            stdout_data, stderr_data = await asyncio.gather(
                stdout_task,
                stderr_task,
            )
        except BaseException:
            _kill_if_running(process)
            await process.wait()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise

        stdout, stdout_truncated = stdout_data
        stderr, stderr_truncated = stderr_data
        return SandboxResult(
            cwd=str(cwd),
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            output_truncated=stdout_truncated or stderr_truncated,
        )

    def _resolve_cwd(self, requested_cwd: str) -> Path:
        candidate = Path(requested_cwd)
        if not candidate.is_absolute():
            candidate = self._root / candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self._root):
            raise SandboxBoundaryError(
                f"sandbox cwd escapes configured root: {requested_cwd}"
            )
        if not resolved.is_dir():
            raise SandboxBoundaryError(
                f"sandbox cwd is not an existing directory: {requested_cwd}"
            )
        return resolved

    def _resolve_timeout(self, requested: float | None) -> float:
        timeout = self._default_timeout_seconds if requested is None else requested
        if timeout > self._max_timeout_seconds:
            raise SandboxLimitError(
                f"timeout exceeds sandbox maximum of {self._max_timeout_seconds:g} seconds"
            )
        return timeout

    def _resolve_output_limit(self, requested: int | None) -> int:
        limit = self._default_output_limit_bytes if requested is None else requested
        if limit > self._max_output_limit_bytes:
            raise SandboxLimitError(
                "output limit exceeds sandbox maximum of "
                f"{self._max_output_limit_bytes} bytes per stream"
            )
        return limit


async def _read_bounded(
    stream: asyncio.StreamReader | None,
    limit: int,
) -> tuple[str, bool]:
    if stream is None:
        return "", False
    captured = bytearray()
    truncated = False
    while chunk := await stream.read(8_192):
        remaining = limit - len(captured)
        if remaining > 0:
            captured.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return captured.decode("utf-8", errors="replace"), truncated


def _kill_if_running(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass


def _default_shell_argv() -> tuple[str, ...]:
    if sys.platform == "win32":
        return (
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
        )
    return ("/bin/sh", "-c")


def _validated_environment(environment: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(environment, Mapping):
        raise TypeError("base_environment must be an object")
    request = SandboxRequest(command="validate", environment=environment)
    return dict(request.environment)


def _validate_positive_number(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    if value <= 0 or not isfinite(value):
        raise ValueError(f"{name} must be positive and finite")


def _validate_positive_integer(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
