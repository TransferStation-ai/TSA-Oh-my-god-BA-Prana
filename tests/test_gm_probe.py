import socket
import struct
import threading
import unittest
from typing import Any

from skills.game.gm import GmProbeError, a2s_info, detect_local_gm


def make_response(name: str = "My GMod", map_name: str = "gm_bigcity", players: int = 1, max_players: int = 8) -> bytes:
    def s(value: str) -> bytes:
        return value.encode("utf-8") + b"\x00"

    # 基础 0x64 报文：engine_version 后接双引擎版本（Steam3 A2S 风格，
    # 与真实 Source 引擎响应一致），随后 name/map/folder/game/players/max_players。
    body = (
        struct.pack("<H", 15)
        + b"\x05\x05"
        + s(name)
        + s(map_name)
        + s("garrysmod")
        + s("999")
        + struct.pack("<HH", players, max_players)
    )
    return b"\x64" + struct.pack(">H", len(body)) + body


class FakeUdpServer:
    """回环 UDP 假服务器：收到 A2S 查询后向真实发送地址回构造响应。"""

    def __init__(self, response: bytes, *, replies: int = 3) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(5.0)
        self.port = self.sock.getsockname()[1]
        self.response = response
        self.replies_left = replies
        self.queries: list[bytes] = []
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "FakeUdpServer":
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.sock.close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _serve(self) -> None:
        while self.replies_left > 0:
            try:
                data, sender = self.sock.recvfrom(4096)
            except OSError:
                return
            self.queries.append(bytes(data))
            if data.startswith(b"\xff\xff\xff\xff"):
                self.sock.sendto(self.response, sender)
                self.replies_left -= 1


class GmProbeTests(unittest.TestCase):
    def test_parses_a2s_response(self) -> None:
        with FakeUdpServer(make_response()) as fake:
            info = a2s_info("127.0.0.1", fake.port, timeout=2.0)
        self.assertEqual(info["name"], "My GMod")
        self.assertEqual(info["map"], "gm_bigcity")
        self.assertEqual(info["players"], 1)
        self.assertEqual(info["max_players"], 8)
        self.assertEqual(info["game"], "garrysmod")
        self.assertEqual(info["host"], "127.0.0.1")
        self.assertEqual(info["port"], fake.port)
        self.assertTrue(fake.queries[0].startswith(b"\xff\xff\xff\xffA2S_INFO"))

    def test_retries_once_on_missing_first_response(self) -> None:
        # 只应答一次：实现应重发查询后拿到结果（共 2 次查询）。
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server_sock.bind(("127.0.0.1", 0))
        server_sock.settimeout(5.0)
        port = server_sock.getsockname()[1]
        received: list[bytes] = []
        answered = {"done": False}

        def serve() -> None:
            while not answered["done"]:
                try:
                    data, sender = server_sock.recvfrom(4096)
                except OSError:
                    return
                received.append(bytes(data))
                if len(received) == 2:
                    server_sock.sendto(make_response(), sender)
                    answered["done"] = True

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            info = a2s_info("127.0.0.1", port, timeout=2.0)
        finally:
            server_sock.close()
            thread.join(timeout=5)
        self.assertEqual(info["name"], "My GMod")
        self.assertGreaterEqual(len(received), 2)

    def test_bad_response_type_raises(self) -> None:
        with FakeUdpServer(b"\x12\x00\x00") as fake:
            with self.assertRaises(GmProbeError):
                a2s_info("127.0.0.1", fake.port, timeout=2.0)

    def test_dead_port_raises(self) -> None:
        with self.assertRaises(GmProbeError):
            a2s_info("127.0.0.1", 1, timeout=0.3)  # 端口 1 无服务

    def test_detect_skips_failures(self) -> None:
        with FakeUdpServer(make_response()) as fake:
            servers = detect_local_gm((fake.port, 65500), timeout=0.5)
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["port"], fake.port)


if __name__ == "__main__":
    unittest.main()
