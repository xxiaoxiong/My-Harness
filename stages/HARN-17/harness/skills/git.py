"""GitSkill: three Git-domain Tools injected through the Plugin mechanism."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping

from harness.core import JsonValue
from harness.extensions import Harness, PromptFragment, Skill
from harness.policy import PermissionDecision, StaticPolicyEngine
from harness.tools import Tool, ToolError, ToolSchema

GIT_STATUS_NAME = "git_status"
GIT_DIFF_NAME = "git_diff"
GIT_COMMIT_NAME = "git_commit"


class GitToolError(ToolError):
    """Raised when a Git Tool receives an invalid request."""


class GitBackend(ABC):
    """Execution boundary supplied to Git Tools without spawning processes."""

    @abstractmethod
    async def status(self) -> JsonValue:
        """Return repository status data."""

    @abstractmethod
    async def diff(self, *, staged: bool) -> JsonValue:
        """Return staged or unstaged changes."""

    @abstractmethod
    async def commit(self, message: str) -> JsonValue:
        """Create a commit and return identifying data."""


class InMemoryGitBackend(GitBackend):
    """Deterministic no-process backend for the HARN-12 Demo and tests."""

    def __init__(
        self,
        *,
        branch: str = "main",
        changed_files: Iterable[str] = (),
        unstaged_diff: str = "",
        staged_diff: str = "",
    ) -> None:
        if not isinstance(branch, str) or not branch.strip():
            raise ValueError("branch must not be empty")
        files = tuple(changed_files)
        if not all(isinstance(path, str) and path.strip() for path in files):
            raise ValueError("changed_files must contain nonempty paths")
        if not isinstance(unstaged_diff, str) or not isinstance(staged_diff, str):
            raise TypeError("diff values must be strings")
        self._branch = branch.strip()
        self._changed_files = tuple(path.strip() for path in files)
        self._unstaged_diff = unstaged_diff
        self._staged_diff = staged_diff
        self._commits: list[str] = []

    @property
    def commits(self) -> tuple[str, ...]:
        return tuple(self._commits)

    async def status(self) -> JsonValue:
        return {
            "branch": self._branch,
            "changed_files": list(self._changed_files),
            "clean": not self._changed_files,
        }

    async def diff(self, *, staged: bool) -> JsonValue:
        return {
            "staged": staged,
            "diff": self._staged_diff if staged else self._unstaged_diff,
        }

    async def commit(self, message: str) -> JsonValue:
        self._commits.append(message)
        return {
            "commit_id": f"demo-{len(self._commits):04d}",
            "message": message,
        }


class GitStatusTool(Tool):
    def __init__(self, backend: GitBackend) -> None:
        _require_backend(backend)
        self._backend = backend

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=GIT_STATUS_NAME,
            description="Show the current Git branch and changed files.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )

    async def execute(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        _require_arguments(arguments)
        if arguments:
            raise GitToolError("git_status does not accept arguments")
        return await self._backend.status()


class GitDiffTool(Tool):
    def __init__(self, backend: GitBackend) -> None:
        _require_backend(backend)
        self._backend = backend

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=GIT_DIFF_NAME,
            description="Show staged or unstaged Git changes.",
            parameters={
                "type": "object",
                "properties": {
                    "staged": {
                        "type": "boolean",
                        "description": "Whether to show the staged diff.",
                    }
                },
                "additionalProperties": False,
            },
        )

    async def execute(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        _require_arguments(arguments)
        if not set(arguments).issubset({"staged"}):
            raise GitToolError("git_diff accepts only the staged argument")
        staged = arguments.get("staged", False)
        if not isinstance(staged, bool):
            raise GitToolError("git_diff staged must be a boolean")
        return await self._backend.diff(staged=staged)


class GitCommitTool(Tool):
    def __init__(self, backend: GitBackend) -> None:
        _require_backend(backend)
        self._backend = backend

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=GIT_COMMIT_NAME,
            description="Create a Git commit with a message.",
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Commit message.",
                    }
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        )

    async def execute(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        _require_arguments(arguments)
        if set(arguments) != {"message"}:
            raise GitToolError("git_commit requires only the message argument")
        message = arguments.get("message")
        if not isinstance(message, str) or not message.strip():
            raise GitToolError("git_commit message must be a nonempty string")
        return await self._backend.commit(message.strip())


class GitSkill(Skill):
    """Bundle Git Tools, prompt guidance, and commit approval policy."""

    def __init__(self, backend: GitBackend) -> None:
        _require_backend(backend)
        self._backend = backend

    @property
    def name(self) -> str:
        return "git"

    def setup(self, harness: Harness) -> None:
        harness.register_tool(GitStatusTool(self._backend))
        harness.register_tool(GitDiffTool(self._backend))
        harness.register_tool(GitCommitTool(self._backend))
        harness.register_prompt_fragment(
            PromptFragment(
                name="git-skill",
                content=(
                    "Use git_status before git_diff. Create commits only when "
                    "the task explicitly requires it."
                ),
            )
        )
        harness.register_policy(
            StaticPolicyEngine(
                {GIT_COMMIT_NAME: PermissionDecision.REQUIRE_APPROVAL}
            )
        )


def _require_backend(backend: GitBackend) -> None:
    if not isinstance(backend, GitBackend):
        raise TypeError("backend must be a GitBackend")


def _require_arguments(arguments: Mapping[str, JsonValue]) -> None:
    if not isinstance(arguments, Mapping):
        raise GitToolError("Git Tool arguments must be an object")
