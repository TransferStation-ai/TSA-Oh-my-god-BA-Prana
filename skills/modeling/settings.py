"""建模工具路径解析。

优先使用用户在设置里填写的路径，其次检查本机和常见安装位置。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


class ModelingToolError(RuntimeError):
    """建模工具路径不可用。"""


def _local_app_data() -> Path:
    value = os.environ.get("LOCALAPPDATA")
    return Path(value) if value else Path.home() / "AppData" / "Local"


BLENDER_CANDIDATES: tuple[Path, ...] = (
    Path(r"D:\mc\blender-3.0.1-windows-x64\blender.exe"),
    Path(r"D:\mc\blender\blender.exe"),
    Path(r"C:\Program Files\Blender Foundation\Blender 3.0\blender.exe"),
    Path(r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe"),
)

BLOCKBENCH_CANDIDATES: tuple[Path, ...] = (
    Path(r"D:\mc\Blockbench\Blockbench.exe"),
    _local_app_data() / "Programs" / "Blockbench" / "Blockbench.exe",
    Path(r"C:\Program Files\Blockbench\Blockbench.exe"),
)

GMOD_CANDIDATES: tuple[Path, ...] = (
    Path(r"D:\Administrator\Steam\steamapps\common\GarrysMod"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\GarrysMod"),
    Path(r"C:\Program Files\Steam\steamapps\common\GarrysMod"),
)

SFM_CANDIDATES: tuple[Path, ...] = (
    Path(r"E:\SteamLibrary\steamapps\common\SourceFilmmaker"),
    Path(r"D:\SteamLibrary\steamapps\common\SourceFilmmaker"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\SourceFilmmaker"),
)


def _setting_path(settings: Mapping[str, str], key: str) -> str:
    value = settings.get(key, "")
    return str(value).strip() if value is not None else ""


def _resolve(
    *,
    tool_name: str,
    key: str,
    settings: Mapping[str, str],
    candidates: tuple[Path, ...],
    expect_dir: bool,
) -> Path:
    explicit = _setting_path(settings, key)
    if explicit:
        path = Path(explicit).expanduser()
        valid = path.is_dir() if expect_dir else path.is_file()
        if not valid:
            kind = "目录" if expect_dir else "文件"
            raise ModelingToolError(
                f"{tool_name} 路径不可用：{path}。请在设置中填写 {key}，指向有效的{kind}。"
            )
        return path

    for candidate in candidates:
        try:
            valid = candidate.is_dir() if expect_dir else candidate.is_file()
        except OSError:
            valid = False
        if valid:
            return candidate

    raise ModelingToolError(
        f"找不到可用的 {tool_name}，请在设置中填写 {key}。"
    )


def resolve_blender(settings: Mapping[str, str]) -> Path:
    return _resolve(
        tool_name="Blender",
        key="blender_path",
        settings=settings,
        candidates=BLENDER_CANDIDATES,
        expect_dir=False,
    )


def resolve_blockbench(settings: Mapping[str, str]) -> Path:
    return _resolve(
        tool_name="Blockbench",
        key="blockbench_path",
        settings=settings,
        candidates=BLOCKBENCH_CANDIDATES,
        expect_dir=False,
    )


def resolve_gmod(settings: Mapping[str, str]) -> Path:
    return _resolve(
        tool_name="Garry's Mod",
        key="gmod_dir",
        settings=settings,
        candidates=GMOD_CANDIDATES,
        expect_dir=True,
    )


def resolve_sfm(settings: Mapping[str, str]) -> Path | None:
    try:
        return _resolve(
            tool_name="Source Filmmaker",
            key="sfm_dir",
            settings=settings,
            candidates=SFM_CANDIDATES,
            expect_dir=True,
        )
    except ModelingToolError:
        return None


__all__ = [
    "BLENDER_CANDIDATES",
    "BLOCKBENCH_CANDIDATES",
    "GMOD_CANDIDATES",
    "SFM_CANDIDATES",
    "ModelingToolError",
    "resolve_blender",
    "resolve_blockbench",
    "resolve_gmod",
    "resolve_sfm",
]
