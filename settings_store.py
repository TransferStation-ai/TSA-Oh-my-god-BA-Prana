import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def settings_file_path() -> Path:
    app_data = os.environ.get("LOCALAPPDATA")
    if app_data:
        return Path(app_data) / "Prana" / "settings.json"
    return Path.home() / ".config" / "prana" / "settings.json"


def _crypt(data: bytes, *, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("API Key 加密存储仅支持 Windows。")

    input_buffer = (ctypes.c_byte * len(data)).from_buffer_copy(data)
    input_blob = _DataBlob(
        len(data), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte))
    )
    output_blob = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    function.restype = wintypes.BOOL
    description = ctypes.c_wchar_p("Prana API Key") if protect else None
    description_pointer = (
        ctypes.cast(description, ctypes.c_void_p) if protect else None
    )

    if not function(
        ctypes.byref(input_blob),
        description_pointer,
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    ):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, ctypes.FormatError(error_code))

    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        local_free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def _protect_api_key(api_key: str) -> str:
    encrypted = _crypt(api_key.encode("utf-8"), protect=True)
    return base64.b64encode(encrypted).decode("ascii")


def _unprotect_api_key(encrypted: str) -> str:
    try:
        ciphertext = base64.b64decode(encrypted, validate=True)
        return _crypt(ciphertext, protect=False).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise RuntimeError("保存的 API Key 无法解密，请重新配置。") from error


def load_settings() -> dict[str, object]:
    path = settings_file_path()
    if not path.exists():
        return {}

    try:
        contents = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(contents, dict):
            raise ValueError("设置内容必须是 JSON 对象。")
        api_key = _unprotect_api_key(contents["api_key"])
        base_url = contents["base_url"]
        model = contents["model"]
        if not isinstance(base_url, str) or not isinstance(model, str):
            raise ValueError("设置字段格式无效。")
        stored_subagents = contents.get("subagents", [])
        if not isinstance(stored_subagents, list):
            raise ValueError("子代理设置格式无效。")
        subagents: list[dict[str, object]] = []
        for stored_agent in stored_subagents:
            if not isinstance(stored_agent, dict):
                raise ValueError("子代理设置格式无效。")
            agent = dict(stored_agent)
            encrypted_key = agent.get("api_key")
            if not isinstance(encrypted_key, str):
                raise ValueError("子代理 API Key 设置格式无效。")
            agent["api_key"] = _unprotect_api_key(encrypted_key)
            subagents.append(agent)
        settings = {
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "subagents": subagents,
        }
        for field in ("image_model", "video_model"):
            value = contents.get(field)
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError("媒体生成模型设置格式无效。")
                settings[field] = value
        review_model = contents.get("review_model")
        if review_model is not None:
            if not isinstance(review_model, str):
                raise ValueError("操作审查模型设置格式无效。")
            settings["review_model"] = review_model
        return settings
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise RuntimeError(f"无法读取 Prana 设置文件：{error}") from error


def save_settings(
    api_key: str,
    base_url: str,
    model: str,
    subagents: list[dict[str, object]] | None = None,
    image_model: str | None = None,
    video_model: str | None = None,
    review_model: str | None = None,
) -> None:
    path = settings_file_path()
    contents = {
        "api_key": _protect_api_key(api_key),
        "base_url": base_url,
        "model": model,
        "subagents": [
            {**agent, "api_key": _protect_api_key(str(agent["api_key"]))}
            for agent in (subagents or [])
        ],
    }
    if image_model is not None:
        contents["image_model"] = image_model
    if video_model is not None:
        contents["video_model"] = video_model
    if review_model is not None:
        contents["review_model"] = review_model
    temporary_path = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps(contents, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except OSError as error:
        raise RuntimeError(f"无法保存 Prana 设置文件：{error}") from error
