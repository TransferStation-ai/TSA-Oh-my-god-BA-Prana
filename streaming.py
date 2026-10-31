"""SSE 流解析与 Chat / Responses 两个流式累加器。

纯逻辑模块：不依赖 tkinter / urllib / ai_client / main，无第三方依赖。
"""

from __future__ import annotations

import json
import re
import threading
import time
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


_HEADING_BOLD_RE = re.compile(r"^\*\*([^*\n]+)\*\*(?:\r?\n\r?\n|$)")


def reasoning_heading(text: str) -> str:
    """从 reasoning 正文开头提取一个短标题。"""
    match = _HEADING_BOLD_RE.match(text)
    if match:
        return match.group(1)
    for line in text.splitlines():
        if not line.strip():
            continue
        cleaned = line.replace("`", "").replace("*", "").replace("#", "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if len(cleaned) > 40:
            cleaned = cleaned[:40] + "…"
        return cleaned
    return ""


def format_reasoning_duration(seconds: float) -> str:
    """把已思考时长格式化成 "7s" / "1m 12s"。"""
    total = max(1, int(seconds))
    if total >= 60:
        return f"{total // 60}m {total % 60}s"
    return f"{total}s"


def shimmer_colors(start: str, end: str, steps: int = 6) -> list[str]:
    """在两个 #rrggbb 之间线性插值出 steps 个十六进制颜色。"""
    if steps < 2:
        raise ValueError("shimmer_colors 至少需要 2 步。")

    def parse(value: str) -> tuple[int, int, int]:
        value = value.lstrip("#")
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)

    start_color = parse(start)
    end_color = parse(end)
    colors: list[str] = []
    last = steps - 1
    for i in range(steps):
        ratio = i / last
        channels = []
        for a, b in zip(start_color, end_color):
            channels.append(round(a + (b - a) * ratio))
        colors.append("#{:02x}{:02x}{:02x}".format(*channels))
    return colors


def normalize_reasoning_blocks(value: object) -> list[dict[str, object]]:
    """把任意输入规整成 [{"text": str, "seconds": float}]，丢弃非法元素。"""
    if not isinstance(value, list):
        return []
    blocks: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        seconds = item.get("seconds")
        if not isinstance(text, str) or not text:
            continue
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            continue
        blocks.append({"text": text, "seconds": float(seconds)})
    return blocks


def should_follow(viewport_bottom: float, threshold: float = 0.98) -> bool:
    """视口贴底（或超过阈值）时允许自动滚动跟随。"""
    return viewport_bottom >= threshold


class StreamBuffer:
    """工作线程写入、UI 线程定时取走的事件缓冲。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[tuple[str, str, float]] = []
        self._closed = False
        self._wakeup_posted = False

    def push(self, kind: str, text: str) -> None:
        with self._lock:
            self._events.append((kind, text, time.monotonic()))

    def request_wakeup(self) -> bool:
        """请求一次唤醒；重复请求会被合并，返回是否是本次第一次。"""
        with self._lock:
            if self._wakeup_posted:
                return False
            self._wakeup_posted = True
            return True

    def drain(self) -> tuple[list[tuple[str, str, float]], bool]:
        """取走全部事件并复位唤醒标记；返回 (事件列表, 流是否已结束)。"""
        with self._lock:
            events = self._events
            self._events = []
            self._wakeup_posted = False
            closed = self._closed
        return events, closed

    def close(self) -> None:
        with self._lock:
            self._closed = True
