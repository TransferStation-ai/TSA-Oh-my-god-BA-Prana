from __future__ import annotations

import json
import math
import re
from typing import Protocol

MC_GAME = "mc"
PLAYER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,16}$")
MC_ITEM_PATTERN = re.compile(r"^(?:[a-z0-9_.-]+:)?[a-z0-9_]+$")
MAX_GAME_MESSAGE_LENGTH = 250
GAME_COMMAND_ACTIONS = (
    "follow",
    "say",
    "msg",
    "stop",
    "status",
    "mine",
    "inventory",
    "equip",
    "move",
    "eat",
    "interact",
    "place",
    "attack",
)

GAME_SKILLS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "probe_game_server",
            "description": "探测本机是否有 Minecraft Java 服务器。",
            "parameters": {
                "type": "object",
                "properties": {"game": {"type": "string", "enum": ["mc"]}},
                "required": ["game"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "join_game",
            "description": "经操作审查后加入指定 Minecraft Java 服务器。",
            "parameters": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "enum": ["mc"]},
                    "host": {"type": "string"},
                    "port": {"type": "integer"},
                    "player_name": {"type": "string"},
                    "version": {"type": "string"},
                },
                "required": ["game", "host", "port"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "game_command",
            "description": "向当前游戏会话发送一个陪玩动作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "game": {"type": "string", "enum": ["mc"]},
                    "action": {
                        "type": "string",
                        "enum": list(GAME_COMMAND_ACTIONS),
                    },
                    "target": {"type": "string"},
                    "text": {"type": "string"},
                    "block": {"type": "string"},
                    "item": {"type": "string"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "z": {"type": "number"},
                    "kind": {"type": "string", "enum": ["block", "entity"]},
                },
                "required": ["game", "action"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "leave_game",
            "description": "结束当前游戏会话。",
            "parameters": {
                "type": "object",
                "properties": {"game": {"type": "string", "enum": ["mc"]}},
                "required": ["game"],
                "additionalProperties": False,
            },
        },
    },
]


class GameSessionAccess(Protocol):
    def probe(self, game: str) -> dict[str, object]: ...

    def join(
        self,
        game: str,
        host: str,
        port: int,
        player_name: str = "",
        version: str | None = None,
    ) -> dict[str, object]: ...

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
    ) -> dict[str, object]: ...

    def leave(self, game: str) -> dict[str, object]: ...

    def status(self, game: str) -> dict[str, object]: ...


def validate_game_skill_arguments(name: str, arguments: object) -> None:
    if not isinstance(arguments, dict):
        raise ValueError("游戏 skill 参数必须是对象。")
    schemas = {
        "probe_game_server": {"game"},
        "join_game": {"game", "host", "port", "player_name", "version"},
        "game_command": {
            "game",
            "action",
            "target",
            "text",
            "block",
            "item",
            "x",
            "y",
            "z",
            "kind",
        },
        "leave_game": {"game"},
    }
    if name not in schemas:
        raise ValueError("未知的游戏 skill。")
    if set(arguments) - schemas[name]:
        raise ValueError("游戏 skill 包含未知参数。")
    if "game" not in arguments:
        raise ValueError("游戏类型不能为空。")
    if arguments["game"] != MC_GAME:
        raise ValueError('一期只支持 Minecraft（game 必须为 "mc"）。')

    if name == "join_game":
        if not isinstance(arguments.get("host"), str) or not arguments["host"].strip():
            raise ValueError("服务器地址不能为空。")
        port = arguments.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("端口必须在 1-65535 之间。")
        player_name = arguments.get("player_name", "")
        if not isinstance(player_name, str) or (
            player_name and PLAYER_NAME_PATTERN.fullmatch(player_name) is None
        ):
            raise ValueError(
                "Minecraft 玩家名只能使用 1-16 位字母、数字或下划线。"
            )
        if "version" in arguments and not isinstance(arguments["version"], str):
            raise ValueError("Minecraft 版本必须是文本。")
    elif name == "game_command":
        action = arguments.get("action")
        if action not in GAME_COMMAND_ACTIONS:
            raise ValueError("未知的游戏动作。")
        action_fields = {
            "follow": {"target"},
            "say": {"text"},
            "msg": {"target", "text"},
            "stop": set(),
            "status": set(),
            "mine": {"block"},
            "inventory": set(),
            "equip": {"item"},
            "move": {"x", "y", "z"},
            "eat": {"item"},
            "interact": {"target", "kind"},
            "place": {"item", "x", "y", "z"},
            "attack": {"target"},
        }
        supplied_fields = set(arguments) - {"game", "action"}
        if supplied_fields - action_fields[action]:
            raise ValueError("游戏动作包含不适用的参数。")

        target = arguments.get("target", "")
        text = arguments.get("text", "")
        if action in {"follow", "msg"} and (
            not isinstance(target, str) or not target.strip()
        ):
            raise ValueError("follow 和 msg 需要指定玩家名。")
        if action in {"interact", "attack"} and (
            not isinstance(target, str) or not target.strip()
        ):
            raise ValueError("此动作需要指定目标。")
        if action in {"say", "msg"}:
            if not isinstance(text, str) or not text:
                raise ValueError("游戏内消息不能为空。")
            if len(text) > MAX_GAME_MESSAGE_LENGTH:
                raise ValueError("游戏内消息不能超过 250 个字符。")
        if action == "mine":
            block = arguments.get("block")
            if not isinstance(block, str) or MC_ITEM_PATTERN.fullmatch(block) is None:
                raise ValueError("采矿需要有效的方块注册名。")
        if action in {"equip", "eat", "place"}:
            item = arguments.get("item")
            if not isinstance(item, str) or MC_ITEM_PATTERN.fullmatch(item) is None:
                raise ValueError("此动作需要有效的物品注册名。")
        if action in {"move", "place"}:
            coordinates = (arguments.get("x"), arguments.get("y"), arguments.get("z"))
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in coordinates
            ):
                raise ValueError("移动和放置方块需要有效的 x、y、z 坐标。")
            if action == "place" and any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in coordinates
            ):
                raise ValueError("放置方块的坐标必须是整数。")
        if action == "interact":
            if arguments.get("kind") not in {"block", "entity"}:
                raise ValueError("交互目标类型必须是 block 或 entity。")


def invoke_game_skill(
    name: str,
    arguments: object,
    *,
    access: GameSessionAccess,
) -> str:
    validate_game_skill_arguments(name, arguments)
    if not isinstance(arguments, dict):
        raise ValueError("游戏 skill 参数必须是对象。")
    game = str(arguments["game"])
    if name == "probe_game_server":
        result = access.probe(game)
    elif name == "join_game":
        result = access.join(
            game,
            str(arguments["host"]),
            int(arguments["port"]),
            str(arguments.get("player_name", "")),
            str(arguments["version"]) if arguments.get("version") else None,
        )
    elif name == "game_command":
        action = str(arguments["action"])
        if action == "status":
            result = access.status(game)
        else:
            result = access.command(
                game,
                action,
                target=str(arguments.get("target", "")),
                text=str(arguments.get("text", "")),
                block=str(arguments.get("block", "")),
                item=str(arguments.get("item", "")),
                x=arguments.get("x"),
                y=arguments.get("y"),
                z=arguments.get("z"),
                kind=str(arguments.get("kind", "")),
            )
    else:
        result = access.leave(game)
    return json.dumps(result, ensure_ascii=False)


__all__ = [
    "GAME_SKILLS",
    "GameSessionAccess",
    "invoke_game_skill",
    "validate_game_skill_arguments",
]
