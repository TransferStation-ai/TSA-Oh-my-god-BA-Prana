"""Blender 后台命令循环。

由 ``BlenderBody`` 启动：
``blender -b --factory-startup --python blender_body.py``
逐行读取 stdin 的 JSON 请求，向 stdout 写一行 JSON 响应。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
import prana_api  # noqa: E402


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
        try:
            if command == "ping":
                send({
                    "id": request_id,
                    "ok": True,
                    "result": {"ok": True, "blender": bpy.app.version_string},
                })
            elif command == "exec":
                code = str(request.get("code", ""))
                buffer = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buffer):
                        exec(code, {"prana_api": prana_api, "bpy": bpy, "__name__": "__main__"})
                except Exception as exc:
                    send({
                        "id": request_id,
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc()[-4000:],
                    })
                    continue
                send({
                    "id": request_id,
                    "ok": True,
                    "result": {"ok": True, "stdout": buffer.getvalue()},
                })
            elif command == "render":
                result = prana_api.render_preview(
                    str(request.get("view", "three_quarter")),
                    str(request.get("resolution", "900x1200")),
                    str(request.get("output", "preview.png")),
                )
                send({"id": request_id, "ok": True, "result": result})
            elif command == "save":
                result = prana_api.save_blend(str(request.get("path", "work.blend")))
                send({"id": request_id, "ok": True, "result": result})
            elif command == "quit":
                send({"id": request_id, "ok": True, "result": {"ok": True}})
                return
            else:
                send({"id": request_id, "ok": False, "error": f"未知命令：{command}"})
        except Exception as exc:
            send({
                "id": request_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-4000:],
            })


if __name__ == "__main__":
    main()
