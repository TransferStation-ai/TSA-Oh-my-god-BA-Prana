"""Blender 后台进程的 JSONL 客户端。

Blender 由外部启动为 ``--background --python blender_body.py``，主进程通过
stdin/stdout 上的 JSON Lines 驱动它。这里只负责进程、请求/响应和日志，
不包含任何 bpy 代码。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO


class BlenderBodyError(RuntimeError):
    """Blender 子进程不可用、请求失败或超时。"""


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


class BlenderBody:
    def __init__(
        self,
        command: Sequence[str],
        cwd: Path,
        *,
        env: Mapping[str, str] | None = None,
        log_path: Path | None = None,
    ) -> None:
        self._command = list(command)
        self._cwd = Path(cwd)
        self._env = dict(env) if env is not None else None
        self._log_path = Path(log_path) if log_path is not None else None
        self._process: subprocess.Popen[str] | None = None
        self._exited = False
        self._lock = threading.RLock()
        self._pending: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
        self._next_id = 1
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        process = self._process
        return process is not None and not self._exited and process.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._exited = False
            if self._log_path is not None:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                self._log_path.write_text("", encoding="utf-8")
            env = None
            if self._env is not None:
                env = {**os.environ, **self._env}
            self._process = subprocess.Popen(
                self._command,
                cwd=str(self._cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=_creation_flags(),
            )
            self._stdout_thread = threading.Thread(target=self._read_stdout, name="blender-stdout", daemon=True)
            self._stderr_thread = threading.Thread(target=self._read_stderr, name="blender-stderr", daemon=True)
            self._stdout_thread.start()
            self._stderr_thread.start()

    def _write_log(self, line: str) -> None:
        if self._log_path is None:
            return
        try:
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                if not line.endswith("\n"):
                    handle.write("\n")
        except OSError:
            pass

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                message = json.loads(stripped)
            except json.JSONDecodeError:
                self._write_log(f"[stdout] {stripped}")
                continue
            if not isinstance(message, dict) or not isinstance(message.get("id"), int):
                self._write_log(f"[stdout] {stripped}")
                continue
            with self._lock:
                pending = self._pending.pop(message["id"], None)
                if pending is not None:
                    event, slot = pending
                    slot.update(message)
                    event.set()
                else:
                    self._write_log(f"[stdout] unmatched response: {stripped}")
        with self._lock:
            self._exited = True
            for event, slot in self._pending.values():
                slot.setdefault("error", "Blender 进程已退出。")
                event.set()
            self._pending.clear()
        self._write_log("[stdout] EOF")

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._write_log(f"[stderr] {line.rstrip()}")

    def request(self, command: str, *, timeout: float = 120.0, **fields: object) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise BlenderBodyError("Blender 尚未启动或已经退出。")
            request_id = self._next_id
            self._next_id += 1
            event = threading.Event()
            slot: dict[str, Any] = {}
            self._pending[request_id] = (event, slot)
            payload = {"id": request_id, "cmd": command, **fields}
            try:
                process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._pending.pop(request_id, None)
                raise BlenderBodyError(f"Blender 请求写入失败：{command}") from exc

        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            raise BlenderBodyError(f"Blender 请求超时：{command}")

        response = slot
        error = response.get("error")
        if error:
            raise BlenderBodyError(str(error))
        if not response.get("ok", False):
            raise BlenderBodyError(f"Blender 请求失败：{command}")
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise BlenderBodyError(f"Blender 响应 result 不是对象：{command}")
        return result

    def exec(self, code: str, *, description: str, timeout: float = 120.0) -> dict[str, Any]:
        return self.request("exec", code=code, description=description, timeout=timeout)

    def render(self, *, view: str, resolution: str, output: Path, timeout: float = 120.0) -> dict[str, Any]:
        return self.request("render", view=view, resolution=resolution, output=str(output), timeout=timeout)

    def save(self, path: Path, timeout: float = 120.0) -> dict[str, Any]:
        return self.request("save", path=str(path), timeout=timeout)

    def quit(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                self.request("quit", timeout=1.0)
            except BlenderBodyError:
                pass
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        with self._lock:
            self._process = None
            self._exited = True
            for event, slot in self._pending.values():
                slot.setdefault("error", "Blender 已关闭。")
                event.set()
            self._pending.clear()

    def log_tail(self, lines: int = 40) -> str:
        if self._log_path is None or not self._log_path.is_file():
            return ""
        try:
            content = self._log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(content[-max(1, lines):])


__all__ = ["BlenderBody", "BlenderBodyError"]
