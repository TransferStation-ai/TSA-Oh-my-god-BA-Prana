"""SSE 流解析与 Chat / Responses 两个流式累加器。

纯逻辑模块：不依赖 tkinter / urllib / ai_client / main，无第三方依赖。
"""

from __future__ import annotations

import json
from typing import Iterable, Iterator


class StreamUnsupported(RuntimeError):
    """当前 AI 服务不支持流式响应。"""


def iter_sse(lines: Iterable[bytes | str]) -> Iterator[tuple[str, dict]]:
    """把 SSE 文本行迭代成 (event_name, json_obj)。

    - 按 ``\\n`` 切行并 ``rstrip("\\r")``；``bytes`` 先按 utf-8（errors=replace）解码。
    - ``:`` 开头视为注释忽略；``event:`` 记录当前事件名；``data:`` 追加缓冲（多行用 ``\\n`` 拼接）。
    - 空行且有 data 时解析 JSON 并产出 ``(event_name, obj)``，随后重置。
    - ``data: [DONE]`` 直接结束。
    """
    event_name = ""
    data_buf: list[str] = []
    for raw in lines:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        segments = raw.split("\n")
        if raw.endswith("\n"):
            # 行尾换行符本身不是空行，丢弃 split 出来的末尾占位空段。
            segments = segments[:-1]
        for line in segments:
            line = line.rstrip("\r")
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                value = line[len("event:"):]
                if value.startswith(" "):
                    value = value[1:]
                event_name = value
                continue
            if line.startswith("data:"):
                value = line[len("data:"):]
                if value.startswith(" "):
                    value = value[1:]
                data_buf.append(value)
                continue
            if not line:
                if data_buf:
                    data = "\n".join(data_buf)
                    data_buf = []
                    if data == "[DONE]":
                        return
                    try:
                        obj = json.loads(data)
                    except (ValueError, TypeError) as exc:
                        raise RuntimeError(
                            "AI 服务返回了无法解析的流式数据。"
                        ) from exc
                    yield (event_name, obj)
                event_name = ""
                continue
            # 其他 SSE 字段（id:/retry: 等）忽略


def _first_str(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def chat_reasoning_text(message: dict[str, object]) -> str:
    """从 chat 消息里提取 reasoning 文本：reasoning_content → reasoning → thinking。"""
    for field in ("reasoning_content", "reasoning", "thinking"):
        value = message.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


def responses_reasoning_text(payload: dict[str, object]) -> str:
    """遍历 Responses 载荷 ``output`` 里 ``type == "reasoning"`` 的项，拼接摘要与正文。"""
    parts: list[str] = []
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        for key in ("summary", "content"):
            section = item.get(key)
            if not isinstance(section, list):
                continue
            for part in section:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
    return "".join(parts)


def _safe_error_reason(data: dict[str, object]) -> str:
    """从失败事件里提取一个简短、不携带原始 output 的错误原因。"""
    reason = ""
    response = data.get("response")
    if isinstance(response, dict):
        error = response.get("error")
        if isinstance(error, dict):
            reason = _first_str(error.get("message"), error.get("code")) or ""
    if not reason:
        # error / response.incomplete 事件的顶层 error 载荷（dict 或 str）。
        error = data.get("error")
        if isinstance(error, str) and error:
            reason = error
        elif isinstance(error, dict):
            reason = _first_str(error.get("message"), error.get("code")) or ""
    if not reason:
        reason = _first_str(data.get("message")) or ""
    return reason or "未知错误"


class ChatStream:
    """累加 Chat Completions 风格的流式 chunk。"""

    content: str
    reasoning: str
    tool_calls: list[dict[str, object]]
    finish_reason: str | None

    def __init__(self) -> None:
        self.content = ""
        self.reasoning = ""
        self.tool_calls = []
        self.finish_reason = None

    def feed(self, chunk: dict[str, object]) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return events
        choice = choices[0]
        if not isinstance(choice, dict):
            return events
        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason:
            self.finish_reason = finish_reason
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return events

        # 设计固定 reasoning → content 的发射顺序：
        # content 先到会触发 UI 思考块折叠，随后到达的 reasoning 将被折叠丢失。
        reasoning = _first_str(
            delta.get("reasoning_content"), delta.get("reasoning"), delta.get("thinking"),
        )
        if reasoning is not None:
            self.reasoning += reasoning
            events.append(("reasoning", reasoning))

        content = delta.get("content")
        if isinstance(content, str) and content:
            self.content += content
            events.append(("content", content))

        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if isinstance(tc, dict):
                    self._merge_tool_call(tc)
        return events

    def _merge_tool_call(self, tc: dict[str, object]) -> None:
        index = tc.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            index = len(self.tool_calls)
        entry: dict[str, object] | None = None
        for existing in self.tool_calls:
            if existing.get("index") == index:
                entry = existing
                break
        if entry is None:
            entry = {"index": index, "id": "", "type": "function", "name": "", "arguments": ""}
            self.tool_calls.append(entry)
        tc_id = tc.get("id")
        if isinstance(tc_id, str) and tc_id and not entry["id"]:
            entry["id"] = tc_id
        tc_type = tc.get("type")
        if isinstance(tc_type, str) and tc_type and entry["type"] == "function":
            entry["type"] = tc_type
        function = tc.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name and not entry["name"]:
                entry["name"] = name
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                entry["arguments"] += arguments

    def message(self) -> dict[str, object]:
        msg: dict[str, object] = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            tools: list[dict[str, object]] = []
            for entry in self.tool_calls:
                call: dict[str, object] = {"type": entry["type"]}
                if entry["id"]:
                    call["id"] = entry["id"]
                call["function"] = {"name": entry["name"], "arguments": entry["arguments"]}
                tools.append(call)
            msg["tool_calls"] = tools
        return msg


class ResponsesStream:
    """累加 Responses API 风格的事件流。"""

    content: str
    reasoning: str
    final_payload: dict[str, object] | None

    def __init__(self) -> None:
        self.content = ""
        self.reasoning = ""
        self.final_payload = None

    def feed(self, name: str, data: dict[str, object]) -> list[tuple[str, str]]:
        if name in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
            delta = data.get("delta")
            if isinstance(delta, str) and delta:
                self.reasoning += delta
                return [("reasoning", delta)]
            return []
        if name == "response.output_text.delta":
            delta = data.get("delta")
            if isinstance(delta, str) and delta:
                self.content += delta
                return [("content", delta)]
            return []
        if name == "response.completed":
            response = data.get("response")
            if not isinstance(response, dict):
                raise RuntimeError("AI 服务流式响应失败：非法的 response.completed 载荷。")
            self.final_payload = response
            return []
        if name in ("response.failed", "response.incomplete", "error"):
            raise RuntimeError("AI 服务流式响应失败：" + _safe_error_reason(data))
        return []
