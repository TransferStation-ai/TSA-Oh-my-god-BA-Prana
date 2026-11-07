import base64
import ctypes
from datetime import datetime
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path

import providers


DEFAULT_GAME_SETTINGS: dict[str, str] = {
    "mc_name": "Prana",
    "mc_skin_path": "",
    "mc_skin_command": "",
    "gmod_client_dir": "",
    "gmod_model": "",
    "gm_port": "27015",
    "gm_password": "",
}

DEFAULT_MODELING_SETTINGS: dict[str, str] = {
    "blender_path": "",
    "blockbench_path": "",
    "gmod_dir": "",
    "sfm_dir": "",
}


def normalize_game(value: object) -> dict[str, str]:
    if value is None:
        return dict(DEFAULT_GAME_SETTINGS)
    if not isinstance(value, dict):
        raise ValueError("游戏设置格式无效。")
    normalized = dict(DEFAULT_GAME_SETTINGS)
    for key in normalized:
        item = value.get(key, normalized[key])
        if not isinstance(item, str):
            raise ValueError("游戏设置格式无效。")
        normalized[key] = item.strip()
    if not normalized["mc_name"]:
        normalized["mc_name"] = "Prana"
    gm_port = normalized["gm_port"]
    if not (gm_port.isdigit() and 1 <= int(gm_port) <= 65535):
        normalized["gm_port"] = "27015"
    return normalized


def normalize_modeling(value: object) -> dict[str, str]:
    if value is None:
        return dict(DEFAULT_MODELING_SETTINGS)
    if not isinstance(value, dict):
        raise ValueError("建模设置格式无效。")
    normalized = dict(DEFAULT_MODELING_SETTINGS)
    for key in normalized:
        item = value.get(key, normalized[key])
        if not isinstance(item, str):
            raise ValueError("建模设置格式无效。")
        normalized[key] = item.strip()
    return normalized


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
        return {
            "provider_vault": {},
            "game": normalize_game(None),
            "modeling": normalize_modeling(None),
        }

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
        raw_vault = contents.get("provider_vault", {})
        if not isinstance(raw_vault, dict):
            raise ValueError("提供商库设置格式无效。")
        provider_vault: dict[str, dict[str, object]] = {}
        for key, raw_entry in raw_vault.items():
            if not isinstance(key, str) or not isinstance(raw_entry, dict):
                raise ValueError("提供商库设置格式无效。")
            encrypted_key = raw_entry.get("api_key_enc", "")
            if not isinstance(encrypted_key, str):
                raise ValueError("提供商库 API Key 设置格式无效。")
            models = raw_entry.get("models", [])
            if not isinstance(models, list):
                raise ValueError("提供商模型目录格式无效。")
            normalized_models: list[dict[str, str]] = []
            for model_entry in models:
                if not isinstance(model_entry, dict):
                    raise ValueError("提供商模型目录格式无效。")
                model_id = model_entry.get("id")
                model_name = model_entry.get("name", model_id)
                if not isinstance(model_id, str) or not isinstance(model_name, str):
                    raise ValueError("提供商模型目录格式无效。")
                normalized_models.append({"id": model_id, "name": model_name})
            entry: dict[str, object] = {
                "name": raw_entry.get("name", ""),
                "base_url": raw_entry.get("base_url", ""),
                "api_key": (
                    _unprotect_api_key(encrypted_key) if encrypted_key else ""
                ),
                "api_key_hint": raw_entry.get("api_key_hint", ""),
                "model": raw_entry.get("model", ""),
                "models": normalized_models,
                "image_model": raw_entry.get("image_model", ""),
                "video_model": raw_entry.get("video_model", ""),
                "review_model": raw_entry.get("review_model", ""),
                "updated_at": raw_entry.get("updated_at", ""),
            }
            if not all(
                isinstance(entry[field], str)
                for field in (
                    "name",
                    "base_url",
                    "api_key_hint",
                    "model",
                    "image_model",
                    "video_model",
                    "review_model",
                    "updated_at",
                )
            ):
                raise ValueError("提供商库设置格式无效。")
            provider_vault[key] = entry
        settings["provider_vault"] = provider_vault
        settings["game"] = normalize_game(contents.get("game"))
        settings["modeling"] = normalize_modeling(contents.get("modeling"))
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
        brainstorm_rounds = contents.get("brainstorm_rounds", 10)
        if (
            isinstance(brainstorm_rounds, bool)
            or not isinstance(brainstorm_rounds, int)
            or not 1 <= brainstorm_rounds <= 50
        ):
            raise ValueError("头脑风暴轮数设置无效。")
        settings["brainstorm_rounds"] = brainstorm_rounds

        raw_linked_providers = contents.get("linked_providers", [])
        if not isinstance(raw_linked_providers, list):
            raise ValueError("链接提供商设置格式无效。")
        linked_providers: list[dict[str, object]] = []
        for stored_provider in raw_linked_providers:
            if not isinstance(stored_provider, dict):
                raise ValueError("链接提供商设置格式无效。")
            provider = dict(stored_provider)
            encrypted_key = provider.get("api_key", "")
            if not isinstance(encrypted_key, str):
                raise ValueError("链接提供商 API Key 设置格式无效。")
            provider["api_key"] = (
                _unprotect_api_key(encrypted_key) if encrypted_key else ""
            )
            linked_providers.append(provider)
        settings["linked_providers"] = linked_providers
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
    brainstorm_rounds: int | None = None,
    linked_providers: list[dict[str, object]] | None = None,
    provider_vault: dict[str, dict[str, object]] | None = None,
    vault_models: list[dict[str, str]] | None = None,
    vault_key: str | None = None,
    game: dict[str, object] | None = None,
    modeling: dict[str, object] | None = None,
) -> dict[str, dict[str, object]]:
    path = settings_file_path()
    key = vault_key or providers.resolve_main_vault_key(base_url)
    if provider_vault is None:
        stored_vault = load_settings().get("provider_vault", {})
        provider_vault = (
            stored_vault if isinstance(stored_vault, dict) else {}
        )
    vault = {
        entry_key: dict(entry)
        for entry_key, entry in provider_vault.items()
    }
    entry = dict(vault.get(key) or {})
    preset = next(
        (item for item in providers.BUILTIN_PROVIDERS if item.matches(base_url)),
        None,
    )
    entry.update(
        {
            "base_url": providers.normalize_base_url(base_url),
            "api_key": api_key,
            "api_key_hint": providers.make_key_hint(api_key),
            "model": model,
            "models": (
                [dict(item) for item in vault_models]
                if vault_models is not None
                else entry.get("models", [])
            ),
            "image_model": (
                image_model if image_model is not None else entry.get("image_model", "")
            ),
            "video_model": (
                video_model if video_model is not None else entry.get("video_model", "")
            ),
            "review_model": (
                review_model if review_model is not None else entry.get("review_model", "")
            ),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    if not entry.get("name"):
        entry["name"] = (
            preset.name
            if preset is not None
            else f"自定义 {providers.make_key_hint(api_key)}"
        )
    vault[key] = entry

    contents = {
        "api_key": _protect_api_key(api_key),
        "base_url": base_url,
        "model": model,
        "subagents": [
            {**agent, "api_key": _protect_api_key(str(agent["api_key"]))}
            for agent in (subagents or [])
        ],
        "provider_vault": {
            entry_key: {
                **{
                    field: value
                    for field, value in stored_entry.items()
                    if field not in {"api_key", "api_key_enc"}
                },
                "api_key_enc": _protect_api_key(
                    str(stored_entry.get("api_key", ""))
                )
                if stored_entry.get("api_key")
                else "",
            }
            for entry_key, stored_entry in vault.items()
        },
        "game": normalize_game(game),
        "modeling": normalize_modeling(modeling),
    }
    if image_model is not None:
        contents["image_model"] = image_model
    if video_model is not None:
        contents["video_model"] = video_model
    if review_model is not None:
        contents["review_model"] = review_model
    if brainstorm_rounds is not None:
        if (
            isinstance(brainstorm_rounds, bool)
            or not isinstance(brainstorm_rounds, int)
            or not 1 <= brainstorm_rounds <= 50
        ):
            raise ValueError("头脑风暴轮数设置无效。")
        contents["brainstorm_rounds"] = brainstorm_rounds
    if linked_providers is not None:
        encrypted_providers: list[dict[str, object]] = []
        for provider in linked_providers:
            if not isinstance(provider, dict):
                raise ValueError("链接提供商设置格式无效。")
            stored = dict(provider)
            key = stored.get("api_key", "")
            if not isinstance(key, str):
                raise ValueError("链接提供商 API Key 设置格式无效。")
            stored["api_key"] = _protect_api_key(key) if key else ""
            encrypted_providers.append(stored)
        contents["linked_providers"] = encrypted_providers
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
    return vault
