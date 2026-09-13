"""Workspace-bounded file Tools used by the final Coding Agent."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from harness.core import JsonValue
from harness.tools.base import Tool, ToolError, ToolSchema

READ_FILE_NAME = "read_file"
WRITE_FILE_NAME = "write_file"
SEARCH_FILES_NAME = "search_files"


class WorkspaceToolError(ToolError):
    """A file request that is invalid or escapes the configured workspace."""


class _WorkspaceTool(Tool):
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        if not self._root.is_dir():
            raise ValueError("workspace root must be an existing directory")

    def _resolve(self, value: JsonValue, *, name: str = "path") -> Path:
        if not isinstance(value, str) or not value.strip():
            raise WorkspaceToolError(f"{name} must be a nonempty string")
        relative = Path(value.strip())
        if relative.is_absolute():
            raise WorkspaceToolError(f"{name} must be relative to the workspace")
        resolved = (self._root / relative).resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError as error:
            raise WorkspaceToolError(
                f"{name} escapes the configured workspace"
            ) from error
        return resolved

    def _display(self, path: Path) -> str:
        return path.relative_to(self._root).as_posix()


class ReadFileTool(_WorkspaceTool):
    """Read bounded UTF-8 text without allowing workspace traversal."""

    def __init__(self, root: str | Path, *, max_chars: int = 100_000) -> None:
        super().__init__(root)
        self._max_chars = _positive_integer(max_chars, "max_chars")

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=READ_FILE_NAME,
            description="Read a UTF-8 text file below the coding workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    async def execute(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        _exact_arguments(arguments, {"path"}, READ_FILE_NAME)
        path = self._resolve(arguments["path"])
        if not path.is_file():
            raise WorkspaceToolError(f"file does not exist: {self._display(path)}")
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                content = handle.read(self._max_chars + 1)
        except (OSError, UnicodeError) as error:
            raise WorkspaceToolError(f"cannot read {self._display(path)}: {error}") from error
        truncated = len(content) > self._max_chars
        return {
            "path": self._display(path),
            "content": content[: self._max_chars],
            "truncated": truncated,
        }


class WriteFileTool(_WorkspaceTool):
    """Atomically replace one bounded UTF-8 file below the workspace root."""

    def __init__(self, root: str | Path, *, max_chars: int = 100_000) -> None:
        super().__init__(root)
        self._max_chars = _positive_integer(max_chars, "max_chars")

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=WRITE_FILE_NAME,
            description="Create or replace a UTF-8 text file in the coding workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        )

    async def execute(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        _exact_arguments(arguments, {"path", "content"}, WRITE_FILE_NAME)
        content = arguments["content"]
        if not isinstance(content, str):
            raise WorkspaceToolError("write_file content must be a string")
        if len(content) > self._max_chars:
            raise WorkspaceToolError(
                f"write_file content must not exceed {self._max_chars} characters"
            )
        path = self._resolve(arguments["path"])
        if path == self._root or path.is_dir():
            raise WorkspaceToolError("write_file path must identify a file")
        temporary: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(content)
                temporary = Path(handle.name)
            os.replace(temporary, path)
        except OSError as error:
            raise WorkspaceToolError(
                f"cannot write {self._display(path)}: {error}"
            ) from error
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return {
            "path": self._display(path),
            "characters_written": len(content),
        }


class SearchFilesTool(_WorkspaceTool):
    """Search matching text lines across a bounded set of workspace files."""

    _IGNORED_DIRECTORIES = frozenset({".git", ".venv", "__pycache__"})

    def __init__(
        self,
        root: str | Path,
        *,
        default_max_results: int = 50,
        max_results: int = 200,
        max_file_chars: int = 200_000,
    ) -> None:
        super().__init__(root)
        self._default_max_results = _positive_integer(
            default_max_results,
            "default_max_results",
        )
        self._max_results = _positive_integer(max_results, "max_results")
        self._max_file_chars = _positive_integer(max_file_chars, "max_file_chars")
        if self._default_max_results > self._max_results:
            raise ValueError("default_max_results must not exceed max_results")

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=SEARCH_FILES_NAME,
            description="Search UTF-8 workspace files for matching text lines.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                    "pattern": {"type": "string", "default": "*.py"},
                    "case_sensitive": {"type": "boolean", "default": True},
                    "max_results": {"type": "integer", "minimum": 1},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        )

    async def execute(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        allowed = {
            "query",
            "path",
            "pattern",
            "case_sensitive",
            "max_results",
        }
        _allowed_arguments(arguments, allowed, SEARCH_FILES_NAME)
        query = arguments.get("query")
        if not isinstance(query, str) or not query:
            raise WorkspaceToolError("search_files query must be a nonempty string")
        directory = self._resolve(arguments.get("path", "."))
        if not directory.is_dir():
            raise WorkspaceToolError(
                f"search path is not a directory: {self._display(directory)}"
            )
        pattern = arguments.get("pattern", "*.py")
        if not isinstance(pattern, str) or not pattern.strip():
            raise WorkspaceToolError("search_files pattern must be nonempty")
        pattern_path = Path(pattern)
        if pattern_path.is_absolute() or ".." in pattern_path.parts:
            raise WorkspaceToolError("search_files pattern must stay in the workspace")
        case_sensitive = arguments.get("case_sensitive", True)
        if not isinstance(case_sensitive, bool):
            raise WorkspaceToolError("search_files case_sensitive must be a boolean")
        result_limit = arguments.get("max_results", self._default_max_results)
        if isinstance(result_limit, bool) or not isinstance(result_limit, int):
            raise WorkspaceToolError("search_files max_results must be an integer")
        if result_limit <= 0 or result_limit > self._max_results:
            raise WorkspaceToolError(
                f"search_files max_results must be from 1 to {self._max_results}"
            )

        needle = query if case_sensitive else query.casefold()
        matches: list[JsonValue] = []
        truncated = False
        for candidate in sorted(directory.rglob(pattern.strip())):
            try:
                path = candidate.resolve()
                path.relative_to(self._root)
            except (OSError, ValueError):
                continue
            if not path.is_file() or self._ignored(path):
                continue
            try:
                if path.stat().st_size > self._max_file_chars * 4:
                    continue
                with path.open("r", encoding="utf-8", newline="") as handle:
                    content = handle.read(self._max_file_chars + 1)
            except (OSError, UnicodeError):
                continue
            if len(content) > self._max_file_chars:
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                haystack = line if case_sensitive else line.casefold()
                if needle not in haystack:
                    continue
                if len(matches) == result_limit:
                    truncated = True
                    break
                matches.append(
                    {
                        "path": self._display(path),
                        "line": line_number,
                        "text": line[:500],
                    }
                )
            if truncated:
                break
        return {
            "query": query,
            "path": self._display(directory) if directory != self._root else ".",
            "matches": matches,
            "truncated": truncated,
        }

    def _ignored(self, path: Path) -> bool:
        relative = path.relative_to(self._root)
        return bool(self._IGNORED_DIRECTORIES.intersection(relative.parts))


def _exact_arguments(
    arguments: Mapping[str, JsonValue],
    expected: set[str],
    tool_name: str,
) -> None:
    _allowed_arguments(arguments, expected, tool_name)
    if set(arguments) != expected:
        raise WorkspaceToolError(
            f"{tool_name} requires exactly: {', '.join(sorted(expected))}"
        )


def _allowed_arguments(
    arguments: Mapping[str, JsonValue],
    allowed: set[str],
    tool_name: str,
) -> None:
    if not isinstance(arguments, Mapping):
        raise WorkspaceToolError(f"{tool_name} arguments must be an object")
    if not set(arguments).issubset(allowed):
        raise WorkspaceToolError(f"{tool_name} received an unsupported argument")


def _positive_integer(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value
