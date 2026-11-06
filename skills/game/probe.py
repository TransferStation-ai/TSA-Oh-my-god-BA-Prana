from __future__ import annotations

import json
import socket
import struct
from typing import Any

MC_PROBE_HOST = "127.0.0.1"
MC_PROBE_PORTS = tuple(range(25565, 25570))
MC_PROBE_TIMEOUT = 0.6
MC_PROTOCOL_VERSION = 763


class ProbeError(RuntimeError):
    pass


def encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("VarInt 不能为负数。")
    output = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            output.append(byte | 0x80)
        else:
            output.append(byte)
            return bytes(output)


def decode_varint(buffer: bytes, offset: int = 0) -> tuple[int, int]:
    result = 0
    for index in range(5):
        position = offset + index
        if position >= len(buffer):
            raise ProbeError("Minecraft 服务器响应不完整。")
        byte = buffer[position]
        result |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            if result > 0x7FFFFFFF:
                raise ProbeError("Minecraft 服务器返回了无效长度。")
            return result, position + 1
    raise ProbeError("Minecraft 服务器返回了无效 VarInt。")


def _read_varint(stream: socket.socket) -> int:
    value = 0
    for index in range(5):
        byte = stream.recv(1)
        if not byte:
            raise ProbeError("Minecraft 服务器提前关闭了连接。")
        current = byte[0]
        value |= (current & 0x7F) << (7 * index)
        if not current & 0x80:
            return value
    raise ProbeError("Minecraft 服务器返回了无效 VarInt。")


def _read_exact(stream: socket.socket, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        part = stream.recv(length - len(result))
        if not part:
            raise ProbeError("Minecraft 服务器响应不完整。")
        result.extend(part)
    return bytes(result)


def _flatten_chat(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        own = value.get("text", "")
        extra = value.get("extra", [])
        return (
            (own if isinstance(own, str) else "")
            + "".join(_flatten_chat(item) for item in extra)
            if isinstance(extra, list)
            else (own if isinstance(own, str) else "")
        )
    if isinstance(value, list):
        return "".join(_flatten_chat(item) for item in value)
    return ""


def slp_ping(
    host: str,
    port: int,
    *,
    timeout: float = MC_PROBE_TIMEOUT,
) -> dict[str, object]:
    if not host.strip() or not 1 <= port <= 65535:
        raise ProbeError("Minecraft 服务器地址或端口无效。")
    hostname = host.encode("utf-8")
    handshake = (
        encode_varint(0)
        + encode_varint(MC_PROTOCOL_VERSION)
        + encode_varint(len(hostname))
        + hostname
        + struct.pack(">H", port)
        + encode_varint(1)
    )
    packet = encode_varint(len(handshake)) + handshake + b"\x01\x00"
    try:
        with socket.create_connection((host, port), timeout=timeout) as stream:
            stream.settimeout(timeout)
            stream.sendall(packet)
            packet_length = _read_varint(stream)
            packet_data = _read_exact(stream, packet_length)
            packet_id, offset = decode_varint(packet_data)
            if packet_id != 0:
                raise ProbeError("目标服务不是 Minecraft Java 服务器。")
            string_length, offset = decode_varint(packet_data, offset)
            payload = json.loads(
                packet_data[offset : offset + string_length].decode("utf-8")
            )
    except ProbeError:
        raise
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        struct.error,
        ValueError,
    ) as error:
        raise ProbeError(f"无法读取 Minecraft 服务器状态：{error}") from error
    if not isinstance(payload, dict):
        raise ProbeError("Minecraft 服务器返回了无效状态。")
    version = payload.get("version")
    players = payload.get("players")
    if not isinstance(version, dict) or not isinstance(players, dict):
        raise ProbeError("Minecraft 服务器状态缺少版本或玩家信息。")
    return {
        "host": host,
        "port": port,
        "version": str(version.get("name", "")),
        "protocol": version.get("protocol"),
        "players_online": players.get("online", 0),
        "players_max": players.get("max", 0),
        "motd": _flatten_chat(payload.get("description", "")),
    }


def detect_local_mc(
    ports: tuple[int, ...] = MC_PROBE_PORTS,
    *,
    host: str = MC_PROBE_HOST,
    timeout: float = MC_PROBE_TIMEOUT,
) -> list[dict[str, object]]:
    servers: list[dict[str, object]] = []
    for port in ports:
        try:
            servers.append(slp_ping(host, port, timeout=timeout))
        except ProbeError:
            continue
    return servers
