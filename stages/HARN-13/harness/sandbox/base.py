"""Provider-neutral process execution contracts for HARN-13."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite

from harness.core import JsonValue


@dataclass(frozen=True, slots=True)
class SandboxRequest:
    """One command plus the resource controls applied by a Sandbox."""

    command: str
    cwd: str = "."
    timeout_seconds: float | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    output_limit_bytes: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.command, str) or not self.command.strip():
            raise ValueError("sandbox command must not be empty")
        if not isinstance(self.cwd, str) or not self.cwd.strip():
            raise ValueError("sandbox cwd must not be empty")
        if self.timeout_seconds is not None:
            if isinstance(self.timeout_seconds, bool) or not isinstance(
                self.timeout_seconds, (int, float)
            ):
                raise TypeError("sandbox timeout_seconds must be a number or null")
            if self.timeout_seconds <= 0 or not isfinite(self.timeout_seconds):
                raise ValueError("sandbox timeout_seconds must be positive and finite")
        if not isinstance(self.environment, Mapping):
            raise TypeError("sandbox environment must be an object")
        if not all(
            isinstance(name, str)
            and name
            and "\x00" not in name
            and "=" not in name
            and isinstance(value, str)
            and "\x00" not in value
            for name, value in self.environment.items()
        ):
            raise ValueError(
                "sandbox environment must contain valid string names and values"
            )
        if self.output_limit_bytes is not None:
            if isinstance(self.output_limit_bytes, bool) or not isinstance(
                self.output_limit_bytes, int
            ):
                raise TypeError(
                    "sandbox output_limit_bytes must be an integer or null"
                )
            if self.output_limit_bytes <= 0:
                raise ValueError("sandbox output_limit_bytes must be positive")

        object.__setattr__(self, "command", self.command.strip())
        object.__setattr__(self, "cwd", self.cwd.strip())
        object.__setattr__(self, "environment", dict(self.environment))
        if self.timeout_seconds is not None:
            object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """Bounded process output returned through the Sandbox API."""

    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    output_truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.cwd, str) or not self.cwd:
            raise ValueError("sandbox result cwd must not be empty")
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise TypeError("sandbox result exit_code must be an integer")
        if not isinstance(self.stdout, str) or not isinstance(self.stderr, str):
            raise TypeError("sandbox result output must be text")
        if not isinstance(self.timed_out, bool):
            raise TypeError("sandbox result timed_out must be a boolean")
        if not isinstance(self.output_truncated, bool):
            raise TypeError("sandbox result output_truncated must be a boolean")

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "output_truncated": self.output_truncated,
        }


class SandboxError(RuntimeError):
    """Base error raised by a Sandbox implementation."""


class SandboxBoundaryError(SandboxError):
    """Raised when a requested working directory escapes the Sandbox root."""


class SandboxLimitError(SandboxError):
    """Raised when a request exceeds configured resource ceilings."""


class SandboxProcessError(SandboxError):
    """Raised when the Sandbox cannot create or control a process."""


class Sandbox(ABC):
    """The only API process-backed Tools use to start a command."""

    @abstractmethod
    async def execute(self, request: SandboxRequest) -> SandboxResult:
        """Execute one bounded request and return a normalized result."""
