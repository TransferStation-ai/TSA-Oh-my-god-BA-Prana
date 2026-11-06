from __future__ import annotations

import ast
import json
import math
from pathlib import Path
import re
import threading
from typing import Callable

MAX_SKILL_SOURCE_CHARS = 8_000
MAX_SKILL_STEPS = 16
MAX_SKILL_DESCRIPTION_CHARS = 160
MAX_SAVED_SKILLS = 32
SKILL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
_STORE_LOCK = threading.RLock()
SKILL_ACTIONS = {
    "scan",
    "inventory",
    "mine",
    "craft",
    "equip",
    "move",
    "eat",
    "use",
    "interact",
    "place",
    "attack",
    "follow",
    "stop",
    "say",
    "msg",
}


def parse_skill_source(name: str, source: str) -> list[dict[str, object]]:
    if not isinstance(name, str) or SKILL_NAME_PATTERN.fullmatch(name) is None:
        raise ValueError("技能名称只能包含小写字母、数字和下划线，且须以字母开头。")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("技能代码不能为空。")
    if len(source) > MAX_SKILL_SOURCE_CHARS:
        raise ValueError("技能代码不能超过 8000 个字符。")
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as error:
        raise ValueError(f"技能代码语法无效：{error.msg}") from error
    if (
        len(tree.body) != 1
        or not isinstance(tree.body[0], ast.FunctionDef)
        or tree.body[0].name != "run"
    ):
        raise ValueError("技能代码只能定义一个名为 run(api) 的函数。")
    function = tree.body[0]
    if (
        function.decorator_list
        or function.args.posonlyargs
        or len(function.args.args) != 1
        or function.args.args[0].arg != "api"
        or function.args.kwonlyargs
        or function.args.vararg is not None
        or function.args.kwarg is not None
        or function.args.defaults
        or function.args.kw_defaults
        or function.returns is not None
        or function.type_comment is not None
        or function.args.args[0].annotation is not None
        or getattr(function, "type_params", [])
    ):
        raise ValueError("技能函数必须使用唯一参数 api，且不能包含装饰器或类型标注。")
    if not function.body or len(function.body) > MAX_SKILL_STEPS:
        raise ValueError(f"技能必须包含 1 到 {MAX_SKILL_STEPS} 个游戏动作。")

    steps: list[dict[str, object]] = []
    for statement in function.body:
        if not isinstance(statement, ast.Expr) or not isinstance(
            statement.value, ast.Call
        ):
            raise ValueError("技能函数体只能包含 api 动作调用。")
        call = statement.value
        if (
            not isinstance(call.func, ast.Attribute)
            or not isinstance(call.func.value, ast.Name)
            or call.func.value.id != "api"
            or call.func.attr not in SKILL_ACTIONS
            or call.args
            or any(keyword.arg is None for keyword in call.keywords)
        ):
            raise ValueError("技能只能调用白名单中的 api 动作，并使用命名参数。")
        arguments: dict[str, object] = {}
        for keyword in call.keywords:
            if keyword.arg in arguments:
                raise ValueError("技能动作不能重复指定参数。")
            try:
                value = ast.literal_eval(keyword.value)
            except (ValueError, TypeError, SyntaxError) as error:
                raise ValueError("技能动作参数只能使用文本或数字常量。") from error
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise ValueError("技能动作参数只能使用文本或数字常量。")
            if isinstance(value, str) and len(value) > 250:
                raise ValueError("技能动作中的文本参数不能超过 250 个字符。")
            if isinstance(value, int) and abs(value) > 2**53:
                raise ValueError("技能动作中的整数参数超出允许范围。")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("技能动作中的数字参数必须是有限值。")
            arguments[keyword.arg] = value
        action = call.func.attr
        from skills.game import validate_game_skill_arguments

        validate_game_skill_arguments(
            "game_command",
            {"game": "mc", "action": action, **arguments},
        )
        steps.append({"action": action, **arguments})
    return steps


class GameSkillStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        name: str,
        description: str,
        source: str,
        review: Callable[[dict[str, object], list[dict[str, object]]], None],
        messages: list[dict[str, object]],
    ) -> dict[str, object]:
        if not isinstance(description, str) or not description.strip():
            raise ValueError("技能描述不能为空。")
        description = description.strip()
        if len(description) > MAX_SKILL_DESCRIPTION_CHARS:
            raise ValueError("技能描述不能超过 160 个字符。")
        steps = parse_skill_source(name, source)
        path = self.directory / f"{name}.json"
        if path.exists():
            raise ValueError("同名技能已存在，请使用新的技能名称。")
        if len(self.list_skills()) >= MAX_SAVED_SKILLS:
            raise ValueError(f"最多只能保存 {MAX_SAVED_SKILLS} 个 MC 技能。")
        review(
            {"name": name, "description": description, "source": source, "steps": steps},
            messages,
        )
        payload = {
            "name": name,
            "description": description,
            "source": source,
            "steps": steps,
        }
        temporary = self.directory / f".{name}.tmp"
        with _STORE_LOCK:
            if path.exists():
                raise ValueError("同名技能已存在，请使用新的技能名称。")
            if len(self.list_skills()) >= MAX_SAVED_SKILLS:
                raise ValueError(f"最多只能保存 {MAX_SAVED_SKILLS} 个 MC 技能。")
            try:
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temporary.replace(path)
            except OSError:
                temporary.unlink(missing_ok=True)
                raise
        return {
            "name": name,
            "description": description,
            "step_count": len(steps),
        }

    def list_skills(self) -> list[dict[str, object]]:
        skills: list[dict[str, object]] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise RuntimeError(f"无法读取已保存的 MC 技能：{path.name}") from error
            if (
                not isinstance(payload, dict)
                or payload.get("name") != path.stem
                or not isinstance(payload.get("description"), str)
                or not payload["description"].strip()
                or len(payload["description"]) > MAX_SKILL_DESCRIPTION_CHARS
                or not isinstance(payload.get("source"), str)
            ):
                raise RuntimeError(f"已保存的 MC 技能格式无效：{path.name}")
            steps = parse_skill_source(path.stem, payload["source"])
            skills.append(
                {
                    "name": path.stem,
                    "description": payload["description"],
                    "steps": steps,
                }
            )
            if len(skills) > MAX_SAVED_SKILLS:
                raise RuntimeError(
                    f"MC 技能数量超过上限（{MAX_SAVED_SKILLS}）。"
                )
        return skills

    def run(
        self,
        name: str,
        execute: Callable[[str, dict[str, object]], str],
    ) -> list[str]:
        if not isinstance(name, str) or SKILL_NAME_PATTERN.fullmatch(name) is None:
            raise ValueError("技能名称无效。")
        skill = next(
            (item for item in self.list_skills() if item["name"] == name),
            None,
        )
        if skill is None:
            raise ValueError(f"未找到已保存的 MC 技能：{name}")
        results: list[str] = []
        for step in skill["steps"]:
            action = step["action"]
            arguments = dict(step)
            arguments.pop("action")
            results.append(execute(f"mc_{action}", arguments))
        return results

    def tool_schemas(self) -> list[dict[str, object]]:
        skills = self.list_skills()
        if not skills:
            return []
        names = ", ".join(
            f"{item['name']}（{item['description']}）" for item in skills
        )
        return [
            {
                "type": "function",
                "function": {
                    "name": "mc_run_skill",
                    "description": f"运行一个已经审核并保存的 MC 技能。可用技能：{names}",
                    "parameters": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                },
            }
        ]


__all__ = ["GameSkillStore", "parse_skill_source"]
