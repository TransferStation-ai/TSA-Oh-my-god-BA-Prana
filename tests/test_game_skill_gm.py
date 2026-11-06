import unittest

from skills.game import GM_COMMAND_ACTIONS, invoke_game_skill


class FakeAccess:
    """实现 GameSessionAccess 五方法，全部返回 {"ok": True}。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def probe(self, game, port=None):
        self.calls.append(("probe", game, port))
        return {"ok": True, "found": True, "servers": [], "hint": ""}

    def join(self, game, host, port, player_name="", version=None):
        self.calls.append(("join", game, host, port, player_name, version))
        return {"ok": True, "game": game, "host": host, "port": port}

    def command(self, game, action, **kwargs):
        self.calls.append(("command", game, action))
        return {"ok": True, "action": action}

    def leave(self, game):
        self.calls.append(("leave", game))
        return {"ok": True}

    def status(self, game):
        self.calls.append(("status", game))
        return {"ok": True, "game": game}


class GameSkillGmTests(unittest.TestCase):
    def test_gm_command_actions_constant(self) -> None:
        self.assertEqual(GM_COMMAND_ACTIONS, ("follow", "say", "stop", "status", "scan"))

    def test_gm_say_validation(self) -> None:
        access = FakeAccess()
        result = invoke_game_skill(
            "game_command", {"game": "gm", "action": "say", "text": "hi"}, access=access
        )
        self.assertIn('"ok": true', result)
        self.assertEqual(access.calls[-1][0], "command")

    def test_gm_follow_and_stop_allowed(self) -> None:
        for action, args in (("follow", {}), ("stop", {}), ("status", {})):
            invoke_game_skill(
                "game_command", {"game": "gm", "action": action, **args}, access=FakeAccess()
            )

    def test_gm_scan_rejects_bad_distance(self) -> None:
        with self.assertRaises(ValueError):
            invoke_game_skill(
                "game_command",
                {"game": "gm", "action": "scan", "max_distance": 999},
                access=FakeAccess(),
            )

    def test_gm_unknown_action_rejected(self) -> None:
        with self.assertRaises(ValueError):
            invoke_game_skill(
                "game_command",
                {"game": "gm", "action": "mine", "block": "stone"},
                access=FakeAccess(),
            )

    def test_gm_move_rejected(self) -> None:
        with self.assertRaises(ValueError):
            invoke_game_skill(
                "game_command",
                {"game": "gm", "action": "move", "x": 1, "y": 2, "z": 3},
                access=FakeAccess(),
            )

    def test_gm_join_rejects_bad_player_name(self) -> None:
        # 抽样回归：gm 路径下 player_name 仍须为合法名称（忽略 version）。
        with self.assertRaises(ValueError):
            invoke_game_skill(
                "join_game",
                {"game": "gm", "host": "127.0.0.1", "port": 27015, "player_name": "Prana!!"},
                access=FakeAccess(),
            )

    def test_gm_join_ok(self) -> None:
        access = FakeAccess()
        result = invoke_game_skill(
            "join_game",
            {"game": "gm", "host": "127.0.0.1", "port": 27015, "player_name": "普拉娜"},
            access=access,
        )
        self.assertIn('"ok": true', result)

    def test_mc_behavior_unchanged(self) -> None:
        # 抽样回归：mc 路径动作/参数校验行为保持不变。
        with self.assertRaises(ValueError):
            invoke_game_skill(
                "game_command",
                {"game": "mc", "action": "mine", "block": "bad!!stone"},
                access=FakeAccess(),
            )
        # mc 拒绝 gm 专属动作不在其中（mine 合法）——合法 mc say。
        result = invoke_game_skill(
            "game_command", {"game": "mc", "action": "say", "text": "hi"}, access=FakeAccess()
        )
        self.assertIn('"ok": true', result)

    def test_schema_game_enum_supports_gm(self) -> None:
        from skills.game import GAME_SKILLS

        for skill in GAME_SKILLS:
            function = skill["function"]
            if function["name"] in ("probe_game_server", "join_game", "game_command", "leave_game"):
                enum = function["parameters"]["properties"]["game"]["enum"]
                self.assertIn("mc", enum)
                self.assertIn("gm", enum)


if __name__ == "__main__":
    unittest.main()
