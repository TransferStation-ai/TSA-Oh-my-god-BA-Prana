"""Stateless bridge between internal Chat messages and official Responses.

The private sidecar is persisted, never displayed, and replayed only to its
original model and API base. It is not a server-side response reference.
"""

from copy import deepcopy
from typing import Any, TypeGuard

OFFICIAL_BASE_URL = "https://api.openai.com/v1"
RESPONSES_METADATA = "_responses"


def uses_responses(base_url: str) -> bool:
    return base_url.rstrip("/") == OFFICIAL_BASE_URL


def valid_metadata(value: object) -> TypeGuard[dict[str, Any]]:
    return (
        isinstance(value, dict)
        and isinstance(value.get("model"), str)
        and bool(value["model"])
        and isinstance(value.get("base_url"), str)
        and bool(value["base_url"])
        and isinstance(value.get("output"), list)
        and all(isinstance(item, dict) for item in value["output"])
    )


def chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip only our private protocol field; keep existing Chat behavior."""
    return [
        {key: value for key, value in message.items() if key != RESPONSES_METADATA}
        for message in messages
    ]


def _input_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise RuntimeError("Responses 输入消息内容格式无效。")
    converted = []
    for part in content:
        if not isinstance(part, dict):
            raise RuntimeError("Responses 输入内容项格式无效。")
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            converted.append({"type": "input_text", "text": part["text"]})
        elif part.get("type") == "image_url":
            image = part.get("image_url")
            if not isinstance(image, dict) or not isinstance(image.get("url"), str):
                raise RuntimeError("Responses 输入图片格式无效。")
            item = {"type": "input_image", "image_url": image["url"]}
            if "detail" in image:
                item["detail"] = image["detail"]
            converted.append(item)
        else:
            raise RuntimeError("Responses 不支持该输入内容项类型。")
    return converted


def build_payload(
    messages: list[dict[str, Any]],
    *,
    model: str,
    base_url: str,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        metadata = message.get(RESPONSES_METADATA)
        if (
            role == "assistant"
            and valid_metadata(metadata)
            and metadata["model"] == model
            and metadata["base_url"] == base_url.rstrip("/")
        ):
            # Raw items replace, rather than accompany, the Chat projection.
            items.extend(deepcopy(metadata["output"]))
            continue
        if role == "tool":
            items.append({"type": "function_call_output",
                          "call_id": message.get("tool_call_id"),
                          "output": message.get("content")})
            continue
        if role not in {"system", "developer", "user", "assistant"}:
            raise RuntimeError("Responses 输入消息角色无效。")
        content = message.get("content")
        if content is not None and (content != "" or not message.get("tool_calls")):
            items.append({"role": role, "content": _input_content(content)})
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                function = call["function"]
                items.append({"type": "function_call", "call_id": call["id"],
                              "name": function["name"], "arguments": function["arguments"]})

    payload: dict[str, Any] = {
        "model": model, "input": items, "store": False,
        "include": ["reasoning.encrypted_content"],
    }
    if tools is not None:
        flat_tools = []
        for tool in tools:
            function = tool["function"]
            flat = {"type": "function", **deepcopy(function)}
            # Responses defaults to strict schemas; Chat schemas may be optional.
            flat.setdefault("strict", False)
            flat_tools.append(flat)
        payload["tools"] = flat_tools
        payload["parallel_tool_calls"] = False
    if tool_choice is not None:
        payload["tool_choice"] = (
            {"type": "function", "name": tool_choice["function"]["name"]}
            if isinstance(tool_choice, dict) else tool_choice
        )
    return payload


def parse_response(payload: Any, *, model: str, base_url: str) -> dict[str, Any]:
    # Never interpolate raw output, encrypted content, or arbitrary error objects
    # into user-visible errors. Validate the whole response before executing tools.
    if not isinstance(payload, dict):
        raise RuntimeError("Responses 响应格式无效。")
    status = payload.get("status")
    if status != "completed":
        if status == "incomplete":
            details = payload.get("incomplete_details")
            reason = details.get("reason") if isinstance(details, dict) else None
            if reason not in ("max_output_tokens", "content_filter"):
                reason = "unknown"
            raise RuntimeError(f"Responses 响应未完成 (incomplete): {reason}。")
        if status in ("failed", "cancelled", "queued", "in_progress"):
            raise RuntimeError(f"Responses 响应未成功完成 ({status})。")
        raise RuntimeError("Responses 响应状态格式无效。")
    if payload.get("error") is not None:
        raise RuntimeError("Responses 返回了错误。")
    output = payload.get("output")
    if not isinstance(output, list) or not output:
        raise RuntimeError("Responses 响应缺少 output 内容。")
    texts: list[str] = []
    calls: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            raise RuntimeError("Responses output 项格式无效。")
        if item.get("status") not in (None, "completed"):
            raise RuntimeError("Responses output 项未完成。")
        kind = item.get("type")
        if kind == "reasoning":
            continue
        if kind == "message":
            if item.get("role") != "assistant" or not isinstance(item.get("content"), list):
                raise RuntimeError("Responses message 格式无效。")
            for part in item["content"]:
                if not isinstance(part, dict):
                    raise RuntimeError("Responses message 内容格式无效。")
                part_type = part.get("type")
                field = (
                    "text" if part_type == "output_text"
                    else "refusal" if part_type == "refusal" else None
                )
                if field is None or not isinstance(part.get(field), str):
                    raise RuntimeError("Responses message 内容类型无效。")
                texts.append(part[field])
        elif kind == "function_call":
            if not all(isinstance(item.get(key), str) and item[key]
                       for key in ("call_id", "name", "arguments")):
                raise RuntimeError("Responses function_call 格式无效。")
            calls.append({"id": item["call_id"], "type": "function", "function": {
                "name": item["name"], "arguments": item["arguments"]}})
        else:
            raise RuntimeError("Responses 返回了不支持的 output 类型。")
    if not calls and not "".join(texts).strip():
        raise RuntimeError("Responses 返回了空回复。")
    result: dict[str, Any] = {"role": "assistant", "content": "".join(texts) or None}
    if calls:
        result["tool_calls"] = calls
    result[RESPONSES_METADATA] = {
        "model": model, "base_url": base_url.rstrip("/"), "output": deepcopy(output),
    }
    return result
