import json
import tempfile
import unittest
from pathlib import Path

import sessions
from sessions import (
    KIND_BRAINSTORM,
    KIND_MAIN,
    KIND_PARTICIPANT,
    STATUS_INTERRUPTED,
    SessionStore,
    build_api_messages,
    build_session_title,
    new_message,
)


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "sessions"
        self.store = SessionStore(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_new_main_session_defaults(self) -> None:
        session = self.store.new_session()
        self.assertEqual(session["kind"], KIND_MAIN)
        self.assertIsNone(session["parent_id"])
        self.assertEqual(session["title"], "新对话")
        self.assertEqual(session["messages"], [])
        self.assertRegex(str(session["id"]), r"^[a-f0-9]{32}$")

    def test_save_and_load_roundtrip(self) -> None:
        session = self.store.new_session(title="测试会话")
        message = new_message("user", "你好", display="你好")
        session["messages"].append(message)
        self.store.save(session)

        loaded = self.store.load(str(session["id"]))
        self.assertEqual(loaded["title"], "测试会话")
        self.assertEqual(loaded["messages"][0]["display"], "你好")
        self.assertEqual(loaded["messages"][0]["id"], message["id"])

    def test_update_appends_message_and_persists(self) -> None:
        session = self.store.new_session()
        self.store.save(session)

        updated = self.store.update(
            str(session["id"]),
            lambda data: data["messages"].append(
                new_message("assistant", "好的")
            ),
        )

        self.assertEqual(len(updated["messages"]), 1)
        self.assertEqual(
            len(self.store.load(str(session["id"]))["messages"]), 1
        )

    def test_list_sessions_sorts_and_summarizes(self) -> None:
        import time

        first = self.store.new_session(title="first")
        self.store.save(first)
        time.sleep(0.02)

        second = self.store.new_session(title="second")
        second["messages"].append(new_message("user", "x"))
        second["messages"].append(
            new_message(
                "assistant",
                "",
                kind="brainstorm",
                brainstorm_session_id="a" * 32,
                in_api=False,
            )
        )
        self.store.save(second)

        summaries = self.store.list_sessions(kind=KIND_MAIN)

        self.assertEqual([item["title"] for item in summaries], ["second", "first"])
        self.assertEqual(summaries[0]["message_count"], 2)
        self.assertEqual(summaries[0]["brainstorm_count"], 1)

    def test_invalid_session_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.load("../settings")
        with self.assertRaises(ValueError):
            self.store.save({"id": "../x"})

    def test_delete_children_keeps_parent(self) -> None:
        main = self.store.new_session(title="main")
        self.store.save(main)
        brainstorm = self.store.new_session(
            kind=KIND_BRAINSTORM,
            parent_id=str(main["id"]),
            title="讨论",
            brainstorm={
                "topic": "t",
                "focus": "",
                "rounds": 1,
                "participants": [],
                "summary": "",
                "key_disagreements": [],
            },
        )
        self.store.save(brainstorm)
        participant = self.store.new_session(
            kind=KIND_PARTICIPANT,
            parent_id=str(brainstorm["id"]),
            title="participant",
            agent="A",
        )
        self.store.save(participant)

        self.store.delete_children(str(main["id"]))

        self.assertEqual(len(self.store.list_sessions()), 1)
        self.assertEqual(self.store.list_sessions()[0]["id"], main["id"])
        self.assertIsNotNone(self.store.load(str(main["id"])))

    def test_delete_children_never_deletes_target(self) -> None:
        first = self.store.new_session(title="first")
        self.store.save(first)
        second = self.store.new_session(
            title="second", parent_id=str(first["id"])
        )
        self.store.save(second)
        first["parent_id"] = str(second["id"])
        self.store.save(first)

        self.store.delete_children(str(first["id"]))

        self.assertEqual(
            [item["id"] for item in self.store.list_sessions()],
            [first["id"]],
        )
        self.assertIsNotNone(self.store.load(str(first["id"])))

    def test_delete_tree_removes_descendants(self) -> None:
        main = self.store.new_session()
        self.store.save(main)
        brainstorm = self.store.new_session(
            kind=KIND_BRAINSTORM,
            parent_id=str(main["id"]),
            title="讨论",
            brainstorm={
                "topic": "t",
                "focus": "",
                "rounds": 1,
                "participants": [],
                "summary": "",
                "key_disagreements": [],
            },
        )
        self.store.save(brainstorm)

        self.store.delete_tree(str(main["id"]))

        self.assertEqual(self.store.list_sessions(), [])
        self.assertFalse((self.root / f"{brainstorm['id']}.json").exists())

    def test_list_skips_corrupt_files(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "broken.json").write_text("{not json", encoding="utf-8")

        summaries = self.store.list_sessions()

        self.assertEqual(summaries, [])
        self.assertEqual(len(self.store.last_scan_errors), 1)

    def test_load_rejects_invalid_utf8_file(self) -> None:
        session = self.store.new_session()
        session_id = str(session["id"])
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{session_id}.json").write_bytes(b"\xff\xfe\xfd")

        with self.assertRaisesRegex(RuntimeError, "会话文件损坏"):
            self.store.load(session_id)

    def test_save_rejects_self_parent(self) -> None:
        session = self.store.new_session()
        session["parent_id"] = session["id"]

        with self.assertRaises(ValueError):
            self.store.save(session)

    def test_filename_id_mismatch_is_rejected(self) -> None:
        session = self.store.new_session(title="mismatch")
        session_id = str(session["id"])
        other_id = "c" * 32
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{other_id}.json").write_text(
            json.dumps(session, ensure_ascii=False), encoding="utf-8"
        )

        self.assertEqual(self.store.list_sessions(), [])
        self.assertEqual(len(self.store.last_scan_errors), 1)
        with self.assertRaises(RuntimeError):
            self.store.load(session_id)
        with self.assertRaisesRegex(RuntimeError, "会话文件损坏"):
            self.store.load(other_id)

    def test_atomic_write_leaves_no_tmp_file(self) -> None:
        session = self.store.new_session()
        self.store.save(session)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_build_session_title_collapses_and_truncates(self) -> None:
        self.assertEqual(
            build_session_title("  你好   世界  ", fallback="新对话"),
            "你好 世界",
        )
        self.assertEqual(build_session_title("   ", fallback="附件对话"), "附件对话")
        self.assertEqual(len(build_session_title("x" * 100)), 30)

    def test_build_api_messages_filters_flags(self) -> None:
        session = self.store.new_session()
        session["messages"] = [
            new_message("user", "问题"),
            new_message(
                "assistant",
                "错误",
                error="boom",
                in_api=False,
            ),
            new_message(
                "assistant",
                "【头脑风暴总结】结论",
                visible=False,
                kind="brainstorm_summary",
            ),
        ]

        messages = build_api_messages(session, "系统提示")

        self.assertEqual(
            messages,
            [
                {"role": "system", "content": "系统提示"},
                {"role": "user", "content": "问题"},
                {"role": "assistant", "content": "【头脑风暴总结】结论"},
            ],
        )

    def test_new_message_stringifies_media_paths(self) -> None:
        message = new_message(
            "assistant",
            "图片",
            media_paths=[Path("C:/tmp/a.png")],
            muted=True,
        )
        self.assertEqual(message["media_paths"], ["C:\\tmp\\a.png"])
        self.assertTrue(message["muted"])


class SessionValidationTests(unittest.TestCase):
    def test_turn_records_are_valid_on_brainstorm_sessions(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = SessionStore(Path(temporary.name) / "sessions")
        brainstorm = store.new_session(
            kind=KIND_BRAINSTORM,
            title="讨论",
            brainstorm={
                "topic": "t",
                "focus": "",
                "rounds": 1,
                "participants": [],
                "summary": "",
                "key_disagreements": [],
            },
        )
        brainstorm["messages"].append(
            {
                "id": sessions.new_id(),
                "round": 1,
                "participant_session_id": "b" * 32,
                "participant_name": "A",
                "kind": "speak",
                "content": "观点",
                "error": None,
                "created_at": 1.0,
            }
        )
        store.save(brainstorm)
        self.assertEqual(len(store.load(str(brainstorm["id"]))["messages"]), 1)

    def test_interrupted_status_roundtrip(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = SessionStore(Path(temporary.name) / "sessions")
        session = store.new_session()
        session["status"] = STATUS_INTERRUPTED
        store.save(session)
        self.assertEqual(
            store.load(str(session["id"]))["status"], STATUS_INTERRUPTED
        )


def test_new_session_has_permanent_workspace_default():
    from sessions import SessionStore

    store = SessionStore()
    session = store.new_session()
    assert session["permanent_workspace"] is None


def test_new_session_accepts_permanent_workspace_value():
    from sessions import SessionStore

    store = SessionStore()
    session = store.new_session(permanent_workspace="D:/myproject")
    assert session["permanent_workspace"] == "D:/myproject"


if __name__ == "__main__":
    unittest.main()
