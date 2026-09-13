"""Coding Skill that composes file, process, Git, Prompt, and Policy pieces."""

from __future__ import annotations

from pathlib import Path

from harness.extensions import Harness, PromptFragment, Skill
from harness.policy import PermissionDecision, StaticPolicyEngine
from harness.sandbox import Sandbox
from harness.skills.git import GitBackend, GitDiffTool
from harness.tools import (
    SHELL_NAME,
    WRITE_FILE_NAME,
    ReadFileTool,
    SearchFilesTool,
    ShellTool,
    WriteFileTool,
)


class CodingSkill(Skill):
    """Install the five capabilities needed by the Mini Coding Agent."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        sandbox: Sandbox,
        git_backend: GitBackend,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        if not self._workspace_root.is_dir():
            raise ValueError("workspace_root must be an existing directory")
        if not isinstance(sandbox, Sandbox):
            raise TypeError("sandbox must be a Sandbox")
        if not isinstance(git_backend, GitBackend):
            raise TypeError("git_backend must be a GitBackend")
        self._sandbox = sandbox
        self._git_backend = git_backend

    @property
    def name(self) -> str:
        return "coding"

    def setup(self, harness: Harness) -> None:
        harness.register_tool(SearchFilesTool(self._workspace_root))
        harness.register_tool(ReadFileTool(self._workspace_root))
        harness.register_tool(WriteFileTool(self._workspace_root))
        harness.register_tool(ShellTool(self._sandbox))
        harness.register_tool(GitDiffTool(self._git_backend))
        harness.register_prompt_fragment(
            PromptFragment(
                name="coding-workflow",
                content=(
                    "Inspect before editing. Make the smallest justified change, "
                    "run focused tests through shell, inspect failures, iterate, "
                    "and review git_diff before the final answer."
                ),
            )
        )
        harness.register_policy(
            StaticPolicyEngine(
                {
                    WRITE_FILE_NAME: PermissionDecision.REQUIRE_APPROVAL,
                    SHELL_NAME: PermissionDecision.REQUIRE_APPROVAL,
                }
            )
        )
