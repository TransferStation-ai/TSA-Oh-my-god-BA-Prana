from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from typing import Callable

from PIL import Image

from skills.game.probe import detect_local_mc

BODY_VERSION = "2"
MC_BODY_DIRNAME = "mc_body"
JOIN_TIMEOUT_SECONDS = 60.0
COMMAND_TIMEOUT_SECONDS = 15.0
NPM_INSTALL_TIMEOUT_SECONDS = 600.0
MAX_CHAT_LENGTH = 250
SKIN_SIZE = {(64, 32), (64, 64)}


class McBodyError(RuntimeError):
    pass


def app_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Prana"
    return Path.home() / ".config" / "prana"


def find_node() -> str:
    node = shutil.which("node")
    if node is None:
        raise McBodyError("本机没有 Node.js，无法运行 MC 本体。")
    return node


def find_npm() -> str:
    npm = shutil.which("npm")
    if npm is None:
        raise McBodyError("本机没有 npm，无法安装 MC 本体依赖。")
    return npm


def body_source_dir() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / "prana_mc_body"


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def ensure_body_ready(
    dest: Path,
    *,
    source: Path,
    npm: str,
    runner: Callable[..., object] | None = None,
) -> Path:
    dest = Path(dest)
    source = Path(source)
    marker = dest / ".version"
    if not source.is_dir():
        raise McBodyError("MC 本体源文件不存在。")
    if not dest.is_dir() or not marker.is_file() or marker.read_text(
        encoding="utf-8"
    ).strip() != BODY_VERSION:
        try:
            shutil.copytree(
                source,
                dest,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("node_modules", ".version"),
            )
            marker.write_text(BODY_VERSION, encoding="utf-8")
        except OSError as error:
            raise McBodyError(f"无法准备 MC 本体文件：{error}") from error
    if not (dest / "node_modules" / "mineflayer").is_dir():
        if runner is None:
            runner = subprocess.run
        try:
            result = runner(
                [npm, "ci", "--omit=dev"],
                cwd=str(dest),
                timeout=NPM_INSTALL_TIMEOUT_SECONDS,
                check=False,
                creationflags=_creation_flags(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise McBodyError(f"MC 本体依赖安装失败：{error}") from error
        if getattr(result, "returncode", 1) != 0:
            stderr = getattr(result, "stderr", "")
            detail = str(stderr).strip()
            suffix = f"：{detail[-500:]}" if detail else ""
            raise McBodyError(f"MC 本体依赖安装失败{suffix}")
    return dest


def prepare_skin(
    game_settings: dict[str, str], assets_dir: Path
) -> tuple[Path | None, str]:
    source_text = game_settings.get("mc_skin_path", "").strip()
    source = (
        Path(source_text)
        if source_text
        else Path.home() / "Downloads" / "普拉娜.png"
    )
    destination = Path(assets_dir) / "普拉娜.png"
    candidate = (
        source
        if source.is_file()
        else destination
        if destination.is_file()
        else None
    )
    if candidate is None:
        return None, "皮肤文件缺失，将使用默认皮肤"
    try:
        with Image.open(candidate) as image:
            if image.format != "PNG" or image.size not in SKIN_SIZE:
                return None, "皮肤格式无效，将使用默认皮肤"
            image.verify()
        if candidate != destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(candidate, destination)
    except (OSError, ValueError):
        return None, "皮肤格式无效，将使用默认皮肤"
    return destination, "皮肤文件已准备（vanilla 离线服不会显示自定义皮肤）"


class McBody:
    def __init__(
        self,
        command: list[str],
        cwd: Path,
        *,
        on_event: Callable[[dict[str, object]], None] | None = None,
        env: dict[str, str] | None = None,
        popen: Callable[..., subprocess.Popen[str]] | None = None,
    ) -> None:
        self.command = list(command)
        self.cwd = Path(cwd)
        self.on_event = on_event
        self.env = env
        self._popen = popen
        self._process: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._pending: dict[int, tuple[threading.Event, dict[str, object]]] = {}
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._stderr = ""

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        if self.running:
            return
        popen = self._popen or subprocess.Popen
        try:
            self._process = popen(
                self.command,
                cwd=str(self.cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=self.env,
                creationflags=_creation_flags(),
            )
        except OSError as error:
            raise McBodyError(f"无法启动 MC 本体：{error}") from error
        threading.Thread(
            target=self._read_stdout, name="PranaMcBodyStdout", daemon=True
        ).start()
        threading.Thread(
            target=self._read_stderr, name="PranaMcBodyStderr", daemon=True
        ).start()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._notify({"event": "error", "data": {"message": "MC 本体协议响应无效。"}})
                continue
            if not isinstance(message, dict):
                continue
            event_name = message.get("event")
            if isinstance(event_name, str):
                self._notify(message)
                continue
            request_id = message.get("id")
            if isinstance(request_id, int):
                with self._lock:
                    pending = self._pending.get(request_id)
                    if pending is not None:
                        pending[1].update(message)
                        pending[0].set()
        with self._lock:
            for event, response in self._pending.values():
                response.setdefault("error", "MC 本体进程已退出。")
                event.set()

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr = (self._stderr + line)[-4000:]

    def _notify(self, event: dict[str, object]) -> None:
        if self.on_event is None:
            return
        self.on_event(event)

    def _request(
        self, command: str, *, timeout: float | None = None, **fields: object
    ) -> dict[str, object]:
        timeout = COMMAND_TIMEOUT_SECONDS if timeout is None else timeout
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise McBodyError("MC 本体进程已退出。")
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            event = threading.Event()
            response: dict[str, object] = {}
            self._pending[request_id] = (event, response)
        try:
            payload = json.dumps(
                {"id": request_id, "cmd": command, **fields}, ensure_ascii=False
            )
            with self._write_lock:
                process.stdin.write(payload + "\n")
                process.stdin.flush()
        except (OSError, BrokenPipeError) as error:
            with self._lock:
                self._pending.pop(request_id, None)
            raise McBodyError(f"无法向 MC 本体发送指令：{error}") from error
        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            raise McBodyError(f"MC 本体响应超时（{command}）。")
        with self._lock:
            self._pending.pop(request_id, None)
        if "error" in response:
            raise McBodyError(str(response["error"]))
        if not response.get("ok"):
            raise McBodyError("MC 本体返回了无效响应。")
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise McBodyError("MC 本体返回了无效结果。")
        return result

    def join(
        self,
        *,
        host: str,
        port: int,
        username: str,
        skin_path: Path | None = None,
        version: str | None = None,
        skin_command: str = "",
    ) -> dict[str, object]:
        return self._request(
            "join",
            timeout=JOIN_TIMEOUT_SECONDS,
            host=host,
            port=port,
            username=username,
            skinPath=str(skin_path) if skin_path else "",
            version=version or "",
            skinCommand=skin_command,
        )

    def say(self, text: str) -> None:
        self._request("say", text=text[:MAX_CHAT_LENGTH])

    def msg(self, target: str, text: str) -> None:
        self._request(
            "msg", target=target, text=text[:MAX_CHAT_LENGTH]
        )

    def follow(self, target: str) -> None:
        self._request("follow", target=target)

    def stop_follow(self) -> None:
        self._request("stop_follow")

    def mine(self, block: str) -> dict[str, object]:
        if not isinstance(block, str) or not block.strip():
            raise ValueError("方块名称不能为空。")
        return self._request("mine", timeout=60.0, block=block)

    def inventory(self) -> list[dict[str, object]]:
        result = self._request("inventory")
        items = result.get("items")
        if not isinstance(items, list) or not all(
            isinstance(item, dict) for item in items
        ):
            raise McBodyError("MC 本体返回了无效的背包数据。")
        return items

    def equip(self, item: str) -> None:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("物品名称不能为空。")
        self._request("equip", item=item)

    def move_to(self, x: float, y: float, z: float) -> dict[str, object]:
        return self._request(
            "move_to", timeout=75.0, x=x, y=y, z=z
        )

    def eat(self, item: str) -> None:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("食物名称不能为空。")
        self._request("eat", timeout=30.0, item=item)

    def interact(self, target: str, kind: str) -> None:
        if not isinstance(target, str) or not target.strip():
            raise ValueError("交互目标不能为空。")
        if kind not in {"block", "entity"}:
            raise ValueError("交互目标类型无效。")
        self._request("interact", target=target, kind=kind)

    def place(self, item: str, x: int, y: int, z: int) -> None:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("方块名称不能为空。")
        self._request("place", item=item, x=x, y=y, z=z)

    def attack(self, target: str) -> None:
        if not isinstance(target, str) or not target.strip():
            raise ValueError("攻击目标不能为空。")
        self._request("attack", target=target)

    def status(self) -> dict[str, object]:
        return self._request("status")

    def quit(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is not None:
            self._process = None
            return
        try:
            self._request("quit", timeout=1.0)
        except (McBodyError, OSError):
            pass
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        self._process = None


__all__ = [
    "McBody",
    "McBodyError",
    "app_data_dir",
    "body_source_dir",
    "detect_local_mc",
    "ensure_body_ready",
    "find_node",
    "find_npm",
    "prepare_skin",
]
