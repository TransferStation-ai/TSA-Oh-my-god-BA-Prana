import json
import os
from pathlib import Path
import re


OPENAI_COMPATIBLE_PACKAGES = {"@ai-sdk/openai-compatible", "@ai-sdk/openai"}
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
ENV_PLACEHOLDER_PATTERN = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


def config_file_path() -> Path:
    override = os.environ.get("OPENCODE_CONFIG")
    if override:
        return Path(override).expanduser()
    directory = Path.home() / ".config" / "opencode"
    for filename in ("opencode.json", "opencode.jsonc"):
        path = directory / filename
        if path.exists():
            return path
    return directory / "opencode.json"


def auth_file_path() -> Path:
    return Path.home() / ".local" / "share" / "opencode" / "auth.json"


def load_providers(
    config_path: Path | None = None,
    auth_path: Path | None = None,
) -> list[dict[str, object]]:
    resolved_config_path = (
        Path(config_path) if config_path is not None else config_file_path()
    )
    resolved_auth_path = (
        Path(auth_path) if auth_path is not None else auth_file_path()
    )
    if not resolved_config_path.exists():
        raise RuntimeError(
            f"未找到 OpenCode 配置文件：{resolved_config_path}"
        )

    config = _load_json_object(resolved_config_path, "配置文件")
    auth = (
        _load_json_object(resolved_auth_path, "认证文件")
        if resolved_auth_path.exists()
        else {}
    )
    provider_configs = config.get("provider")
    if not isinstance(provider_configs, dict):
        raise RuntimeError(
            f"OpenCode 配置文件缺少 provider 段：{resolved_config_path}"
        )

    raw_disabled = config.get("disabled_providers")
    disabled_ids = (
        {item for item in raw_disabled if isinstance(item, str)}
        if isinstance(raw_disabled, list)
        else set()
    )

    providers: list[dict[str, object]] = []
    for provider_id, provider_config in provider_configs.items():
        if not isinstance(provider_id, str) or not isinstance(
            provider_config, dict
        ):
            continue
        providers.append(
            _build_provider(
                provider_id, provider_config, auth, disabled_ids
            )
        )
    return providers


def _build_provider(
    provider_id: str,
    provider_config: dict[str, object],
    auth: dict[str, object],
    disabled_ids: set[str],
) -> dict[str, object]:
    options = provider_config.get("options")
    if not isinstance(options, dict):
        options = {}
    npm = provider_config.get("npm")
    base_url = _resolve_text(options.get("baseURL")).strip()
    if not base_url and npm == "@ai-sdk/openai":
        base_url = DEFAULT_OPENAI_BASE_URL

    raw_name = provider_config.get("name")
    name = (
        raw_name.strip()
        if isinstance(raw_name, str) and raw_name.strip()
        else provider_id
    )
    api_key = _resolve_text(options.get("apiKey")).strip()
    if not api_key:
        api_key = _api_key_from_auth(provider_id, auth)

    return {
        "id": provider_id,
        "name": name,
        "base_url": base_url,
        "api_key": api_key,
        "models": _build_models(provider_config.get("models")),
        "disabled": provider_id in disabled_ids,
        "compatible": _is_openai_compatible(npm),
    }


def _build_models(raw_models: object) -> list[dict[str, str]]:
    if not isinstance(raw_models, dict):
        return []
    models: list[dict[str, str]] = []
    for model_id, model_config in raw_models.items():
        if not isinstance(model_id, str) or not model_id:
            continue
        if not isinstance(model_config, dict):
            model_config = {}
        raw_identifier = model_config.get("id")
        identifier = (
            raw_identifier.strip()
            if isinstance(raw_identifier, str) and raw_identifier.strip()
            else model_id
        )
        raw_name = model_config.get("name")
        name = (
            raw_name.strip()
            if isinstance(raw_name, str) and raw_name.strip()
            else model_id
        )
        models.append({"id": identifier, "name": name})
    return models


def _api_key_from_auth(provider_id: str, auth: dict[str, object]) -> str:
    entry = auth.get(provider_id)
    if not isinstance(entry, dict) or entry.get("type") != "api":
        return ""
    key = entry.get("key")
    return key.strip() if isinstance(key, str) else ""


def _is_openai_compatible(npm: object) -> bool:
    return (
        not isinstance(npm, str)
        or not npm.strip()
        or npm.strip() in OPENAI_COMPATIBLE_PACKAGES
    )


def _resolve_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return ENV_PLACEHOLDER_PATTERN.sub(
        lambda match: os.environ.get(match.group(1), ""), value
    )


def to_linked_provider(
    provider: dict[str, object],
) -> dict[str, object]:
    models: list[dict[str, object]] = []
    raw_models = provider.get("models")
    if isinstance(raw_models, list):
        for model in raw_models:
            if not isinstance(model, dict):
                continue
            model_id = model.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                continue
            model_id = model_id.strip()
            name = model.get("name", model_id)
            models.append(
                {
                    "id": model_id,
                    "name": str(name).strip() or model_id,
                    "brainstorm": False,
                }
            )
    return {
        "id": str(provider.get("id", "")),
        "name": str(provider.get("name", "")),
        "base_url": str(provider.get("base_url", "")),
        "api_key": str(provider.get("api_key", "")),
        "source": "opencode",
        "models": models,
        "compatible": bool(provider.get("compatible", True)),
        "disabled": bool(provider.get("disabled", False)),
    }


def merge_linked_provider(
    existing: dict[str, object],
    converted: dict[str, object],
) -> dict[str, object]:
    merged = dict(converted)
    raw_models = converted.get("models")
    merged_models = (
        [dict(model) for model in raw_models if isinstance(model, dict)]
        if isinstance(raw_models, list)
        else []
    )
    merged["models"] = merged_models
    previous: dict[str, bool] = {}
    raw_existing_models = existing.get("models")
    if isinstance(raw_existing_models, list):
        for model in raw_existing_models:
            if not isinstance(model, dict):
                continue
            model_id = model.get("id")
            if isinstance(model_id, str):
                previous[model_id] = bool(model.get("brainstorm", False))
    for model in merged_models:
        model_id = model.get("id")
        if isinstance(model_id, str):
            model["brainstorm"] = previous.get(model_id, False)
    return merged


def _load_json_object(path: Path, description: str) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        raise RuntimeError(
            f"无法读取 OpenCode {description}：{path}"
        ) from error
    try:
        payload = json.loads(
            _remove_trailing_commas(_remove_json_comments(text))
        )
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"OpenCode {description}格式无效：{path}"
        ) from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"OpenCode {description}必须是 JSON 对象：{path}")
    return payload


def _remove_json_comments(text: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        character = text[index]
        if in_string:
            result.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            result.append(character)
            index += 1
            continue
        if text.startswith("//", index):
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end == -1 else end + 2
            continue
        result.append(character)
        index += 1
    return "".join(result)


def _remove_trailing_commas(text: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        character = text[index]
        if in_string:
            result.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
        elif character == ",":
            next_index = index + 1
            while next_index < len(text) and text[next_index].isspace():
                next_index += 1
            if (
                next_index < len(text)
                and text[next_index] in "]}"
            ):
                index += 1
                continue
        result.append(character)
        index += 1
    return "".join(result)
