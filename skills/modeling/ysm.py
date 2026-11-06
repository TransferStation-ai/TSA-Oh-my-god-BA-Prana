"""YSM / Bedrock geometry 参考模型解析。

这里只做纯 Python 解析，不依赖 Blender，方便主进程测试和提前发现坏模型。
"""

from __future__ import annotations

import json
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class YsmParseError(ValueError):
    """YSM 参考模型无法解析。"""


@dataclass(frozen=True)
class YsmCube:
    bone: str
    origin: tuple[float, float, float]
    size: tuple[float, float, float]
    uv: object = None
    rotation: tuple[float, float, float] | None = None
    pivot: tuple[float, float, float] | None = None
    inflate: float | None = None


@dataclass(frozen=True)
class YsmBone:
    name: str
    parent: str | None
    pivot: tuple[float, float, float]
    rotation: tuple[float, float, float] | None
    cubes: tuple[YsmCube, ...]


@dataclass(frozen=True)
class HandReference:
    side: str
    wrist_bone: str
    finger_groups: tuple[str, ...]
    finger_count: int
    segments_per_finger: int
    has_thumb: bool
    warning: str | None


@dataclass(frozen=True)
class YsmModel:
    path: Path
    format_version: str
    identifier: str
    texture_width: int
    texture_height: int
    visible_bounds_width: float
    visible_bounds_height: float
    bones: tuple[YsmBone, ...]
    cubes: tuple[YsmCube, ...]
    height: float
    hands: tuple[HandReference, ...]
    warnings: tuple[str, ...]

    def scale_to_units(self, target_height: float = 72.0) -> float:
        if self.height <= 0:
            raise YsmParseError("模型高度必须大于 0。")
        return float(target_height) / self.height


def _vec3(value: object, field: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise YsmParseError(f"{field} 必须是三个数字的数组。")
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError) as exc:
        raise YsmParseError(f"{field} 必须是三个数字的数组。") from exc


def _optional_vec3(value: object, field: str) -> tuple[float, float, float] | None:
    if value is None:
        return None
    return _vec3(value, field)


def read_png_size(path: Path) -> tuple[int, int]:
    """从 PNG 的 IHDR 读取宽高，不依赖 Pillow。"""
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise YsmParseError(f"不是有效的 PNG：{path}")
    if data[12:16] != b"IHDR":
        raise YsmParseError(f"PNG 缺少 IHDR：{path}")
    width, height = struct.unpack(">II", data[16:24])
    if width <= 0 or height <= 0:
        raise YsmParseError(f"PNG 尺寸无效：{path}")
    return int(width), int(height)


def _check_bone_tree(bones: Sequence[YsmBone]) -> tuple[str, ...]:
    warnings: list[str] = []
    by_name = {bone.name: bone for bone in bones}
    for bone in bones:
        if bone.parent is not None and bone.parent not in by_name:
            warnings.append(f"骨骼 {bone.name} 的父骨骼 {bone.parent} 不存在。")

    for bone in bones:
        seen: set[str] = set()
        current: str | None = bone.name
        while current is not None:
            if current in seen:
                raise YsmParseError(f"骨骼父子关系存在环：{bone.name}")
            seen.add(current)
            parent = by_name.get(current)
            if parent is None:
                break
            current = parent.parent
    return tuple(warnings)


def _small_cube_bone(bone: YsmBone) -> bool:
    if not bone.cubes:
        return False
    largest = max(max(abs(value) for value in cube.size) for cube in bone.cubes)
    return largest <= 2.5


def _subtree(start: YsmBone, children: dict[str, list[YsmBone]]) -> list[YsmBone]:
    result: list[YsmBone] = []
    stack = list(children.get(start.name, ()))
    while stack:
        bone = stack.pop()
        result.append(bone)
        stack.extend(children.get(bone.name, ()))
    return result


def _chain_length(bone: YsmBone, children: dict[str, list[YsmBone]]) -> int:
    best = 1
    for child in children.get(bone.name, ()):
        if _small_cube_bone(child):
            best = max(best, 1 + _chain_length(child, children))
    return best


def _has_thumb(bones: Sequence[YsmBone]) -> bool:
    for bone in bones:
        name = bone.name.lower()
        if "thumb" in name or "pollex" in name or "拇指" in name:
            return True
    return False


def extract_hand_references(bones: Sequence[YsmBone]) -> tuple[HandReference, ...]:
    by_name = {bone.name: bone for bone in bones}
    children: dict[str, list[YsmBone]] = {}
    for bone in bones:
        if bone.parent:
            children.setdefault(bone.parent, []).append(bone)

    references: list[HandReference] = []
    for side, hand_name in (("left", "LeftHand"), ("right", "RightHand")):
        hand = by_name.get(hand_name)
        if hand is None or hand.parent is None:
            continue
        wrist = by_name.get(hand.parent)
        if wrist is None:
            continue

        subtree = _subtree(wrist, children)
        candidates = [
            bone
            for bone in subtree
            if _small_cube_bone(bone)
            and any(_small_cube_bone(child) for child in children.get(bone.name, ()))
        ]
        candidate_names = {bone.name for bone in candidates}
        roots: list[YsmBone] = []
        for bone in candidates:
            current = bone.parent
            has_candidate_ancestor = False
            while current and current != wrist.name:
                if current in candidate_names:
                    has_candidate_ancestor = True
                    break
                parent = by_name.get(current)
                current = parent.parent if parent else None
            if not has_candidate_ancestor:
                roots.append(bone)

        roots.sort(key=lambda bone: bone.name)
        finger_count = len(roots)
        segments = max((_chain_length(root, children) for root in roots), default=0)
        thumb = _has_thumb(roots)
        warning = None
        if finger_count < 5 or not thumb:
            warning = (
                f"参考模型{('左' if side == 'left' else '右')}手只有 "
                f"{finger_count} 根手指且没有拇指，建模时必须补足为 5 指。"
            )
        references.append(
            HandReference(
                side=side,
                wrist_bone=wrist.name,
                finger_groups=tuple(root.name for root in roots),
                finger_count=finger_count,
                segments_per_finger=segments,
                has_thumb=thumb,
                warning=warning,
            )
        )
    return tuple(references)


def parse_ysm(models_dir: Path) -> YsmModel:
    main_json = Path(models_dir) / "main.json"
    if not main_json.is_file():
        raise YsmParseError(f"找不到 main.json：{main_json}")

    try:
        data = json.loads(main_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise YsmParseError(f"main.json JSON 解析失败：{main_json}") from exc

    if not isinstance(data, dict):
        raise YsmParseError("main.json 顶层必须是对象。")
    geometries = data.get("minecraft:geometry")
    if not isinstance(geometries, list) or not geometries:
        raise YsmParseError("main.json 缺少 minecraft:geometry 列表。")
    geometry = geometries[0]
    if not isinstance(geometry, dict):
        raise YsmParseError("minecraft:geometry[0] 必须是对象。")
    description = geometry.get("description")
    if not isinstance(description, dict):
        description = {}
    raw_bones = geometry.get("bones")
    if not isinstance(raw_bones, list):
        raise YsmParseError("minecraft:geometry[0].bones 必须是列表。")

    texture_width = int(description.get("texture_width", 64) or 64)
    texture_height = int(description.get("texture_height", 64) or 64)
    warnings: list[str] = []
    texture_path = Path(models_dir).parent / "textures" / "default.png"
    if texture_path.is_file():
        try:
            texture_width, texture_height = read_png_size(texture_path)
        except YsmParseError as exc:
            warnings.append(f"贴图读取失败，使用 geometry 尺寸：{exc}")
    else:
        warnings.append("贴图 default.png 不存在，使用 geometry 中的贴图尺寸。")

    bones: list[YsmBone] = []
    all_cubes: list[YsmCube] = []
    uv_warnings: set[str] = set()
    for raw_bone in raw_bones:
        if not isinstance(raw_bone, dict):
            raise YsmParseError("bones 中每个骨骼必须是对象。")
        name = raw_bone.get("name")
        if not isinstance(name, str) or not name:
            raise YsmParseError("骨骼缺少 name。")
        parent = raw_bone.get("parent")
        if parent is not None and not isinstance(parent, str):
            raise YsmParseError(f"骨骼 {name} 的 parent 必须是字符串。")
        pivot = _vec3(raw_bone.get("pivot", [0, 0, 0]), f"骨骼 {name} 的 pivot")
        rotation = _optional_vec3(raw_bone.get("rotation"), f"骨骼 {name} 的 rotation")
        raw_cubes = raw_bone.get("cubes", [])
        if raw_cubes is None:
            raw_cubes = []
        if not isinstance(raw_cubes, list):
            raise YsmParseError(f"骨骼 {name} 的 cubes 必须是列表。")
        cubes: list[YsmCube] = []
        for raw_cube in raw_cubes:
            if not isinstance(raw_cube, dict):
                raise YsmParseError(f"骨骼 {name} 的 cube 必须是对象。")
            origin = _vec3(raw_cube.get("origin", [0, 0, 0]), f"骨骼 {name} 的 cube.origin")
            size = _vec3(raw_cube.get("size", [0, 0, 0]), f"骨骼 {name} 的 cube.size")
            uv = raw_cube.get("uv")
            if isinstance(uv, dict):
                for face, value in uv.items():
                    if not isinstance(value, dict):
                        continue
                    raw_uv = value.get("uv")
                    if not isinstance(raw_uv, (list, tuple)) or len(raw_uv) < 2:
                        continue
                    try:
                        u = float(raw_uv[0])
                        v = float(raw_uv[1])
                    except (TypeError, ValueError):
                        continue
                    if u < 0 or v < 0 or u > texture_width or v > texture_height:
                        uv_warnings.add(f"UV 坐标超出贴图尺寸：{name}/{face} ({u:g}, {v:g})")
            cube = YsmCube(
                bone=name,
                origin=origin,
                size=size,
                uv=uv,
                rotation=_optional_vec3(raw_cube.get("rotation"), f"骨骼 {name} 的 cube.rotation"),
                pivot=_optional_vec3(raw_cube.get("pivot"), f"骨骼 {name} 的 cube.pivot"),
                inflate=float(raw_cube["inflate"]) if raw_cube.get("inflate") is not None else None,
            )
            cubes.append(cube)
            all_cubes.append(cube)
        bones.append(YsmBone(name=name, parent=parent, pivot=pivot, rotation=rotation, cubes=tuple(cubes)))

    warnings.extend(_check_bone_tree(bones))
    warnings.extend(sorted(uv_warnings))

    if not all_cubes:
        raise YsmParseError("模型没有任何 cube，无法计算高度。")
    lowest = min(cube.origin[1] for cube in all_cubes)
    highest = max(cube.origin[1] + cube.size[1] for cube in all_cubes)
    height = highest - lowest
    if height <= 0:
        raise YsmParseError("模型高度必须大于 0。")

    hands = extract_hand_references(bones)
    if not hands:
        warnings.append("未找到 LeftHand/RightHand 手部参考。")
    warnings.extend(hand.warning for hand in hands if hand.warning)

    return YsmModel(
        path=main_json,
        format_version=str(data.get("format_version", "")),
        identifier=str(description.get("identifier", "")),
        texture_width=texture_width,
        texture_height=texture_height,
        visible_bounds_width=float(description.get("visible_bounds_width", 0) or 0),
        visible_bounds_height=float(description.get("visible_bounds_height", 0) or 0),
        bones=tuple(bones),
        cubes=tuple(all_cubes),
        height=height,
        hands=hands,
        warnings=tuple(warnings),
    )


__all__ = [
    "HandReference",
    "YsmBone",
    "YsmCube",
    "YsmModel",
    "YsmParseError",
    "extract_hand_references",
    "parse_ysm",
    "read_png_size",
]
