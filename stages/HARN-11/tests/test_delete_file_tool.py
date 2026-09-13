import tempfile
import unittest
from pathlib import Path

from harness.tools import DeleteFileError, DeleteFileTool


class DeleteFileToolTests(unittest.IsolatedAsyncioTestCase):
    def test_requires_an_existing_root_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "existing directory"):
                DeleteFileTool(Path(directory) / "missing")
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            DeleteFileTool(" ")

    async def test_deletes_a_file_below_the_configured_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "nested" / "disposable.txt"
            target.parent.mkdir()
            target.write_text("temporary", encoding="utf-8")

            result = await DeleteFileTool(root).execute(
                {"path": "nested/disposable.txt"}
            )

            self.assertEqual(result, {"deleted": "nested/disposable.txt"})
            self.assertFalse(target.exists())

    async def test_rejects_paths_outside_the_configured_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "allowed"
            root.mkdir()
            tool = DeleteFileTool(root)

            with self.assertRaisesRegex(DeleteFileError, "escapes"):
                await tool.execute({"path": "../outside.txt"})
            with self.assertRaisesRegex(DeleteFileError, "must be relative"):
                await tool.execute({"path": str(Path(directory).resolve())})

    async def test_validates_arguments_and_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tool = DeleteFileTool(directory)

            with self.assertRaisesRegex(DeleteFileError, "must be an object"):
                await tool.execute([])  # type: ignore[arg-type]
            with self.assertRaisesRegex(DeleteFileError, "only the path"):
                await tool.execute({"path": "file.txt", "extra": True})
            with self.assertRaisesRegex(DeleteFileError, "nonempty"):
                await tool.execute({"path": " "})
            with self.assertRaisesRegex(DeleteFileError, "does not exist"):
                await tool.execute({"path": "missing.txt"})


if __name__ == "__main__":
    unittest.main()
