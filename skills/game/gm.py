from __future__ import annotations

import json
import socket
import struct
import threading
import time

GM_PROBE_HOST = "127.0.0.1"
GM_PROBE_PORTS = (27015,)
GM_PROBE_TIMEOUT = 2.0
GM_DEFAULT_PORT = 27015

GM_CONNECT_TIMEOUT = 5.0
GM_QUIET_WINDOW = 0.8
GM_CMD_TIMEOUT = 10.0

_GM_ACK_MARKER = "PRANA_GM_ACK"
_GM_JSON_MARKER = "PRANA_GM_JSON"
_GM_CHAT_MARKER = "PRANA_GM_CHAT"

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


class GmConsoleError(RuntimeError):
    pass


class GmConsole:
    """GMod 服务端控制台（TCP 行协议）客户端。

    常驻一条 TCP 连接 + 后台读线程，把服务器输出逐行追加到内部缓冲。
    `run(command)` 写一行命令（以 \n 结尾），等到「行数不再增长」的静默
    窗口（受 timeout 约束）后，取增量行并汇总为
    {"ok", "feedback", "lines", "json", "chat"}。
    """

    def __init__(
        self,
        host: str,
        port: int = GM_DEFAULT_PORT,
        *,
        password: str = "",
        timeout: float = GM_CONNECT_TIMEOUT,
        quiet: float = GM_QUIET_WINDOW,
        sock_factory=None,
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.quiet = quiet
        self._sock_factory = sock_factory or socket.socket
        self._sock: socket.socket | None = None
        self._reader_file: object = None
        self._reader: threading.Thread | None = None
        self._lines: list[str] = []
        self._lines_lock = threading.Lock()
        self._closed = False
        self._closed_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._password_sent = False

    @property
    def closed(self) -> bool:
        with self._closed_lock:
            return self._closed

    def connect(self) -> None:
        if self._sock is not None:
            return
        try:
            sock = self._sock_factory(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))
            sock.settimeout(None)
        except OSError as error:
            raise GmConsoleError(
                f"无法连接 GMod 控制台 {self.host}:{self.port}：{error}"
            ) from error
        self._sock = sock
        self._reader_file = sock.makefile("rb")
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        try:
            reader = self._reader_file
            if reader is None:
                return
            while not self.closed:
                data = reader.readline()
                if not data:
                    break
                line = data.decode("utf-8", "replace").rstrip("\r\n")
                with self._lines_lock:
                    self._lines.append(line)
        except (OSError, ValueError):
            pass
        finally:
            with self._closed_lock:
                self._closed = True

    def _send_line(self, line: str) -> None:
        if self._sock is None:
            raise GmConsoleError("控制台未连接。")
        with self._write_lock:
            try:
                self._sock.sendall((line + "\n").encode("utf-8"))
            except OSError as error:
                with self._closed_lock:
                    self._closed = True
                raise GmConsoleError(f"无法写入 GMod 控制台：{error}") from error

    def _wait_quiet(self, start_count: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        last_count = start_count
        last_change = time.monotonic()
        saw_new = False
        while True:
            now = time.monotonic()
            with self._lines_lock:
                count = len(self._lines)
            if count != last_count:
                last_count = count
                last_change = now
                saw_new = True
            if saw_new and now - last_change >= self.quiet:
                break
            if now >= deadline:
                break
            time.sleep(0.03)

    def run(
        self, command: str, *, timeout: float = GM_CMD_TIMEOUT
    ) -> dict[str, object]:
        if self.closed:
            raise GmConsoleError("控制台连接已断开。")
        with self._lines_lock:
            start = len(self._lines)
        if self.password and not self._password_sent:
            self._send_line(self.password)
            self._password_sent = True
            self._wait_quiet(start, timeout)
            with self._lines_lock:
                start = len(self._lines)
        self._send_line(command)
        self._wait_quiet(start, timeout)
        with self._lines_lock:
            new_lines = list(self._lines[start:])
        return self._summarize(new_lines)

    @staticmethod
    def _summarize(lines: list[str]) -> dict[str, object]:
        feedback = False
        parsed_json: dict[str, object] | None = None
        chat: list[str] = []
        for line in lines:
            if _GM_ACK_MARKER in line:
                feedback = True
            if line.startswith(_GM_JSON_MARKER):
                feedback = True
                payload = line[len(_GM_JSON_MARKER):].strip()
                try:
                    parsed_json = json.loads(payload)
                except (json.JSONDecodeError, ValueError):
                    parsed_json = None
            if line.startswith(_GM_CHAT_MARKER):
                chat.append(line[len(_GM_CHAT_MARKER):].lstrip())
        return {
            "ok": True,
            "feedback": feedback,
            "lines": list(lines),
            "json": parsed_json,
            "chat": chat,
        }

    def close(self) -> None:
        if self._sock is not None:
            with self._write_lock:
                try:
                    self._sock.close()
                except OSError:
                    pass
            self._sock = None
            if self._reader_file is not None:
                try:
                    self._reader_file.close()
                except (OSError, ValueError):
                    pass
                self._reader_file = None
        with self._closed_lock:
            self._closed = True


__all__ = [
    "GM_CMD_TIMEOUT",
    "GM_CONNECT_TIMEOUT",
    "GM_DEFAULT_PORT",
    "GM_PROBE_HOST",
    "GM_PROBE_PORTS",
    "GM_PROBE_TIMEOUT",
    "GM_QUIET_WINDOW",
    "GmConsole",
    "GmConsoleError",
    "GmProbeError",
    "a2s_info",
    "detect_local_gm",
]
