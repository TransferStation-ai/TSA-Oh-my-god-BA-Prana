import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

from skills.git import invoke_git_skill, validate_git_arguments

GIT_EXECUTABLE = shutil.which("git")


def run_git(root: Path, *arguments: str) -> None:
    assert GIT_EXECUTABLE is not None
    subprocess.run(
        [GIT_EXECUTABLE, *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


@unittest.skipUnless(GIT_EXECUTABLE, "需要安装 Git 才能运行仓库 skill 测试。")
class GitSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        run_git(self.root, "init")
        run_git(self.root, "config", "user.email", "prana@example.test")
        run_git(self.root, "config", "user.name", "Prana Test")
        (self.root / "app.py").write_text("print('hello')\n", encoding="utf-8")
        run_git(self.root, "add", "app.py")
        run_git(self.root, "commit", "-m", "initial commit")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_status_reports_modified_file(self) -> None:
        (self.root / "app.py").write_text("print('changed')\n", encoding="utf-8")

        result = json.loads(
            invoke_git_skill({"action": "status"}, workspace_root=self.root)
        )

        self.assertEqual(result["action"], "status")
        self.assertIn("M app.py", result["output"])
        self.assertFalse(result["truncated"])

    def test_log_returns_recent_commits(self) -> None:
        result = json.loads(
            invoke_git_skill({"action": "log"}, workspace_root=self.root)
        )

        self.assertIn("initial commit", result["output"])

    def test_diff_shows_working_tree_changes(self) -> None:
        (self.root / "app.py").write_text("print('changed')\n", encoding="utf-8")

        result = json.loads(
            invoke_git_skill({"action": "diff"}, workspace_root=self.root)
        )

        self.assertIn("-print('hello')", result["output"])
        self.assertIn("+print('changed')", result["output"])

    def test_diff_accepts_a_safe_ref(self) -> None:
        (self.root / "app.py").write_text("print('second')\n", encoding="utf-8")
        run_git(self.root, "add", "app.py")
        run_git(self.root, "commit", "-m", "second commit")

        result = json.loads(
            invoke_git_skill(
                {"action": "diff", "ref": "HEAD~1"}, workspace_root=self.root
            )
        )

        self.assertIn("+print('second')", result["output"])

    def test_branch_lists_branches(self) -> None:
        run_git(self.root, "branch", "feature")

        result = json.loads(
            invoke_git_skill({"action": "branch"}, workspace_root=self.root)
        )

        self.assertIn("feature", result["output"])

    def test_rejects_paths_outside_workspace(self) -> None:
        with self.assertRaisesRegex(ValueError, "工作目录"):
            invoke_git_skill(
                {"action": "diff", "path": "../outside.txt"},
                workspace_root=self.root,
            )

    def test_rejects_option_like_refs(self) -> None:
        with self.assertRaises(ValueError):
            invoke_git_skill(
                {"action": "diff", "ref": "--output=evil"}, workspace_root=self.root
            )

    def test_rejects_invalid_arguments(self) -> None:
        invalid_arguments = (
            {},
            {"action": "push"},
            {"action": "status", "limit": 5},
            {"action": "log", "limit": 0},
            {"action": "log", "limit": 51},
            {"action": "log", "ref": "HEAD"},
            {"action": "diff", "path": ""},
            {"action": "status", "unknown": True},
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    validate_git_arguments(arguments)

    def test_rejects_non_repository_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Git 仓库"):
                invoke_git_skill(
                    {"action": "status"}, workspace_root=Path(directory)
                )


def test_git_read_rejects_scratch(tmp_path):
    subprocess.run(
        ["git", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    with pytest.raises(ValueError, match="Git 仅在永久工作区可用。"):
        invoke_git_skill(
            {"action": "status", "workspace": "scratch"},
            workspace_root=tmp_path,
        )


if __name__ == "__main__":
    unittest.main()
