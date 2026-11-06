"""建模 skill：把 Blender / Blockbench / GMod 工具暴露给 AI。"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

_VIEWS = frozenset({"front", "back", "left", "right", "three_quarter"})
_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_RESOLUTION_RE = re.compile(r"^(\d{2,4})x(\d{2,4})$")


class ModelingSessionAccess(Protocol):
    def start(self, **kwargs) -> dict: ...
    def open_reference(self, **kwargs) -> dict: ...
    def run_python(self, **kwargs) -> dict: ...
    def render_preview(self, **kwargs) -> dict: ...
    def compile(self, **kwargs) -> dict: ...
    def status(self, **kwargs) -> dict: ...
    def stop(self, **kwargs) -> dict: ...


def _tool(name: str, description: str, properties: dict[str, object], required: list[str]) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


MODELING_SKILLS: list[dict[str, object]] = [
    _tool(
        "start_modeling_job",
        "启动一个 Blender 3.0 后台建模作业，解析 YSM 参考模型并导入参考场景。开始建模前必须先调用；老师明确要求建模时才使用。",
        {
            "source_models_dir": {
                "type": "string",
                "description": "包含 main.json 与 textures/default.png 的 YSM models 目录绝对路径。",
            },
            "output_name": {
                "type": "string",
                "description": "GMod 模型名，只能包含小写字母、数字和下划线，例如 terminus_guard。",
                "pattern": "^[a-z0-9_]+$",
            },
            "brief": {
                "type": "string",
                "description": "老师想要的写实风格、装甲或配色说明；可为空。",
            },
        },
        ["source_models_dir", "output_name"],
    ),
    _tool(
        "open_modeling_reference",
        "用 Blockbench 打开当前作业的 YSM 参考模型，仅供查看。不会自动操作窗口，也不会截图。",
        {
            "app": {
                "type": "string",
                "enum": ["blockbench"],
                "description": "参考模型查看器，目前只支持 blockbench。",
                "default": "blockbench",
            },
        },
        [],
    ),
    _tool(
        "run_blender_python",
        "在当前 Blender 子进程里执行 Python 建模代码。可以 import prana_api；代码在当前用户权限下运行，不是安全沙箱。",
        {
            "code": {
                "type": "string",
                "description": "要执行的 Python 代码，最多 100KB。",
            },
            "description": {
                "type": "string",
                "description": "这一步在做什么，给老师看的简短说明。",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "超时秒数，默认 120，最大 300。",
                "minimum": 1,
                "maximum": 300,
                "default": 120,
            },
        },
        ["code", "description"],
    ),
    _tool(
        "render_modeling_preview",
        "渲染当前模型的预览图，并把 PNG 返回给 AI 查看。每个请求最多 8 张；至少看四视图再判断模型是否可用。",
        {
            "view": {
                "type": "string",
                "enum": ["front", "back", "left", "right", "three_quarter"],
                "description": "相机视角。",
            },
            "resolution": {
                "type": "string",
                "description": "分辨率，格式 宽x高，每边 64–1600，默认 900x1200。",
                "pattern": "^[0-9]{2,4}x[0-9]{2,4}$",
                "default": "900x1200",
            },
            "caption": {
                "type": "string",
                "description": "给这张预览的简短说明，例如“正面装甲比例”。",
            },
        },
        ["view"],
    ),
    _tool(
        "compile_gmod_model",
        "把当前 Blender 场景编译成 GMod 玩家模型 addon，包含身体、第一人称手模、材质、Lua 注册和验证。只有验证通过后才能说模型可用。",
        {
            "output_name": {
                "type": "string",
                "description": "必须与 start_modeling_job 的 output_name 一致。",
                "pattern": "^[a-z0-9_]+$",
            },
            "surfaceprop": {
                "type": "string",
                "description": "Source 表面材质，默认 flesh。",
                "default": "flesh",
            },
            "make_lods": {
                "type": "boolean",
                "description": "是否生成 LOD，默认 true。",
                "default": True,
            },
            "make_hands": {
                "type": "boolean",
                "description": "是否生成并注册第一人称手模，默认 true。",
                "default": True,
            },
        },
        ["output_name"],
    ),
    _tool(
        "modeling_status",
        "查看当前建模作业状态、Blender 进程、当前步骤和最近预览。",
        {},
        [],
    ),
    _tool(
        "stop_modeling_job",
        "保存 work.blend、关闭 Blender 并结束当前建模作业；保留作业目录和产物。",
        {},
        [],
    ),
]


_ALLOWED: dict[str, set[str]] = {
    "start_modeling_job": {"source_models_dir", "output_name", "brief"},
    "open_modeling_reference": {"app"},
    "run_blender_python": {"code", "description", "timeout_seconds"},
    "render_modeling_preview": {"view", "resolution", "caption"},
    "compile_gmod_model": {"output_name", "surfaceprop", "make_lods", "make_hands"},
    "modeling_status": set(),
    "stop_modeling_job": set(),
}

_REQUIRED: dict[str, tuple[str, ...]] = {
    "start_modeling_job": ("source_models_dir", "output_name"),
    "open_modeling_reference": (),
    "run_blender_python": ("code", "description"),
    "render_modeling_preview": ("view",),
    "compile_gmod_model": ("output_name",),
    "modeling_status": (),
    "stop_modeling_job": (),
}


def _require_string(arguments: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = arguments.get(key)
    if not isinstance(value, str):
        raise ValueError(f"参数 {key} 必须是字符串。")
    if not allow_empty and not value.strip():
        raise ValueError(f"参数 {key} 不能为空。")
    return value


def _require_bool(arguments: dict[str, Any], key: str, default: bool) -> bool:
    if key not in arguments:
        return default
    value = arguments[key]
    if not isinstance(value, bool):
        raise ValueError(f"参数 {key} 必须是布尔值。")
    return value


def validate_modeling_skill_arguments(name: str, arguments: object) -> None:
    if not isinstance(arguments, dict):
        raise ValueError("建模 skill 参数必须是对象。")
    if name not in _ALLOWED:
        raise ValueError("未知的建模 skill。")
    unknown = set(arguments) - _ALLOWED[name]
    if unknown:
        raise ValueError(f"建模 skill 包含未知参数：{', '.join(sorted(unknown))}")
    for key in _REQUIRED[name]:
        if key not in arguments:
            raise ValueError(f"缺少必填参数：{key}")

    if name == "start_modeling_job":
        _require_string(arguments, "source_models_dir")
        output_name = _require_string(arguments, "output_name")
        if not _NAME_RE.fullmatch(output_name):
            raise ValueError("output_name 只能包含小写字母、数字和下划线。")
        if "brief" in arguments:
            _require_string(arguments, "brief", allow_empty=True)
    elif name == "open_modeling_reference":
        app = arguments.get("app", "blockbench")
        if app != "blockbench":
            raise ValueError("app 目前只支持 blockbench。")
    elif name == "run_blender_python":
        _require_string(arguments, "code")
        _require_string(arguments, "description", allow_empty=True)
        timeout = arguments.get("timeout_seconds", 120)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout_seconds 必须是数字。")
        if not 1 <= float(timeout) <= 300:
            raise ValueError("timeout_seconds 必须在 1 到 300 之间。")
    elif name == "render_modeling_preview":
        view = _require_string(arguments, "view")
        if view not in _VIEWS:
            raise ValueError(f"view 必须是 {', '.join(sorted(_VIEWS))} 之一。")
        resolution = arguments.get("resolution", "900x1200")
        if not isinstance(resolution, str):
            raise ValueError("resolution 必须是字符串。")
        match = _RESOLUTION_RE.fullmatch(resolution)
        if not match:
            raise ValueError("resolution 必须写成 宽x高，例如 900x1200。")
        width, height = int(match.group(1)), int(match.group(2))
        if not (64 <= width <= 1600 and 64 <= height <= 1600):
            raise ValueError("resolution 每边必须在 64 到 1600 之间。")
        if "caption" in arguments:
            _require_string(arguments, "caption", allow_empty=True)
    elif name == "compile_gmod_model":
        output_name = _require_string(arguments, "output_name")
        if not _NAME_RE.fullmatch(output_name):
            raise ValueError("output_name 只能包含小写字母、数字和下划线。")
        if "surfaceprop" in arguments:
            _require_string(arguments, "surfaceprop")
        _require_bool(arguments, "make_lods", True)
        _require_bool(arguments, "make_hands", True)


def invoke_modeling_skill(name: str, arguments: object, *, access: ModelingSessionAccess) -> str:
    validate_modeling_skill_arguments(name, arguments)
    assert isinstance(arguments, dict)
    if name == "start_modeling_job":
        result = access.start(
            source_models_dir=arguments["source_models_dir"],
            output_name=arguments["output_name"],
            brief=arguments.get("brief", ""),
        )
    elif name == "open_modeling_reference":
        result = access.open_reference(app=arguments.get("app", "blockbench"))
    elif name == "run_blender_python":
        result = access.run_python(
            code=arguments["code"],
            description=arguments.get("description", ""),
            timeout_seconds=float(arguments.get("timeout_seconds", 120)),
        )
    elif name == "render_modeling_preview":
        result = access.render_preview(
            view=arguments["view"],
            resolution=arguments.get("resolution", "900x1200"),
            caption=arguments.get("caption", ""),
        )
    elif name == "compile_gmod_model":
        result = access.compile(
            output_name=arguments["output_name"],
            surfaceprop=arguments.get("surfaceprop", "flesh"),
            make_lods=arguments.get("make_lods", True),
            make_hands=arguments.get("make_hands", True),
        )
    elif name == "modeling_status":
        result = access.status()
    else:
        result = access.stop()
    return json.dumps(result, ensure_ascii=False)


__all__ = [
    "MODELING_SKILLS",
    "ModelingSessionAccess",
    "invoke_modeling_skill",
    "validate_modeling_skill_arguments",
]
