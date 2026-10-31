import json
import tempfile
import unittest
from pathlib import Path

from skills.workspace import (
    invoke_workspace_skill,
    validate_workspace_skill_arguments,
)


class WorkspaceSkillTests(unittest.TestCase):
    def test_project_structure_respects_depth_and_ignores_noise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("print(1)", encoding="utf-8")
            skills = root / "skills"
            skills.mkdir()
            (skills / "media.py").write_text("", encoding="utf-8")
            deep = skills / "deep"
            deep.mkdir()
            (deep / "nested.py").write_text("", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "main.pyc").write_text("", encoding="utf-8")
            (root / "build").mkdir()
            (root / "build" / "artifact.txt").write_text("", encoding="utf-8")

            result = json.loads(
                invoke_workspace_skill(
                    "get_project_structure",
                    {"path": ".", "max_depth": 2},
                    workspace_root=root,
                )
            )

        self.assertEqual(result["path"], ".")
        self.assertFalse(result["truncated"])
        tree = result["tree"]
        self.assertIn("main.py", tree)
        self.assertIn("skills/", tree)
        self.assertIn("media.py", tree)
        self.assertIn("deep/", tree)
        self.assertNotIn("nested.py", tree)
        self.assertNotIn(".git", tree)
        self.assertNotIn("__pycache__", tree)
        self.assertNotIn("build", tree)
        self.assertNotIn("artifact.txt", tree)

    def test_project_structure_can_limit_depth_to_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("", encoding="utf-8")
            skills = root / "skills"
            skills.mkdir()
            (skills / "media.py").write_text("", encoding="utf-8")

            result = json.loads(
                invoke_workspace_skill(
                    "get_project_structure",
                    {"max_depth": 1},
                    workspace_root=root,
                )
            )

        self.assertIn("main.py", result["tree"])
        self.assertIn("skills/", result["tree"])
        self.assertNotIn("media.py", result["tree"])

    def test_project_structure_truncates_large_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(350):
                (root / f"file-{index:03d}.txt").write_text("", encoding="utf-8")

            result = json.loads(
                invoke_workspace_skill(
                    "get_project_structure",
                    {"max_depth": 1},
                    workspace_root=root,
                )
            )

        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["tree"].splitlines()), 301)

    def test_rejects_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            outside = Path(directory) / "outside"
            outside.mkdir()

            for path in ("../outside", str(outside)):
                with self.subTest(path=path):
                    with self.assertRaisesRegex(ValueError, "工作目录"):
                        invoke_workspace_skill(
                            "get_project_structure",
                            {"path": path},
                            workspace_root=root,
                        )

    def test_filename_search_matches_wildcards_and_reports_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("", encoding="utf-8")
            (root / "notes.txt").write_text("", encoding="utf-8")
            skills = root / "skills"
            skills.mkdir()
            (skills / "media.py").write_text("", encoding="utf-8")

            result = json.loads(
                invoke_workspace_skill(
                    "search_workspace_files",
                    {"mode": "filename", "query": "*.py"},
                    workspace_root=root,
                )
            )
            truncated = json.loads(
                invoke_workspace_skill(
                    "search_workspace_files",
                    {
                        "mode": "filename",
                        "query": "*.py",
                        "max_results": 1,
                    },
                    workspace_root=root,
                )
            )

        self.assertEqual(result["results"], ["main.py", "skills/media.py"])
        self.assertFalse(result["truncated"])
        self.assertEqual(len(truncated["results"]), 1)
        self.assertTrue(truncated["truncated"])

    def test_content_search_is_case_insensitive_and_skips_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.txt").write_text(
                "first line\nHello Prana\nlast line\n", encoding="utf-8"
            )
            (root / "binary.bin").write_bytes(b"\x00Hello Prana")

            result = json.loads(
                invoke_workspace_skill(
                    "search_workspace_files",
                    {"mode": "content", "query": "hello prana"},
                    workspace_root=root,
                )
            )

        self.assertEqual(
            result["results"],
            [{"path": "notes.txt", "line": 2, "text": "Hello Prana"}],
        )
        self.assertFalse(result["truncated"])

    def test_content_search_respects_result_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("match\n", encoding="utf-8")
            (root / "b.txt").write_text("match\n", encoding="utf-8")

            result = json.loads(
                invoke_workspace_skill(
                    "search_workspace_files",
                    {
                        "mode": "content",
                        "query": "match",
                        "max_results": 1,
                    },
                    workspace_root=root,
                )
            )

        self.assertEqual(len(result["results"]), 1)
        self.assertTrue(result["truncated"])

    def test_write_creates_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = json.loads(
                invoke_workspace_skill(
                    "write_workspace_file",
                    {"path": "notes/hello.txt", "content": "hi\n"},
                    workspace_root=root,
                )
            )
            self.assertEqual(result["path"], "notes/hello.txt")
            self.assertTrue(result["created"])
            self.assertFalse(result["overwritten"])
            self.assertEqual(
                (root / "notes" / "hello.txt").read_text(encoding="utf-8"),
                "hi\n",
            )

    def test_write_rejects_existing_file_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("old", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "overwrite"):
                invoke_workspace_skill(
                    "write_workspace_file",
                    {"path": "a.txt", "content": "new"},
                    workspace_root=root,
                )
            self.assertEqual((root / "a.txt").read_text(encoding="utf-8"), "old")

    def test_write_overwrites_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("old", encoding="utf-8")
            result = json.loads(
                invoke_workspace_skill(
                    "write_workspace_file",
                    {"path": "a.txt", "content": "new\n", "overwrite": True},
                    workspace_root=root,
                )
            )
            self.assertTrue(result["overwritten"])
            self.assertFalse(result["created"])
            self.assertEqual(
                (root / "a.txt").read_text(encoding="utf-8"), "new\n"
            )

    def test_write_rejects_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            outside = Path(directory) / "outside.txt"
            with self.assertRaisesRegex(ValueError, "工作目录"):
                invoke_workspace_skill(
                    "write_workspace_file",
                    {"path": "../outside.txt", "content": "x"},
                    workspace_root=root,
                )
            self.assertFalse(outside.exists())

    def test_write_rejects_empty_content(self) -> None:
        with self.assertRaises(ValueError):
            validate_workspace_skill_arguments(
                "write_workspace_file", {"path": "a.txt", "content": ""}
            )

    def test_write_rejects_unknown_arguments(self) -> None:
        with self.assertRaises(ValueError):
            validate_workspace_skill_arguments(
                "write_workspace_file",
                {"path": "a.txt", "content": "x", "bogus": 1},
            )

    def test_edit_replaces_unique_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text(
                "alpha\nbeta\ngamma\n", encoding="utf-8"
            )
            result = json.loads(
                invoke_workspace_skill(
                    "edit_workspace_file",
                    {
                        "path": "a.txt",
                        "oldString": "beta",
                        "newString": "BETA",
                    },
                    workspace_root=root,
                )
            )
            self.assertEqual(result["replacements"], 1)
            self.assertEqual(
                (root / "a.txt").read_text(encoding="utf-8"),
                "alpha\nBETA\ngamma\n",
            )

    def test_edit_requires_unique_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text(
                "x=1\nx=2\nx=3\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "恰好 1 处"):
                invoke_workspace_skill(
                    "edit_workspace_file",
                    {"path": "a.txt", "oldString": "x=", "newString": "y="},
                    workspace_root=root,
                )

    def test_edit_replaces_all_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text(
                "x=1\nx=2\nx=3\n", encoding="utf-8"
            )
            result = json.loads(
                invoke_workspace_skill(
                    "edit_workspace_file",
                    {
                        "path": "a.txt",
                        "oldString": "x=",
                        "newString": "y=",
                        "replaceAll": True,
                    },
                    workspace_root=root,
                )
            )
            self.assertEqual(result["replacements"], 3)
            self.assertEqual(
                (root / "a.txt").read_text(encoding="utf-8"),
                "y=1\ny=2\ny=3\n",
            )

    def test_edit_rejects_missing_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("alpha\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "未找到"):
                invoke_workspace_skill(
                    "edit_workspace_file",
                    {"path": "a.txt", "oldString": "zzz", "newString": "y"},
                    workspace_root=root,
                )

    def test_edit_rejects_unchanged_string(self) -> None:
        with self.assertRaises(ValueError):
            validate_workspace_skill_arguments(
                "edit_workspace_file",
                {
                    "path": "a.txt",
                    "oldString": "same",
                    "newString": "same",
                },
            )

    def test_edit_rejects_empty_old_string(self) -> None:
        with self.assertRaises(ValueError):
            validate_workspace_skill_arguments(
                "edit_workspace_file",
                {"path": "a.txt", "oldString": "", "newString": "y"},
            )

    def test_edit_requires_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "未找到"):
                invoke_workspace_skill(
                    "edit_workspace_file",
                    {"path": "missing.txt", "oldString": "a", "newString": "b"},
                    workspace_root=root,
                )

    def test_rejects_invalid_arguments(self) -> None:
        invalid_arguments = (
            ("get_project_structure", {"max_depth": 0}),
            ("get_project_structure", {"max_depth": 5}),
            ("get_project_structure", {"max_depth": True}),
            ("get_project_structure", {"unknown": 1}),
            ("get_project_structure", {"path": ""}),
            ("search_workspace_files", {"mode": "content", "query": ""}),
            (
                "search_workspace_files",
                {"mode": "content", "query": "x", "max_results": 0},
            ),
            (
                "search_workspace_files",
                {"mode": "content", "query": "x", "max_results": 101},
            ),
            ("search_workspace_files", {"mode": "regex", "query": "x"}),
            (
                "search_workspace_files",
                {
                    "mode": "filename",
                    "query": "x",
                    "unexpected": True,
                },
            ),
            ("unknown_skill", {"query": "x"}),
            ("write_workspace_file", {"path": "a.txt"}),
            ("write_workspace_file", {"path": "", "content": "x"}),
            ("write_workspace_file", {"path": "a.txt", "content": "x", "overwrite": "yes"}),
            ("edit_workspace_file", {"path": "a.txt", "oldString": "o"}),
            (
                "edit_workspace_file",
                {"path": "a.txt", "oldString": "o", "newString": "n", "replaceAll": "yes"},
            ),
        )
        for name, arguments in invalid_arguments:
            with self.subTest(name=name, arguments=arguments):
                with self.assertRaises(ValueError):
                    validate_workspace_skill_arguments(name, arguments)


def test_scratch_workspace_root_is_under_temp(tmp_path):
    from skills.workspace import scratch_workspace_root

    root = scratch_workspace_root()
    import tempfile
    assert root == Path(tempfile.gettempdir()) / "prana-scratch"


def test_write_workspace_file_rejects_bad_workspace(tmp_path):
    import pytest
    from skills.workspace import invoke_workspace_skill

    with pytest.raises(ValueError, match="workspace 只能是 permanent 或 scratch。"):
        invoke_workspace_skill(
            "write_workspace_file",
            {"path": "a.txt", "content": "x", "workspace": "other"},
            workspace_root=tmp_path,
        )


def test_transfer_scratch_to_permanent(tmp_path, monkeypatch):
    import tempfile
    from pathlib import Path
    from skills import invoke_workspace_skill

    scratch = Path(tempfile.gettempdir()) / "prana-scratch"
    scratch.mkdir(exist_ok=True)
    (scratch / "a.txt").write_text("hello")

    invoke_workspace_skill(
        "transfer_workspace_file",
        {
            "source_workspace": "scratch",
            "source_path": "a.txt",
            "target_workspace": "permanent",
            "target_path": "a.txt",
        },
        workspace_root=tmp_path,
    )
    assert (tmp_path / "a.txt").read_text() == "hello"


def test_transfer_rejects_existing_target_without_overwrite(tmp_path, monkeypatch):
    import tempfile
    from pathlib import Path
    from skills import invoke_workspace_skill
    import pytest

    scratch = Path(tempfile.gettempdir()) / "prana-scratch"
    scratch.mkdir(exist_ok=True)
    (scratch / "b.txt").write_text("hello")
    (tmp_path / "b.txt").write_text("old")

    with pytest.raises(ValueError):
        invoke_workspace_skill(
            "transfer_workspace_file",
            {
                "source_workspace": "scratch",
                "source_path": "b.txt",
                "target_workspace": "permanent",
                "target_path": "b.txt",
            },
            workspace_root=tmp_path,
        )


def test_switch_workspace_requires_existing_dir(tmp_path):
    from skills import invoke_workspace_skill
    import pytest

    with pytest.raises(ValueError):
        invoke_workspace_skill(
            "switch_workspace",
            {"path": str(tmp_path / "nope")},
            workspace_root=tmp_path,
        )


def test_switch_workspace_accepts_existing_dir(tmp_path):
    import json
    from skills import invoke_workspace_skill

    out = invoke_workspace_skill(
        "switch_workspace",
        {"path": str(tmp_path)},
        workspace_root=tmp_path,
    )
    parsed = json.loads(out)
    assert parsed["status"] == "switched"
    assert parsed["path"] == str(tmp_path.resolve())


if __name__ == "__main__":
    unittest.main()
