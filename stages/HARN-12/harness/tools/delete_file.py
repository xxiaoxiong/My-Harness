"""A root-confined file deletion Tool used by the HARN-09 approval Demo."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from harness.core.types import JsonValue
from harness.tools.base import Tool, ToolError, ToolSchema

DELETE_FILE_NAME = "delete_file"


class DeleteFileError(ToolError):
    """Raised when a requested deletion is invalid or cannot be completed."""


class DeleteFileTool(Tool):
    """Delete one file below a configured root directory."""

    def __init__(self, root_directory: str | Path) -> None:
        if not isinstance(root_directory, (str, Path)):
            raise TypeError("root_directory must be a path")
        if isinstance(root_directory, str) and not root_directory.strip():
            raise ValueError("root_directory must not be empty")
        self._root_directory = Path(root_directory).resolve()
        if not self._root_directory.is_dir():
            raise ValueError("root_directory must be an existing directory")

    @property
    def root_directory(self) -> Path:
        return self._root_directory

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=DELETE_FILE_NAME,
            description="Delete one file below the configured workspace root.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path of the file to delete.",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    async def execute(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        if not isinstance(arguments, Mapping):
            raise DeleteFileError("delete_file arguments must be an object")
        if set(arguments) != {"path"}:
            raise DeleteFileError("delete_file requires only the path argument")
        raw_path = arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise DeleteFileError("path must be a nonempty string")

        relative_path = Path(raw_path.strip())
        if relative_path.is_absolute():
            raise DeleteFileError("path must be relative to the configured root")
        target = (self._root_directory / relative_path).resolve()
        if not target.is_relative_to(self._root_directory):
            raise DeleteFileError("path escapes the configured root")
        if not target.is_file():
            raise DeleteFileError(f"file does not exist: {relative_path.as_posix()}")

        try:
            target.unlink()
        except OSError as error:
            raise DeleteFileError(
                f"unable to delete file: {relative_path.as_posix()}"
            ) from error
        return {"deleted": relative_path.as_posix()}
