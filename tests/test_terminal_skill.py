import tempfile
import unittest
from pathlib import Path

from skills.terminal import invoke_terminal_skill, validate_terminal_command


class TerminalSkillTests(unittest.TestCase):
    def test_lists_workspace_and_reads_a_text_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.txt").write_text("project notes", encoding="utf-8")
            (root / "subdirectory").mkdir()

            listing = invoke_terminal_skill(
                {"terminal": "powershell", "command": "Get-ChildItem"},
                workspace_root=root,
            )
            contents = invoke_terminal_skill(
                {"terminal": "cmd", "command": 'type "notes.txt"'},
                workspace_root=root,
            )

        self.assertIn("notes.txt", listing)
        self.assertIn("subdirectory", listing)
        self.assertIn("project notes", contents)
        self.assertIn('"terminal": "cmd"', contents)

    def test_rejects_scripts_and_command_chaining(self) -> None:
        for command in (
            "Remove-Item notes.txt",
            "dir; whoami",
            "Get-Content notes.txt | more",
            "python -c print(1)",
        ):
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    validate_terminal_command(
                        {"terminal": "powershell", "command": command}
                    )

    def test_rejects_commands_from_the_wrong_terminal(self) -> None:
        with self.assertRaisesRegex(ValueError, "CMD"):
            validate_terminal_command(
                {"terminal": "cmd", "command": "Get-ChildItem"}
            )
        with self.assertRaisesRegex(ValueError, "PowerShell"):
            validate_terminal_command(
                {"terminal": "powershell", "command": "cd"}
            )

    def test_rejects_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            with self.assertRaisesRegex(ValueError, "工作目录"):
                invoke_terminal_skill(
                    {"terminal": "cmd", "command": "type ../outside.txt"},
                    workspace_root=root,
                )

    def test_rejects_oversized_file_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "large.txt").write_text(
                "x" * (64 * 1024 + 1), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "读取限制"):
                invoke_terminal_skill(
                    {"terminal": "cmd", "command": "type large.txt"},
                    workspace_root=root,
                )
