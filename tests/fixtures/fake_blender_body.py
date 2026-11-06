"""测试用的假 Blender 子进程。"""

import contextlib
import io
import json
import os
import sys
import time


def send(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        request_id = request.get("id")
        command = request.get("cmd")
        if command == "ping":
            send({"id": request_id, "ok": True, "result": {"ok": True, "blender": "fake"}})
        elif command == "exec":
            code = request.get("code", "")
            if code == "noise":
                print("noise", flush=True)
                send({"id": request_id, "ok": True, "result": {"stdout": "", "ok": True}})
                continue
            buffer = io.StringIO()
            try:
                with contextlib.redirect_stdout(buffer):
                    exec(code, {"__name__": "__fake_blender__"})
            except Exception as exc:  # pragma: no cover - diagnostic path
                send({"id": request_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
                continue
            send({"id": request_id, "ok": True, "result": {"stdout": buffer.getvalue(), "ok": True}})
        elif command == "render":
            send({"id": request_id, "ok": True, "result": {"ok": True, "path": request.get("output")}})
        elif command == "save":
            send({"id": request_id, "ok": True, "result": {"ok": True, "path": request.get("path")}})
        elif command == "sleep":
            time.sleep(60)
        elif command == "crash":
            os._exit(1)
        elif command == "quit":
            send({"id": request_id, "ok": True, "result": {"ok": True}})
            return
        else:
            send({"id": request_id, "ok": False, "error": f"unknown command: {command}"})


if __name__ == "__main__":
    main()
