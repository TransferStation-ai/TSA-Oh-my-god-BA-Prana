import json
import unittest

from skills.game.gm import (
    gm_follow_script,
    gm_join_script,
    gm_leave_script,
    gm_say_script,
    gm_scan_script,
    gm_stop_script,
    run_lua_snippet,
)


class GmLuaScriptTests(unittest.TestCase):
    def test_join_script_spawns_once(self) -> None:
        s1 = gm_join_script()
        self.assertIn("npc_gunman", s1)
        self.assertIn("PRANA_GM_ACK", s1)
        self.assertIn("hook.Add", s1)
        self.assertIn("PRUNA_NPC", s1)
        self.assertIn("ChatPlayer", s1)
        self.assertNotIn("\n", s1)

    def test_join_script_default_name_and_model_branch(self) -> None:
        s = gm_join_script()
        self.assertIn(json.dumps("普拉娜", ensure_ascii=False), s)
        # 无模型时不应调用 SetModel 分支以外的路径；模型为空串。
        s2 = gm_join_script(player_model="models/player/soldier.mdl")
        self.assertIn(json.dumps("models/player/soldier.mdl", ensure_ascii=False), s2)
        self.assertNotIn("\n", s2)

    def test_say_script_escapes_json(self) -> None:
        s = gm_say_script('a "b" \\ c')
        self.assertIn(json.dumps('a "b" \\ c'), s)
        self.assertIn("PlayerSay", s)
        self.assertIn("PRANA_GM_ACK", s)
        self.assertNotIn("\n", s)

    def test_follow_script_moves_toward_target(self) -> None:
        s = gm_follow_script("Alice")
        self.assertIn(json.dumps("Alice", ensure_ascii=False), s)
        self.assertIn("SetVelocity", s)
        self.assertIn('hook.Add("Think"', s)
        self.assertIn("PRUNA_NPC", s)
        self.assertNotIn("\n", s)
        local = gm_follow_script("老师")
        self.assertIn(json.dumps("老师", ensure_ascii=False), local)

    def test_stop_script_removes_think(self) -> None:
        s = gm_stop_script()
        self.assertIn('hook.Remove("Think"', s)
        self.assertIn("PRANA_GM_ACK", s)
        self.assertNotIn("\n", s)

    def test_scan_script_emits_json_marker(self) -> None:
        s = gm_scan_script()
        self.assertIn("PRANA_GM_JSON", s)
        self.assertIn("json.encode", s)
        self.assertIn("player.GetAll", s)
        self.assertNotIn("\n", s)
        bounded = gm_scan_script(max_players=3)
        self.assertIn("3", bounded)

    def test_leave_script_cleans_up(self) -> None:
        s = gm_leave_script()
        self.assertIn('hook.Remove("ChatPlayer"', s)
        self.assertIn("PRUNA_NPC=nil", s)
        self.assertIn("PRANA_GM_ACK", s)
        self.assertNotIn("\n", s)

    def test_snippet_rejects_newline(self) -> None:
        with self.assertRaises(ValueError):
            run_lua_snippet(object(), "x\ny")

    def test_snippet_prepends_lua_run_and_delegates(self) -> None:
        captured = {}

        class FakeConsole:
            def run(self, command, *, timeout=10.0):
                captured["command"] = command
                return {"ok": True, "feedback": True, "lines": [], "json": None, "chat": []}

        result = run_lua_snippet(FakeConsole(), gm_say_script("hi"))
        self.assertTrue(result["feedback"])
        self.assertTrue(captured["command"].startswith("lua_run "))
        self.assertIn("PlayerSay", captured["command"])


if __name__ == "__main__":
    unittest.main()
