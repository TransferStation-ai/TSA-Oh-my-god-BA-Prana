from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Callable

from skills.game.agent import GameBrain
from skills.game.custom_skills import GameSkillStore
from skills.game.mc import (
    McBody,
    McBodyError,
    app_data_dir,
    body_source_dir,
    ensure_body_ready,
    find_node,
    find_npm,
    prepare_skin,
)
from skills.game.probe import detect_local_mc


class GameSessionError(RuntimeError):
    pass


class GameSession:
    def __init__(
        self,
        *,
        game_settings: dict[str, str],
        data_dir: Path,
        complete: Callable[
            [list[dict[str, object]], list[dict[str, object]]],
            dict[str, object],
        ],
        on_event: Callable[[str, dict[str, object]], None],
        review_skill: Callable[
            [dict[str, object], list[dict[str, object]]], None
        ]
        | None = None,
        body_factory: Callable[..., McBody] | None = None,
        body_command: list[str] | None = None,
    ) -> None:
        self.game_settings = dict(game_settings)
        self.data_dir = Path(data_dir)
        self.complete = complete
        self.on_event = on_event
        self.review_skill = review_skill
        self.custom_skills = GameSkillStore(self.data_dir / "game_skills")
        self.body_factory = body_factory or McBody
        self.body_command = body_command
        self.body: McBody | None = None
        self.brain: GameBrain | None = None
        self._active_game: str | None = None
        self._lock = threading.RLock()

    @property
    def active_game(self) -> str | None:
        return self._active_game

    def probe(
        self, game: str, port: int | None = None
    ) -> dict[str, object]:
        if game != "mc":
            raise ValueError("一期只支持 Minecraft。")
        if port is not None and (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            raise ValueError("端口必须在 1-65535 之间。")
        servers = (
            detect_local_mc()
            if port is None
            else detect_local_mc((port,))
        )
        return {
            "found": bool(servers),
            "servers": servers,
            "hint": (
                "已找到本地 Minecraft 服务器。"
                if servers
                else "未找到本地服务器，请提供服务器 IP 和端口。"
            ),
        }

    def join(
        self,
        game: str,
        host: str,
        port: int,
        player_name: str = "",
        version: str | None = None,
    ) -> dict[str, object]:
        if game != "mc":
            raise ValueError("一期只支持 Minecraft。")
        with self._lock:
            if self._active_game is not None:
                self.leave(self._active_game)
            username = player_name or self.game_settings.get("mc_name", "Prana")
            skin_path, skin_note = prepare_skin(
                self.game_settings, self.data_dir / "assets"
            )
            if self.body_command is None:
                node = find_node()
                npm = find_npm()
                body_dir = ensure_body_ready(
                    app_data_dir() / "mc_body",
                    source=body_source_dir(),
                    npm=npm,
                )
                command = [node, str(body_dir / "index.js")]
                cwd = body_dir
            else:
                command = list(self.body_command)
                cwd = Path.cwd()
            body = self.body_factory(
                command,
                cwd,
                on_event=self._on_body_event,
            )
            self.body = body
            try:
                body.start()
                body_result = body.join(
                    host=host,
                    port=port,
                    username=username,
                    skin_path=skin_path,
                    version=version,
                    skin_command=self.game_settings.get("mc_skin_command", ""),
                )
                self._active_game = "mc"
                self.brain = GameBrain(
                    game="mc",
                    complete=self.complete,
                    execute=self._execute_action,
                    on_say=body.say,
                    create_skill=self._create_custom_skill,
                    custom_skill_tools=self.custom_skills.tool_schemas,
                    on_error=lambda message: self.on_event(
                        "error", {"message": message}
                    ),
                )
                self.brain.start()
            except Exception:
                try:
                    body.quit()
                finally:
                    self.body = None
                    self.brain = None
                    self._active_game = None
                raise
            result = {
                "game": "mc",
                "host": host,
                "port": port,
                "player_name": username,
                "version": version or body_result.get("version", ""),
                "skin": "prepared" if skin_path else "default",
                "skin_note": skin_note,
                "players": body_result.get("players", []),
            }
            self.on_event("joined", result)
            return result

    def _on_body_event(self, event: dict[str, object]) -> None:
        name = event.get("event")
        data = event.get("data")
        payload = data if isinstance(data, dict) else {}
        if name == "chat":
            if self.brain is not None:
                self.brain.submit(payload)
            self.on_event(str(payload.get("type", "chat")), payload)
        elif name in {"error", "end"}:
            message = str(payload.get("message", "MC 连接已断开。"))
            self.on_event("error", {"message": message})
            threading.Thread(
                target=self._cleanup_after_disconnect,
                name="PranaMcDisconnectCleanup",
                daemon=True,
            ).start()

    def _cleanup_after_disconnect(self) -> None:
        try:
            self.leave("mc")
        except (McBodyError, RuntimeError) as error:
            self.on_event(
                "error", {"message": f"结束 Minecraft 会话失败：{error}"}
            )

    def _execute_action(
        self, name: str, arguments: dict[str, object]
    ) -> str:
        body = self.body
        if body is None:
            return "游戏本体当前未运行。"
        if name == "mc_run_skill":
            if set(arguments) != {"name"} or not isinstance(
                arguments.get("name"), str
            ):
                raise ValueError("运行技能需要且只能指定技能名称。")
            results = self.custom_skills.run(
                arguments["name"], self._execute_action
            )
            return json.dumps(results, ensure_ascii=False)
        if name == "mc_follow":
            body.follow(str(arguments.get("target", "")))
        elif name == "mc_say":
            body.say(str(arguments.get("text", "")))
        elif name == "mc_msg":
            body.msg(
                str(arguments.get("target", "")),
                str(arguments.get("text", "")),
            )
        elif name == "mc_stop":
            body.stop_follow()
        elif name == "mc_scan":
            result = body.scan(
                max_distance=arguments.get("max_distance", 24)
            )
            return json.dumps(result, ensure_ascii=False)
        elif name == "mc_mine":
            result = body.mine(str(arguments.get("block", "")))
            return str(result.get("message", "采矿已完成。"))
        elif name == "mc_craft":
            result = body.craft(
                str(arguments.get("item", "")),
                arguments.get("count", 1),
            )
            return str(result.get("message", "合成已完成。"))
        elif name == "mc_inventory":
            items = body.inventory()
            if not items:
                return "背包是空的。"
            return "背包：" + "、".join(
                f"{item.get('name', '未知物品')} ×{item.get('count', '?')}"
                for item in items
            )
        elif name == "mc_equip":
            body.equip(str(arguments.get("item", "")))
        elif name == "mc_move":
            result = body.move_to(
                float(arguments["x"]),
                float(arguments["y"]),
                float(arguments["z"]),
            )
            return str(result.get("message", "已到达目标坐标。"))
        elif name == "mc_eat":
            body.eat(str(arguments.get("item", "")))
        elif name == "mc_use":
            result = body.use(str(arguments.get("item", "")))
            return str(result.get("message", f"已使用 {arguments.get('item', '')}。"))
        elif name == "mc_interact":
            body.interact(
                str(arguments.get("target", "")),
                str(arguments.get("kind", "")),
            )
        elif name == "mc_place":
            body.place(
                str(arguments.get("item", "")),
                int(arguments["x"]),
                int(arguments["y"]),
                int(arguments["z"]),
            )
        elif name == "mc_attack":
            body.attack(str(arguments.get("target", "")))
        else:
            return "不允许执行此游戏动作。"
        return "已执行。"

    def _create_custom_skill(
        self,
        arguments: dict[str, object],
        messages: list[dict[str, object]],
    ) -> str:
        if set(arguments) != {"name", "description", "source"}:
            raise ValueError("创建技能需要且只能提供名称、描述和代码。")
        if not all(
            isinstance(arguments.get(field), str)
            for field in ("name", "description", "source")
        ):
            raise ValueError("技能名称、描述和代码必须是文本。")
        if self.review_skill is None:
            raise RuntimeError("未配置操作审查模型，不能创建 MC 技能。")
        result = self.custom_skills.create(
            name=arguments["name"],
            description=arguments["description"],
            source=arguments["source"],
            review=self.review_skill,
            messages=messages,
        )
        return json.dumps(
            {"status": "saved", **result},
            ensure_ascii=False,
        )

    def command(
        self,
        game: str,
        action: str,
        *,
        target: str = "",
        text: str = "",
        block: str = "",
        item: str = "",
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        kind: str = "",
        max_distance: int = 24,
        count: int = 1,
    ) -> dict[str, object]:
        if game != self._active_game or game != "mc":
            raise GameSessionError("当前没有运行中的 Minecraft 会话。")
        body = self.body
        if body is None:
            raise GameSessionError("MC 本体当前未运行。")
        if action == "follow":
            body.follow(target)
        elif action == "say":
            body.say(text)
        elif action == "msg":
            body.msg(target, text)
        elif action == "stop":
            body.stop_follow()
        elif action == "scan":
            result = body.scan(max_distance=max_distance)
            return {"ok": True, "action": action, **result}
        elif action == "mine":
            result = body.mine(block)
            return {"ok": True, "action": action, **result}
        elif action == "craft":
            result = body.craft(item, count)
            return {"ok": True, "action": action, **result}
        elif action == "inventory":
            return {"ok": True, "action": action, "items": body.inventory()}
        elif action == "equip":
            body.equip(item)
        elif action == "move":
            result = body.move_to(x, y, z)
            return {"ok": True, "action": action, **result}
        elif action == "eat":
            body.eat(item)
        elif action == "use":
            result = body.use(item)
            return {"ok": True, "action": action, **result}
        elif action == "interact":
            body.interact(target, kind)
        elif action == "place":
            body.place(item, x, y, z)
        elif action == "attack":
            body.attack(target)
        else:
            raise ValueError("未知的游戏动作。")
        return {"ok": True, "action": action}

    def status(self, game: str) -> dict[str, object]:
        if game != self._active_game or self.body is None:
            raise GameSessionError("当前没有运行中的游戏会话。")
        return self.body.status()

    def leave(self, game: str) -> dict[str, object]:
        if game != "mc":
            raise ValueError("当前没有对应的游戏会话。")
        if self._active_game is None and self.body is None and self.brain is None:
            return {"ok": True, "game": "mc"}
        if self._active_game != game:
            raise ValueError("当前没有对应的游戏会话。")
        first_error: Exception | None = None
        brain, body = self.brain, self.body
        self.brain = None
        self.body = None
        self._active_game = None
        if brain is not None:
            try:
                brain.stop()
            except Exception as error:
                first_error = error
        if body is not None:
            try:
                body.quit()
            except Exception as error:
                if first_error is None:
                    first_error = error
        self.on_event("left", {"game": "mc"})
        if first_error is not None:
            raise GameSessionError(f"结束游戏会话时发生错误：{first_error}") from first_error
        return {"ok": True, "game": "mc"}

    def close(self) -> None:
        if self._active_game is None:
            return
        try:
            self.leave(self._active_game)
        except (GameSessionError, RuntimeError):
            return


__all__ = ["GameSession", "GameSessionError"]
