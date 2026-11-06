from __future__ import annotations

import socket
import struct

GM_PROBE_HOST = "127.0.0.1"
GM_PROBE_PORTS = (27015,)
GM_PROBE_TIMEOUT = 2.0
GM_DEFAULT_PORT = 27015

_A2S_QUERY = b"\xff\xff\xff\xffA2S_INFO\x0e" + struct.pack("<I", 0)


class GmProbeError(RuntimeError):
    pass


def _read_string(data: bytes, offset: int) -> tuple[str, int]:
    end = data.find(b"\x00", offset)
    if end < 0:
        raise GmProbeError("GMod 服务器响应不完整。")
    return data[offset:end].decode("utf-8", "replace"), end + 1


def _parse_a2s(data: bytes, host: str, port: int) -> dict[str, object]:
    if not data:
        raise GmProbeError("GMod 服务器响应不完整。")
    response_type = data[0]
    if response_type not in (0x64, 0xD2):
        raise GmProbeError("目标服务不是 GMod 服务器。")
    offset = 1
    if response_type == 0x64:
        offset += 2  # u16 报文长度字段
    if offset + 4 > len(data):
        raise GmProbeError("GMod 服务器响应不完整。")
    protocol = struct.unpack_from("<H", data, offset)[0]
    offset += 4  # u16 protocol + 2 字节双引擎版本
    name, offset = _read_string(data, offset)
    map_name, offset = _read_string(data, offset)
    folder, offset = _read_string(data, offset)
    game_id, offset = _read_string(data, offset)
    if offset + 4 > len(data):
        raise GmProbeError("GMod 服务器响应不完整。")
    players, max_players = struct.unpack_from("<HH", data, offset)
    return {
        "host": host,
        "port": port,
        "protocol": protocol,
        "name": name,
        "map": map_name,
        "game": folder,
        "is_gmod": folder.lower() == "garrysmod",
        "steam_id": game_id,
        "players": players,
        "max_players": max_players,
    }


def a2s_info(
    host: str,
    port: int,
    *,
    timeout: float = GM_PROBE_TIMEOUT,
) -> dict[str, object]:
    if not host.strip() or not 1 <= port <= 65535:
        raise GmProbeError("GMod 服务器地址或端口无效。")
    last_error: Exception | None = None
    for _attempt in range(2):  # 首发 + 1 次重发
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
                stream.settimeout(timeout)
                stream.sendto(_A2S_QUERY, (host, port))
                data, _ = stream.recvfrom(4096)
            return _parse_a2s(data, host, port)
        except (socket.timeout, TimeoutError, OSError) as error:
            last_error = error
    raise GmProbeError(f"无法读取 GMod 服务器状态：{last_error}")


def detect_local_gm(
    ports: tuple[int, ...] = GM_PROBE_PORTS,
    *,
    host: str = GM_PROBE_HOST,
    timeout: float = GM_PROBE_TIMEOUT,
) -> list[dict[str, object]]:
    servers: list[dict[str, object]] = []
    for port in ports:
        try:
            servers.append(a2s_info(host, port, timeout=timeout))
        except GmProbeError:
            continue
    return servers


__all__ = [
    "GM_DEFAULT_PORT",
    "GM_PROBE_HOST",
    "GM_PROBE_PORTS",
    "GM_PROBE_TIMEOUT",
    "GmProbeError",
    "a2s_info",
    "detect_local_gm",
]
