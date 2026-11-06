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


def gm_join_script(player_model: str = "", npc_name: str = "普拉娜") -> str:
    """单行 Lua：生成/复用 NPC，entindex 存于全局 PRUNA_NPC；

    首调 spawn ``npc_gunman``（给定模型则 SetModel），SetName(npc_name)；
    注入 ``ChatPlayer`` 钩子把游戏内聊天以 ``PRANA_GM_CHAT`` 前缀回传；
    末行打印 ``PRANA_GM_ACK``。重复 join 不重复 spawn（复用已存 entindex）。
    """
    model_json = json.dumps(player_model, ensure_ascii=False)
    name_json = json.dumps(npc_name, ensure_ascii=False)
    return (
        'if not(PRUNA_NPC and IsValid(Entity(PRUNA_NPC))) then '
        'local npc=ents.Create("npc_gunman"); '
        'if IsValid(LocalPlayer()) then '
        "npc:SetPos(LocalPlayer():GetPos()+Vector(0,0,64)) end; "
        "npc:Spawn(); "
        "npc:SetName(" + name_json + "); "
        "if " + model_json + '~="" then npc:SetModel(' + model_json + ") end; "
        "PRUNA_NPC=npc:EntIndex() end; "
        'hook.Add("ChatPlayer","PranaGameHook",function(player,text) '
        'print("PRANA_GM_CHAT "..player:Nick().." | "..text) end); '
        'print("PRANA_GM_ACK")'
    )


def gm_say_script(text: str) -> str:
    """让 NPC 名义发言：``PlayerSay`` 单参广播（签名待任务 7 校准）；
    ``text`` 以 JSON 字符串字面量嵌入，避免引号注入；末行 ACK。"""
    text_json = json.dumps(text, ensure_ascii=False)
    return "PlayerSay(" + text_json + '); print("PRANA_GM_ACK")'


def gm_follow_script(target: str) -> str:
    """注入/更新 ``Think`` 钩子（每 30 帧一跳）：NPC 朝目标玩家水平移动；
    距离 > 2 单位才移动，≤2 单位停；目标为玩家 Nick 或 "老师"（本地玩家）；
    重复调用重设目标（全局 PRUNA_FOLLOW_TARGET）；末行 ACK。"""
    target_json = json.dumps(target, ensure_ascii=False)
    return (
        "PRUNA_FOLLOW_TARGET=" + target_json + "; "
        "PRUNA_FOLLOW_TICK=0; "
        "function PranaFollowThink() "
        "PRUNA_FOLLOW_TICK=(PRUNA_FOLLOW_TICK or 0)+1; "
        "if PRUNA_FOLLOW_TICK%30~=0 then return end; "
        "local npc=Entity(PRUNA_NPC); if not IsValid(npc) then return end; "
        "local goal; "
        'if PRUNA_FOLLOW_TARGET=="老师" then goal=LocalPlayer() '
        "else goal=nil; "
        "for _,p in ipairs(player.GetAll()) do "
        "if p:Nick()==PRUNA_FOLLOW_TARGET then goal=p; break end end end; "
        "if not IsValid(goal) then return end; "
        "local d=goal:GetPos()-npc:GetPos(); "
        "local len=math.sqrt(d.x*d.x+d.y*d.y); "
        "if len<=2.0 then return end; "
        "npc:SetVelocity(Vector(d.x/len*300,d.y/len*300,0)); "
        "npc:SetAngles(Angle(0,math.deg(math.atan2(d.y,d.x)),0)) end; "
        'hook.Add("Think","PranaGameFollow",PranaFollowThink); '
        'print("PRANA_GM_ACK")'
    )


def gm_stop_script() -> str:
    """取消移动 ``Think`` 钩子，NPC 原地停留；末行 ACK。"""
    return (
        'hook.Remove("Think","PranaGameFollow"); '
        "PRUNA_FOLLOW_TICK=0; "
        "local npc=Entity(PRUNA_NPC); if IsValid(npc) then "
        "npc:SetVelocity(Vector(0,0,0)) end; "
        'print("PRANA_GM_ACK")'
    )


def gm_scan_script(max_players: int = 8) -> str:
    """收集 NPC 位置 + 至多 ``max_players`` 名玩家（Nick/HP/SteamID），
    ``print("PRANA_GM_JSON " .. json.encode(out))`` 回传解析；末行 ACK。"""
    bound = int(max_players)
    return (
        "local out={players={}}; "
        "local npc=Entity(PRUNA_NPC); "
        "if IsValid(npc) then local p=npc:GetPos(); "
        "out.npc={x=p.x,y=p.y,z=p.z} end; "
        "local all=player.GetAll(); "
        "for i=1,math.min(#all," + str(bound) + ") do local pl=all[i]; "
        "out.players[i]={nick=pl:Nick(),hp=pl:Health(),"
        'steamid=tostring(pl:SteamID())} end; '
        'print("PRANA_GM_JSON "..json.encode(out)); '
        'print("PRANA_GM_ACK")'
    )


def gm_leave_script() -> str:
    """移除 ``ChatPlayer``/``Think`` 钩子，移除 NPC 实体，清全局变量；末行 ACK。"""
    return (
        'hook.Remove("ChatPlayer","PranaGameHook"); '
        'hook.Remove("Think","PranaGameFollow"); '
        "if PRUNA_NPC and IsValid(Entity(PRUNA_NPC)) then "
        "Entity(PRUNA_NPC):Remove() end; "
        "PRUNA_NPC=nil; PRUNA_FOLLOW_TARGET=nil; PRUNA_FOLLOW_TICK=0; "
        'print("PRANA_GM_ACK")'
    )


def run_lua_snippet(console: GmConsole, script: str) -> dict[str, object]:
    """把单行 Lua 片段经 ``lua_run`` 注入：``console.run("lua_run " + script)``。

    ``script`` 不得包含换行（``lua_run`` 单行约束），违反则抛 ``ValueError``。
    """
    if not isinstance(script, str):
        raise ValueError("lua_run 片段必须是字符串。")
    if "\n" in script:
        raise ValueError("lua_run 片段必须为单行（不能包含换行）。")
    return console.run("lua_run " + script)


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
    "gm_follow_script",
    "gm_join_script",
    "gm_leave_script",
    "gm_say_script",
    "gm_scan_script",
    "gm_stop_script",
    "run_lua_snippet",
]
