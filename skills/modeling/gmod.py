"""Source 资产生成：SMD、QC、VMT、VTF、Lua 与 addon 打包。"""

from __future__ import annotations

import json
import math
import shutil
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .source import parse_mdl


@dataclass(frozen=True)
class SmdBone:
    name: str
    parent: int


@dataclass(frozen=True)
class SmdVertex:
    parent: int
    pos: tuple[float, float, float]
    normal: tuple[float, float, float]
    uv: tuple[float, float]
    links: tuple[tuple[int, float], ...]


@dataclass(frozen=True)
class SmdTriangle:
    material: str
    a: int
    b: int
    c: int


def _fmt(value: float) -> str:
    return f"{float(value):.6f}"


def write_smd(
    path: Path,
    *,
    bones: Sequence[SmdBone],
    vertices: Sequence[SmdVertex],
    triangles: Sequence[SmdTriangle],
    frame: int = 0,
) -> None:
    lines = ["version 1", "nodes"]
    for index, bone in enumerate(bones):
        lines.append(f'{index} "{bone.name}" {bone.parent}')
    lines.extend(["end", "skeleton", f"time {frame}"])
    for index in range(len(bones)):
        lines.append(f"{index} 0 0 0 0 0 0 0")
    lines.extend(["end", "triangles"])
    for triangle in triangles:
        lines.append(triangle.material)
        for vertex_index in (triangle.a, triangle.b, triangle.c):
            vertex = vertices[vertex_index]
            links = " ".join(f"{bone} {_fmt(weight)}" for bone, weight in vertex.links)
            lines.append(
                f"{vertex.parent} "
                f"{_fmt(vertex.pos[0])} {_fmt(vertex.pos[1])} {_fmt(vertex.pos[2])} "
                f"{_fmt(vertex.normal[0])} {_fmt(vertex.normal[1])} {_fmt(vertex.normal[2])} "
                f"{_fmt(vertex.uv[0])} {_fmt(vertex.uv[1])} {links}"
            )
    lines.append("end")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_body_qc(
    name: str,
    *,
    surfaceprop: str = "flesh",
    lods: Sequence[tuple[int, str]] = (),
) -> str:
    lines = [
        f'$modelname "player/{name}.mdl"',
        f'$model "body" "{name}.smd"',
        f'$cdmaterials "models/player/{name}/"',
        f'$surfaceprop "{surfaceprop}"',
        "$eyeposition 0 0 70",
        "$illumposition 0 0 36",
        '$includemodel "models/m_anm.mdl"',
        f'$collisionjoints "{name}_physics.smd"',
        "{",
        "\t$mass 80",
        "\t$inertia 1",
        "\t$damping 0.01",
        "\t$rotdamping 1.5",
        '\t$rootbone "ValveBiped.Bip01_Pelvis"',
        "\t$jointconstrain \"ValveBiped.Bip01_Spine\" x limit -30 30 0",
        "\t$jointconstrain \"ValveBiped.Bip01_Spine\" y limit -30 30 0",
        "\t$jointconstrain \"ValveBiped.Bip01_Spine\" z limit -30 30 0",
        "}",
    ]
    for distance, lod_path in lods:
        lines.append(f'$lod {int(distance)} replace "{lod_path}"')
    return "\n".join(lines) + "\n"


def build_hands_qc(name: str, *, surfaceprop: str = "flesh") -> str:
    return "\n".join(
        [
            f'$modelname "player/{name}_arms.mdl"',
            f'$model "hands" "{name}_arms.smd"',
            f'$cdmaterials "models/player/{name}/"',
            f'$surfaceprop "{surfaceprop}"',
            '$includemodel "models/weapons/c_arms_animations.mdl"',
        ]
    ) + "\n"


def build_vmt(base_texture: str, bumpmap: str | None = None) -> str:
    lines = [
        '"VertexLitGeneric"',
        "{",
        f'\t"$basetexture" "{base_texture}"',
        "\t$model 1",
        '\t"$phong" "1"',
        '\t"$phongexponent" "12"',
        '\t"$phongboost" "1.2"',
    ]
    if bumpmap:
        lines.append(f'\t"$bumpmap" "{bumpmap}"')
    lines.append("}")
    return "\n".join(lines) + "\n"


def _next_mip(rgba: bytes, width: int, height: int) -> tuple[bytes, int, int]:
    new_width = max(1, width // 2)
    new_height = max(1, height // 2)
    out = bytearray(new_width * new_height * 4)
    for y in range(new_height):
        for x in range(new_width):
            src = []
            for sy in (y * 2, min(y * 2 + 1, height - 1)):
                for sx in (x * 2, min(x * 2 + 1, width - 1)):
                    offset = (sy * width + sx) * 4
                    src.append(rgba[offset:offset + 4])
            for channel in range(4):
                out[(y * new_width + x) * 4 + channel] = sum(pixel[channel] for pixel in src) // 4
    return bytes(out), new_width, new_height


def write_vtf(path: Path, *, width: int, height: int, rgba: bytes) -> None:
    if width <= 0 or height <= 0:
        raise ValueError("VTF 宽高必须大于 0。")
    if len(rgba) != width * height * 4:
        raise ValueError("RGBA 数据长度必须等于 width*height*4。")

    mip_count = int(math.floor(math.log2(max(width, height)))) + 1
    header = bytearray()
    header += b"VTF\0"
    header += struct.pack("<I", 7)
    header += struct.pack("<I", 2)
    header += struct.pack("<I", 80)
    header += struct.pack("<H", width)
    header += struct.pack("<H", height)
    header += struct.pack("<I", 0)
    header += struct.pack("<H", 1)
    header += struct.pack("<H", 0)
    header += b"\0" * 4
    header += b"\0" * 12
    header += b"\0" * 4
    header += struct.pack("<f", 1.0)
    header += struct.pack("<I", 0)  # RGBA8888
    header += bytes([mip_count])
    header += struct.pack("<I", 0xFFFFFFFF)  # no low-res image
    header += bytes([0, 0])
    header += struct.pack("<H", 1)
    header += b"\0" * 3
    header += struct.pack("<I", 0)
    header += b"\0" * 8
    if len(header) != 80:
        raise AssertionError(f"VTF header must be 80 bytes, got {len(header)}")

    data = bytearray(header)
    current = rgba
    current_width, current_height = width, height
    for _ in range(mip_count):
        data += current
        if current_width == 1 and current_height == 1:
            break
        current, current_width, current_height = _next_mip(current, current_width, current_height)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))


def build_addon_lua(name: str, title: str) -> str:
    title_literal = json.dumps(title, ensure_ascii=False)
    return (
        "-- 由 Prana 建模工具生成；同时注册玩家模型和第一人称手模。\n"
        f'player_manager.AddValidModel("{name}", "models/player/{name}.mdl", '
        f'{title_literal}, "#spawnmenu.category.other")\n'
        f'player_manager.AddValidHands("{name}", "models/player/{name}_arms.mdl", 0, "0000000")\n'
    )


def package_addon(
    *,
    addon_dir: Path,
    name: str,
    body_files: Sequence[Path],
    hands_files: Sequence[Path],
    materials: Sequence[Path],
    title: str,
) -> Path:
    addon_dir = Path(addon_dir)
    player_dir = addon_dir / "models" / "player"
    material_dir = addon_dir / "materials" / "models" / "player" / name
    player_dir.mkdir(parents=True, exist_ok=True)
    material_dir.mkdir(parents=True, exist_ok=True)

    for source in body_files:
        source = Path(source)
        shutil.copy2(source, player_dir / source.name)
    for source in hands_files:
        source = Path(source)
        shutil.copy2(source, player_dir / source.name)
    for source in materials:
        source = Path(source)
        shutil.copy2(source, material_dir / source.name)

    lua_dir = addon_dir / "lua" / "autorun"
    lua_dir.mkdir(parents=True, exist_ok=True)
    (lua_dir / f"{name}.lua").write_text(build_addon_lua(name, title), encoding="utf-8")
    return addon_dir


def validate_compiled_model(
    model_path: Path,
    *,
    expected_bones: int,
    expected_include: str,
) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []
    info = None
    try:
        info = parse_mdl(Path(model_path).read_bytes())
    except Exception as exc:  # 保留给工具层转成可操作错误
        errors.append(f"无法解析 MDL：{exc}")
    if info is not None:
        if info.version > 49:
            errors.append(f"MDL 版本 {info.version} 高于 49。")
        if len(info.bones) != expected_bones:
            errors.append(f"骨骼数 {len(info.bones)}，期望 {expected_bones}。")
        if expected_include not in info.includes:
            errors.append(f"缺少 include：{expected_include}。")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


__all__ = [
    "SmdBone",
    "SmdTriangle",
    "SmdVertex",
    "build_addon_lua",
    "build_body_qc",
    "build_hands_qc",
    "build_vmt",
    "package_addon",
    "validate_compiled_model",
    "write_smd",
    "write_vtf",
]
