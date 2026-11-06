"""读取 Garry's Mod VPK，并解析 Source MDL 骨骼。

只依赖标准库；不启动任何 Source 工具。
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class SourceAssetError(RuntimeError):
    """Source 资产缺失或格式不符合预期。"""


@dataclass(frozen=True)
class VpkEntry:
    path: str
    archive_index: int
    offset: int
    length: int
    preload: bytes = b""


class VpkArchive:
    """VPK v2 目录档案读取器。"""

    def __init__(self, dir_vpk: Path):
        self.path = Path(dir_vpk)
        if not self.path.is_file():
            raise SourceAssetError(f"找不到 garrysmod_dir.vpk：{self.path}")
        data = self.path.read_bytes()
        if len(data) < 28:
            raise SourceAssetError(f"VPK 头不完整：{self.path}")
        self._signature, self._version, tree_size = struct.unpack_from("<III", data, 0)
        if self._signature != 0x55AA1234:
            raise SourceAssetError(f"不是 VPK v2 文件：{self.path}")
        self._data_offset = 28 + int(tree_size)
        if self._data_offset > len(data):
            raise SourceAssetError(f"VPK 目录树超出文件范围：{self.path}")
        tree = data[28 : self._data_offset]
        self._entries: dict[str, VpkEntry] = {}
        self._parse_tree(tree)

    @staticmethod
    def _read_cstr(tree: bytes, offset: int) -> tuple[str, int]:
        end = tree.find(b"\x00", offset)
        if end < 0:
            raise SourceAssetError("VPK 目录树字符串没有终止符。")
        return tree[offset:end].decode("utf-8", errors="replace"), end + 1

    def _parse_tree(self, tree: bytes) -> None:
        pos = 0
        while pos < len(tree):
            extension, pos = self._read_cstr(tree, pos)
            if extension == "":
                break
            while pos < len(tree):
                path, pos = self._read_cstr(tree, pos)
                if path == "":
                    break
                while pos < len(tree):
                    filename, pos = self._read_cstr(tree, pos)
                    if filename == "":
                        break
                    if pos + 18 > len(tree):
                        raise SourceAssetError("VPK 目录条目被截断。")
                    _crc, preload_len, archive_index, entry_offset, entry_length, terminator = struct.unpack_from(
                        "<IHHIIH", tree, pos
                    )
                    pos += 18
                    if terminator != 0xFFFF:
                        raise SourceAssetError("VPK 目录条目结束标记无效。")
                    if preload_len:
                        preload = tree[pos : pos + preload_len]
                        pos += preload_len
                    else:
                        preload = b""
                    full = f"{path}/{filename}.{extension}" if path else f"{filename}.{extension}"
                    full = full.replace("\\", "/").strip("/").lower()
                    self._entries[full] = VpkEntry(
                        path=full,
                        archive_index=int(archive_index),
                        offset=int(entry_offset),
                        length=int(entry_length),
                        preload=preload,
                    )

    @property
    def entries(self) -> Mapping[str, VpkEntry]:
        return self._entries

    def read(self, path: str) -> bytes:
        normalized = path.replace("\\", "/").strip("/").lower()
        entry = self._entries.get(normalized)
        if entry is None:
            raise SourceAssetError(f"VPK 中不存在：{path}")
        if entry.archive_index == 0x7FFF:
            archive_path = self.path
            offset = self._data_offset + entry.offset
        else:
            archive_path = self.path.parent / f"garrysmod_{entry.archive_index:03d}.vpk"
            offset = entry.offset
        if not archive_path.is_file():
            raise SourceAssetError(f"VPK 分卷不存在：{archive_path}")
        with archive_path.open("rb") as handle:
            handle.seek(offset)
            payload = handle.read(entry.length)
        if len(payload) != entry.length:
            raise SourceAssetError(
                f"VPK 文件被截断：{path}（期望 {entry.length} 字节，实际 {len(payload)}）"
            )
        return entry.preload + payload


@dataclass(frozen=True)
class MdlBone:
    name: str
    parent: int
    pos: tuple[float, float, float]
    rot: tuple[float, float, float]


@dataclass(frozen=True)
class MdlInfo:
    version: int
    bones: tuple[MdlBone, ...]
    includes: tuple[str, ...]


@dataclass(frozen=True)
class Skeleton:
    version: int
    bones: tuple[MdlBone, ...]
    includes: tuple[str, ...]
    source: str


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _i32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def _float3(data: bytes, offset: int) -> tuple[float, float, float]:
    return struct.unpack_from("<fff", data, offset)


def parse_mdl(data: bytes) -> MdlInfo:
    if len(data) < 344 or data[:4] != b"IDST":
        raise SourceAssetError("不是有效的 Source MDL（缺少 IDST 头）。")
    version = _u32(data, 4)
    num_bones = _u32(data, 156)
    bone_index = _u32(data, 160)
    num_includes = _u32(data, 336)
    include_index = _u32(data, 340)

    bones: list[MdlBone] = []
    for index in range(num_bones):
        bone_offset = bone_index + index * 216
        if bone_offset + 216 > len(data):
            raise SourceAssetError(f"MDL 骨骼 {index} 超出文件范围。")
        name_rel = _u32(data, bone_offset)
        name_offset = bone_offset + name_rel
        if name_offset >= len(data):
            raise SourceAssetError(f"MDL 骨骼 {index} 名称超出文件范围。")
        end = data.find(b"\x00", name_offset)
        if end < 0:
            end = len(data)
        name = data[name_offset:end].decode("utf-8", errors="replace")
        parent = _i32(data, bone_offset + 4)
        pos = _float3(data, bone_offset + 32)
        rot = _float3(data, bone_offset + 60)
        bones.append(MdlBone(name=name, parent=parent, pos=pos, rot=rot))

    includes: list[str] = []
    for index in range(num_includes):
        entry_offset = include_index + index * 8
        if entry_offset + 8 > len(data):
            raise SourceAssetError(f"MDL include 条目 {index} 超出文件范围。")
        name_rel = _i32(data, entry_offset + 4)
        name_offset = entry_offset + name_rel
        if name_offset < 0 or name_offset >= len(data):
            raise SourceAssetError(f"MDL include 条目 {index} 名称超出文件范围。")
        end = data.find(b"\x00", name_offset)
        if end < 0:
            end = len(data)
        includes.append(data[name_offset:end].decode("utf-8", errors="replace"))

    return MdlInfo(version=version, bones=tuple(bones), includes=tuple(includes))


def _load_skeleton(gmod_dir: Path, asset: str, expected_bones: int, expected_include: str) -> Skeleton:
    archive_path = Path(gmod_dir) / "garrysmod" / "garrysmod_dir.vpk"
    archive = VpkArchive(archive_path)
    info = parse_mdl(archive.read(asset))
    if len(info.bones) != expected_bones:
        raise SourceAssetError(
            f"{asset} 骨骼数不匹配：实际 {len(info.bones)}，期望 {expected_bones}。"
        )
    if expected_include not in info.includes:
        raise SourceAssetError(
            f"{asset} include 不匹配：实际 {info.includes}，期望包含 {expected_include}。"
        )
    return Skeleton(
        version=info.version,
        bones=info.bones,
        includes=info.includes,
        source=f"{archive_path}::{asset}",
    )


def load_player_skeleton(gmod_dir: Path) -> Skeleton:
    return _load_skeleton(
        gmod_dir,
        "models/player/group01/male_01.mdl",
        68,
        "models/m_anm.mdl",
    )


def load_hands_skeleton(gmod_dir: Path) -> Skeleton:
    return _load_skeleton(
        gmod_dir,
        "models/weapons/c_arms_citizen.mdl",
        43,
        "models/weapons/c_arms_animations.mdl",
    )


__all__ = [
    "MdlBone",
    "MdlInfo",
    "Skeleton",
    "SourceAssetError",
    "VpkArchive",
    "VpkEntry",
    "load_hands_skeleton",
    "load_player_skeleton",
    "parse_mdl",
]
