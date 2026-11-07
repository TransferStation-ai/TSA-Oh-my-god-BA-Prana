import socket
import tempfile
import threading
import time
import unittest
from typing import Callable

from skills.game.gm import GmConsole
from skills.game.session import GameSession

Handler = Callable[[list[str]], tuple[list[str], bool]]


class FakeGmServer:
    """单客户端回环 TCP 假服务器：accept 后可先发若干「主动行」，
    随后逐行读命令并按 handler 回复。handler(lines)->(reply_lines, close)。"""

    def __init__(self, handler: Handler, *, proactive: tuple[str, ...] = ()) -> None:
        self._handler = handler
        self._proactive = list(proactive)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.sock.settimeout(5.0)
        self.port = self.sock.getsockname()[1]
        self.received: list[str] = []
        self._conn: socket.socket | None = None
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass
        self._thread.join(timeout=3)

    def _serve(self) -> None:
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        self._conn = conn
        conn.settimeout(0.5)
        for text in self._proactive:
            try:
                conn.sendall((text + "\n").encode("utf-8"))
            except OSError:
                return
        buf = b""
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode("utf-8", "replace")
                    self.received.append(line)
                    reply, close = self._handler(list(self.received))
                    for text in reply:
                        conn.sendall((text + "\n").encode("utf-8"))
                    if close:
                        return
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


def _make_session(events: list[tuple[str, dict[str, object]]], password: str = "") -> GameSession:
    def factory(
        host, port, *, password="", on_chat=None, on_disconnect=None
    ) -> GmConsole:
        return GmConsole(
            host,
            port,
            password=password,
            timeout=2.0,
            quiet=0.1,
            on_chat=on_chat,
            on_disconnect=on_disconnect,
        )

    return GameSession(
        game_settings={"gm_password": password},
        data_dir=tempfile.mkdtemp(prefix="prana-gm-"),
        complete=lambda _msgs, _tools: {"content": "", "tool_calls": []},
        on_event=lambda name, payload: events.append((name, payload)),
        gm_console_factory=factory,
    )


def _ack_handler(lines: list[str]) -> tuple[list[str], bool]:
    return (["PRANA_GM_ACK"], False)


class GmSessionTests(unittest.TestCase):
    def test_gm_join_spawn_and_events(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        server = FakeGmServer(_ack_handler)
        server.start()
        session = _make_session(events)
        try:
            result = session.join("gm", "127.0.0.1", server.port, player_name="普拉娜")
            self.assertEqual(result["game"], "gm")
            self.assertEqual(result["host"], "127.0.0.1")
            self.assertEqual(result["port"], server.port)
            self.assertEqual(result["player_name"], "普拉娜")
            self.assertEqual(result["npc"], "已生成")
            self.assertEqual(session.active_game, "gm")
            self.assertTrue(any(name == "joined" for name, _ in events))
            self.assertTrue(any("lua_run" in line for line in server.received))
        finally:
            session.close()
            server.stop()

    def test_gm_command_say(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        server = FakeGmServer(_ack_handler)
        server.start()
        session = _make_session(events)
        try:
            session.join("gm", "127.0.0.1", server.port)
            out = session.command("gm", "say", text="hi")
            self.assertTrue(out["ok"])
            self.assertTrue(out["feedback"])
            self.assertTrue(
                any("PlayerSay" in line and "lua_run" in line for line in server.received),
                server.received,
            )
        finally:
            session.close()
            server.stop()

    def test_gm_chat_echo_forwarded(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        server = FakeGmServer(
            _ack_handler, proactive=("PRANA_GM_CHAT Alice | hello",)
        )
        server.start()
        session = _make_session(events)
        try:
            session.join("gm", "127.0.0.1", server.port)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not any(
                name == "chat" for name, _ in events
            ):
                time.sleep(0.02)
            chat_events = [payload for name, payload in events if name == "chat"]
            self.assertTrue(chat_events, events)
            self.assertEqual(chat_events[0]["message"], "Alice | hello")
            self.assertEqual(chat_events[0]["game"], "gm")
        finally:
            session.close()
            server.stop()

    def test_gm_no_feedback_downgrade(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []

        def handler(lines: list[str]) -> tuple[list[str], bool]:
            return (["Unknown command: xxx"], False)

        server = FakeGmServer(handler)
        server.start()
        session = _make_session(events)
        try:
            session.join("gm", "127.0.0.1", server.port)
            out = session.command("gm", "say", text="hi")
            self.assertTrue(out["ok"])
            self.assertFalse(out["feedback"])
            self.assertEqual(out["message"], "控制台未回传确认，指令已发送。")
        finally:
            session.close()
            server.stop()

    def test_gm_disconnect_cleans(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []

        def handler(lines: list[str]) -> tuple[list[str], bool]:
            return (["PRANA_GM_ACK"], True)

        server = FakeGmServer(handler)
        server.start()
        session = _make_session(events)
        try:
            session.join("gm", "127.0.0.1", server.port)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and session.active_game == "gm":
                time.sleep(0.02)
            self.assertIsNone(session.active_game)
            errors = [payload for name, payload in events if name == "error"]
            self.assertTrue(any("GMod" in str(p.get("message", "")) for p in errors), events)
        finally:
            session.close()
            server.stop()

    def test_mc_gm_mutual_exclusion(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        quit_calls: list[object] = []

        class FakeBody:
            def __init__(self, command, cwd, on_event=None) -> None:
                self.on_event = on_event

            def start(self) -> None:
                pass

            def join(self, **kwargs: object) -> dict[str, object]:
                return {"players": [], "version": "1.21"}

            def say(self, _text: str) -> None:
                pass

            def quit(self) -> None:
                quit_calls.append(1)

            def status(self) -> dict[str, object]:
                return {"game": "mc"}

        def gm_factory(host, port, *, password="", on_chat=None, on_disconnect=None):
            return GmConsole(
                host,
                port,
                password=password,
                timeout=2.0,
                quiet=0.1,
                on_chat=on_chat,
                on_disconnect=on_disconnect,
            )

        server = FakeGmServer(_ack_handler)
        server.start()
        session = GameSession(
            game_settings={},
            data_dir=tempfile.mkdtemp(prefix="prana-gm-"),
            complete=lambda _m, _t: {"content": "", "tool_calls": []},
            on_event=lambda name, payload: events.append((name, payload)),
            body_factory=FakeBody,
            body_command=["echo", "dummy"],
            gm_console_factory=gm_factory,
        )
        try:
            session.join("mc", "127.0.0.1", 25565, player_name="Prana")
            self.assertEqual(session.active_game, "mc")
            session.join("gm", "127.0.0.1", server.port)
            self.assertEqual(session.active_game, "gm")
            self.assertEqual(quit_calls, [1])
        finally:
            session.close()
            server.stop()


if __name__ == "__main__":
    unittest.main()
