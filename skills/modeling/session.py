"""建模作业生命周期。

一个 ModelingSession 对应一个聊天会话，最多有一个活跃的 Blender 作业。
Blender 以独立子进程运行；主进程只通过 JSONL 命令驱动它。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .body import BlenderBody
from .gmod import (
    build_body_qc,
    build_hands_qc,
    build_vmt,
    package_addon,
    validate_compiled_model,
    write_vtf,
)
from .settings import (
    ModelingToolError,
    resolve_blender,
    resolve_blockbench,
    resolve_gmod,
    resolve_sfm,
)
from .source import load_hands_skeleton, load_player_skeleton
from .ysm import parse_ysm


class ModelingSessionError(RuntimeError):
    """建模作业状态或输入无效。"""


_VIEWS = frozenset({"front", "back", "left", "right", "three_quarter"})
_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_RESOLUTION_RE = re.compile(r"^(\d{2,4})x(\d{2,4})$")
_MAX_CODE = 100_000


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if callable(value):
        return None
    if hasattr(value, "__dict__"):
        return {
            str(key): _jsonable(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return str(value)


class ModelingSession:
    def __init__(
        self,
        session_id: str,
        settings: Mapping[str, str],
        data_dir: Path,
        *,
        body_factory: Callable[..., BlenderBody] | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ):
        self.session_id = session_id
        self.settings = dict(settings or {})
        self.data_dir = Path(data_dir)
        self.body_factory = body_factory
        self.on_event = on_event
        self._lock = threading.RLock()
        self._body: Any = None
        self._state = "idle"
        self._job_id = ""
        self._job_dir: Path | None = None
        self._source_models_dir = ""
        self._output_name = ""
        self._brief = ""
        self._preview: str | None = None
        self._script_index = 0
        self._render_index = 0
        self._last_error = ""
        self._bones = 0
        self._cubes = 0
        self._texture = [0, 0]
        self._scale = 0.0
        self._hands_ready = False

    # ---- 内部工具 -----------------------------------------------------

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(kind, payload)
        except Exception:
            pass

    def _require_job(self) -> Path:
        if self._job_dir is None or self._state != "modeling":
            raise ModelingSessionError("当前没有进行中的建模作业。")
        return self._job_dir

    def _job_file(self, *parts: str) -> Path:
        job_dir = self._require_job()
        return job_dir.joinpath(*parts)

    def _make_body(self, command: list[str], job_dir: Path) -> Any:
        if self.body_factory is not None:
            return self.body_factory(command, cwd=job_dir, log_path=job_dir / "logs" / "blender.log")
        return BlenderBody(command, cwd=job_dir, log_path=job_dir / "logs" / "blender.log")

    def _ensure_body(self) -> Any:
        if self._body is None:
            raise ModelingSessionError("Blender 进程尚未启动。")
        if getattr(self._body, "running", False):
            return self._body
        starter = getattr(self._body, "start", None)
        if starter is None:
            raise ModelingSessionError("Blender 进程已退出，且无法重启。")
        starter()
        work_blend = self._job_file("work.blend")
        if work_blend.is_file():
            self._body.exec(
                f"import bpy\nbpy.ops.wm.open_mainfile(filepath={str(work_blend)!r})\n",
                description="恢复 work.blend",
                timeout=120.0,
            )
        return self._body

    @staticmethod
    def _skeleton_summary(skeleton: Any) -> dict[str, Any]:
        return {
            "version": int(getattr(skeleton, "version", 0) or 0),
            "bones": len(getattr(skeleton, "bones", ()) or ()),
            "includes": [str(item) for item in (getattr(skeleton, "includes", ()) or ())],
        }

    @staticmethod
    def _model_summary(model: Any) -> dict[str, Any]:
        return {
            "identifier": getattr(model, "identifier", ""),
            "bones": len(getattr(model, "bones", ()) or ()),
            "cubes": len(getattr(model, "cubes", ()) or ()),
            "texture_width": int(getattr(model, "texture_width", 0) or 0),
            "texture_height": int(getattr(model, "texture_height", 0) or 0),
            "height": float(getattr(model, "height", 0.0) or 0.0),
            "hands": _jsonable(getattr(model, "hands", ())),
            "warnings": [str(item) for item in (getattr(model, "warnings", ()) or ())],
        }

    # ---- 公开接口 -----------------------------------------------------

    def start(self, source_models_dir: str, output_name: str, brief: str = "") -> dict[str, Any]:
        with self._lock:
            if self._state == "modeling":
                raise ModelingSessionError("当前会话已有进行中的建模作业，请先 stop。")
            if not _NAME_RE.fullmatch(output_name):
                raise ModelingSessionError("output_name 只能包含小写字母、数字和下划线。")
            source = Path(source_models_dir).expanduser()
            if not source.is_dir():
                raise ModelingSessionError(f"参考模型目录不存在：{source}")

            try:
                model = parse_ysm(source)
                blender = resolve_blender(self.settings)
                gmod = resolve_gmod(self.settings)
                body_skeleton = load_player_skeleton(gmod)
                hands_skeleton = load_hands_skeleton(gmod)
            except (ModelingToolError, ValueError, OSError) as exc:
                raise ModelingSessionError(str(exc)) from exc

            scale = model.scale_to_units(72.0)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            job_id = f"{self.session_id}-{stamp}"
            job_dir = self.data_dir / "modeling" / job_id
            if job_dir.exists():
                job_id = f"{job_id}-{datetime.now().strftime('%f')[:3]}"
                job_dir = self.data_dir / "modeling" / job_id
            for name in ("renders", "scripts", "export", "addon", "logs"):
                (job_dir / name).mkdir(parents=True, exist_ok=True)

            warnings = list(getattr(model, "warnings", ()) or ())
            source_payload = {
                "session_id": self.session_id,
                "job_id": job_id,
                "source_models_dir": str(source),
                "output_name": output_name,
                "brief": brief,
                "model": self._model_summary(model),
                "body_skeleton": self._skeleton_summary(body_skeleton),
                "hands_skeleton": self._skeleton_summary(hands_skeleton),
                "scale": scale,
                "warnings": warnings,
            }
            (job_dir / "source.json").write_text(
                json.dumps(source_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            script = Path(__file__).with_name("blender_body.py")
            command = [str(blender), "-b", "--factory-startup", "--python", str(script)]
            try:
                body = self._make_body(command, job_dir)
                starter = getattr(body, "start", None)
                if starter is not None:
                    starter()
            except Exception as exc:
                shutil.rmtree(job_dir, ignore_errors=True)
                raise ModelingSessionError(f"Blender 启动失败：{exc}") from exc

            self._body = body
            self._state = "modeling"
            self._job_id = job_id
            self._job_dir = job_dir
            self._source_models_dir = str(source)
            self._output_name = output_name
            self._brief = brief
            self._bones = len(getattr(body_skeleton, "bones", ()) or ())
            self._cubes = len(getattr(model, "cubes", ()) or ())
            self._texture = [model.texture_width, model.texture_height]
            self._scale = scale
            self._hands_ready = bool(getattr(hands_skeleton, "bones", ()) or ())
            self._last_error = ""

            model_payload = {
                "bones": _jsonable(getattr(model, "bones", ())),
                "cubes": _jsonable(getattr(model, "cubes", ())),
                "texture_width": model.texture_width,
                "texture_height": model.texture_height,
                "height": model.height,
            }
            texture_path = source.parent / "textures" / "default.png"
            import_code = (
                "import json\n"
                "import prana_api\n"
                "prana_api.reset_scene()\n"
                f"payload = json.loads({json.dumps(model_payload, ensure_ascii=False)!r})\n"
                f"prana_api.import_ysm_reference(payload, {str(texture_path)!r}, {scale!r})\n"
            )
            try:
                body.exec(import_code, description="导入 YSM 参考模型", timeout=120.0)
            except Exception as exc:
                warnings.append(f"参考模型导入失败：{exc}")

            try:
                body.save(str(job_dir / "work.blend"), timeout=120.0)
            except Exception as exc:
                warnings.append(f"初始 work.blend 保存失败：{exc}")

            preview: str | None = None
            try:
                preview_path = job_dir / "renders" / "start_front.png"
                body.render(view="front", resolution="900x1200", output=str(preview_path), timeout=120.0)
                preview = str(preview_path)
            except Exception as exc:
                warnings.append(f"初始预览渲染失败：{exc}")

            self._preview = preview
            self._emit("modeling", {"state": "modeling", "job_id": job_id})
            return {
                "ok": True,
                "job_id": job_id,
                "job_dir": str(job_dir),
                "output_name": output_name,
                "bones": self._bones,
                "cubes": self._cubes,
                "texture": self._texture,
                "scale": round(scale, 6),
                "hands_ready": self._hands_ready,
                "preview": preview,
                "warnings": warnings,
            }

    def open_reference(self, app: str = "blockbench") -> dict[str, Any]:
        with self._lock:
            job_dir = self._require_job()
            if app != "blockbench":
                raise ModelingSessionError("目前只支持用 Blockbench 打开参考模型。")
            main_json = Path(self._source_models_dir) / "main.json"
            if not main_json.is_file():
                raise ModelingSessionError(f"参考模型 main.json 不存在：{main_json}")
            try:
                executable = resolve_blockbench(self.settings)
                subprocess.Popen([str(executable), str(main_json)], cwd=str(job_dir))
            except (ModelingToolError, OSError) as exc:
                raise ModelingSessionError(f"打开 Blockbench 失败：{exc}") from exc
            return {"ok": True, "app": "blockbench", "path": str(main_json)}

    def run_python(self, code: str, description: str, timeout_seconds: float = 120.0) -> dict[str, Any]:
        with self._lock:
            job_dir = self._require_job()
            if not code.strip():
                raise ModelingSessionError("code 不能为空。")
            if len(code.encode("utf-8")) > _MAX_CODE:
                raise ModelingSessionError("code 超过 100KB 上限。")
            timeout = min(max(float(timeout_seconds), 1.0), 300.0)
            self._script_index += 1
            script = job_dir / "scripts" / f"{self._script_index:03d}.py"
            script.write_text(code, encoding="utf-8")
            try:
                body = self._ensure_body()
                result = body.exec(code, description=description or "运行 Blender Python", timeout=timeout)
            except Exception as exc:
                self._last_error = str(exc)
                raise ModelingSessionError(f"Blender 执行失败：{exc}") from exc
            return {"ok": True, "script": str(script), **result}

    def render_preview(self, view: str, resolution: str = "900x1200", caption: str = "") -> dict[str, Any]:
        with self._lock:
            job_dir = self._require_job()
            if view not in _VIEWS:
                raise ModelingSessionError(f"view 必须是 {', '.join(sorted(_VIEWS))} 之一。")
            match = _RESOLUTION_RE.fullmatch(str(resolution))
            if not match:
                raise ModelingSessionError("resolution 必须写成 宽x高，例如 900x1200。")
            width, height = int(match.group(1)), int(match.group(2))
            if not (64 <= width <= 1600 and 64 <= height <= 1600):
                raise ModelingSessionError("resolution 每边必须在 64 到 1600 之间。")
            self._render_index += 1
            output = job_dir / "renders" / f"{self._render_index:03d}_{view}.png"
            try:
                body = self._ensure_body()
                body.render(
                    view=view,
                    resolution=f"{width}x{height}",
                    output=str(output),
                    timeout=120.0,
                )
            except Exception as exc:
                self._last_error = str(exc)
                raise ModelingSessionError(f"预览渲染失败：{exc}") from exc
            self._preview = str(output)
            return {
                "ok": True,
                "path": str(output),
                "view": view,
                "caption": caption,
            }

    def compile(
        self,
        output_name: str,
        surfaceprop: str = "flesh",
        make_lods: bool = True,
        make_hands: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            job_dir = self._require_job()
            if output_name != self._output_name:
                raise ModelingSessionError("compile 的 output_name 必须与 start 时一致。")
            if make_hands and not self._hands_ready:
                raise ModelingSessionError("手模骨架不可用，不能生成手模。")
            name = output_name
            export_dir = job_dir / "export"
            export_dir.mkdir(parents=True, exist_ok=True)
            body_smd = export_dir / f"{name}.smd"
            physics_smd = export_dir / f"{name}_physics.smd"
            hands_smd = export_dir / f"{name}_arms.smd"

            try:
                body = self._ensure_body()
                summary = body.exec(
                    "import json, prana_api\nprint(json.dumps(prana_api.scene_summary()))\n",
                    description="读取场景摘要",
                    timeout=120.0,
                )
                names = []
                stdout = str(summary.get("stdout", ""))
                if stdout.strip():
                    try:
                        names = list(json.loads(stdout.strip().splitlines()[-1]).get("names", []))
                    except (ValueError, TypeError):
                        names = []
                export_code = (
                    "import prana_api\n"
                    f"prana_api.export_smd({str(body_smd)!r}, {names!r}, 'PranaRig')\n"
                    f"prana_api.export_smd({str(physics_smd)!r}, {names!r}, 'PranaRig')\n"
                )
                if make_hands:
                    export_code += f"prana_api.export_smd({str(hands_smd)!r}, {names!r}, 'PranaRig')\n"
                body.exec(export_code, description="导出 SMD", timeout=300.0)
            except Exception as exc:
                raise ModelingSessionError(f"导出 SMD 失败：{exc}") from exc

            gmod = resolve_gmod(self.settings)
            game_dir = gmod / "garrysmod"
            staging_rel = f"prana_staging/{self._job_id}"
            body_qc_text = build_body_qc(name, surfaceprop=surfaceprop).replace(
                f'$modelname "player/{name}.mdl"',
                f'$modelname "{staging_rel}/player/{name}.mdl"',
            )
            body_qc = export_dir / f"{name}.qc"
            body_qc.write_text(body_qc_text, encoding="utf-8")
            qcs = [body_qc]
            if make_hands:
                hands_qc_text = build_hands_qc(name, surfaceprop=surfaceprop).replace(
                    f'$modelname "player/{name}_arms.mdl"',
                    f'$modelname "{staging_rel}/player/{name}_arms.mdl"',
                )
                hands_qc = export_dir / f"{name}_arms.qc"
                hands_qc.write_text(hands_qc_text, encoding="utf-8")
                qcs.append(hands_qc)

            studio = gmod / "bin" / "studiomdl.exe"
            if not studio.is_file():
                raise ModelingSessionError(f"找不到 studiomdl.exe：{studio}")
            log_path = job_dir / "logs" / "studiomdl.log"
            logs: list[str] = []
            for qc in qcs:
                proc = subprocess.run(
                    [str(studio), "-game", str(game_dir), "-nop4", str(qc)],
                    cwd=str(export_dir),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=300,
                )
                logs.append(proc.stdout + "\n" + proc.stderr)
                if proc.returncode != 0:
                    log_path.write_text("\n".join(logs), encoding="utf-8")
                    tail = (proc.stdout + "\n" + proc.stderr)[-3000:]
                    raise ModelingSessionError(f"studiomdl 编译失败：\n{tail}")
            log_path.write_text("\n".join(logs), encoding="utf-8")

            staging_root = game_dir / "models" / "prana_staging" / self._job_id / "player"
            body_files = [staging_root / f"{name}{ext}" for ext in (".mdl", ".vvd", ".vtx", ".phy")]
            hands_files = (
                [staging_root / f"{name}_arms{ext}" for ext in (".mdl", ".vvd", ".vtx")]
                if make_hands
                else []
            )
            missing = [str(path) for path in body_files + hands_files if not path.is_file()]
            if missing:
                raise ModelingSessionError(f"编译产物缺失：{', '.join(missing)}")

            body_check = validate_compiled_model(
                body_files[0],
                expected_bones=68,
                expected_include="models/m_anm.mdl",
            )
            if not body_check.get("ok"):
                raise ModelingSessionError(f"身体模型验证失败：{body_check.get('errors')}")
            if make_hands:
                hands_check = validate_compiled_model(
                    hands_files[0],
                    expected_bones=43,
                    expected_include="models/weapons/c_arms_animations.mdl",
                )
                if not hands_check.get("ok"):
                    raise ModelingSessionError(f"手模验证失败：{hands_check.get('errors')}")

            materials_dir = export_dir / "materials"
            materials_dir.mkdir(exist_ok=True)
            base = f"models/player/{name}/body"
            body_vmt = materials_dir / "body.vmt"
            body_vmt.write_text(build_vmt(base), encoding="utf-8")
            body_vtf = materials_dir / "body.vtf"
            write_vtf(
                body_vtf,
                width=64,
                height=64,
                rgba=bytes([180, 180, 180, 255]) * (64 * 64),
            )

            addon_dir = package_addon(
                addon_dir=job_dir / "addon",
                name=name,
                body_files=body_files,
                hands_files=hands_files,
                materials=[body_vmt, body_vtf],
                title=self._brief.strip() or name,
            )
            shutil.rmtree(game_dir / "models" / "prana_staging" / self._job_id, ignore_errors=True)
            return {
                "ok": True,
                "addon_dir": str(addon_dir),
                "body": [str(path) for path in body_files],
                "hands": [str(path) for path in hands_files],
                "warnings": list(body_check.get("warnings", [])),
            }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ok": True,
                "state": self._state,
                "job_id": self._job_id,
                "job_dir": str(self._job_dir) if self._job_dir else None,
                "output_name": self._output_name,
                "bones": self._bones,
                "cubes": self._cubes,
                "texture": list(self._texture),
                "scale": round(self._scale, 6),
                "hands_ready": self._hands_ready,
                "preview": self._preview,
                "body_running": bool(getattr(self._body, "running", False)) if self._body else False,
                "last_error": self._last_error,
            }

    def stop(self) -> dict[str, Any]:
        with self._lock:
            job_dir = self._job_dir
            body = self._body
            if body is not None:
                try:
                    if getattr(body, "running", False) and job_dir is not None:
                        body.save(str(job_dir / "work.blend"), timeout=120.0)
                except Exception as exc:
                    self._last_error = str(exc)
                finally:
                    try:
                        body.quit()
                    except Exception as exc:
                        self._last_error = str(exc)
            self._body = None
            self._state = "stopped"
            self._emit("modeling", {"state": "stopped", "job_id": self._job_id})
            return {"ok": True, "state": "stopped", "job_dir": str(job_dir) if job_dir else None}

    def close(self) -> None:
        self.stop()


__all__ = ["ModelingSession", "ModelingSessionError"]
