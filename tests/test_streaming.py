import json
import unittest

from streaming import (
    ChatStream,
    ResponsesStream,
    chat_reasoning_text,
    format_reasoning_duration,
    iter_sse,
    normalize_reasoning_blocks,
    reasoning_heading,
    responses_reasoning_text,
    shimmer_colors,
    should_follow,
    StreamBuffer,
)


def sse(*payloads: dict) -> list[bytes]:
    return [f"data: {json.dumps(p)}\n\n".encode("utf-8") for p in payloads]


class SseTests(unittest.TestCase):
    def test_split_crlf_comment_multiline_and_done(self) -> None:
        lines = [
            b": keep-alive\r\n",
            b"event: message\r\n",
            b'data: {"a": 1}\r\n',
            b"\r\n",
            b'data: {"b":\r\n',
            b'data:  2}\r\n',
            b"\r\n",
            b"data: [DONE]\r\n",
            b"\r\n",
            b'data: {"c": 3}\r\n',
        ]
        self.assertEqual(
            list(iter_sse(lines)),
            [("message", {"a": 1}), ("", {"b": 2})],
        )

    def test_invalid_json_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            list(iter_sse([b"data: {oops}\n\n"]))


class ChatStreamTests(unittest.TestCase):
    def test_mixes_reasoning_and_content(self) -> None:
        stream = ChatStream()
        events = []
        for chunk in sse(
            {"choices": [{"delta": {"reasoning_content": "先看"}}]},
            {"choices": [{"delta": {"reasoning_content": "目录。"}}]},
            {"choices": [{"delta": {"content": "好的"}}]},
            {"choices": [{"delta": {"content": "。"}, "finish_reason": "stop"}]},
        ):
            for name, data in iter_sse([chunk]):
                events.extend(stream.feed(data))
        self.assertEqual(
            events,
            [("reasoning", "先看"), ("reasoning", "目录。"),
             ("content", "好的"), ("content", "。")],
        )
        self.assertEqual(stream.reasoning, "先看目录。")
        self.assertEqual(stream.message(), {"role": "assistant", "content": "好的。"})
        self.assertEqual(stream.finish_reason, "stop")

    def test_tool_calls_merge_by_index(self) -> None:
        stream = ChatStream()
        chunk = {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call-1", "type": "function",
             "function": {"name": "send_image", "arguments": '{"prompt":'}},
        ]}}]}
        stream.feed(chunk)
        stream.feed({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '"猫"}'}},
        ]}}]})
        self.assertEqual(
            stream.message()["tool_calls"],
            [{"id": "call-1", "type": "function",
              "function": {"name": "send_image", "arguments": '{"prompt":"猫"}'}}],
        )

    def test_openrouter_and_thinking_fields(self) -> None:
        for field in ("reasoning", "thinking"):
            stream = ChatStream()
            events = stream.feed({"choices": [{"delta": {field: "想"}}]})
            self.assertEqual(events, [("reasoning", "想")])

    def test_combined_delta_emits_reasoning_before_content(self) -> None:
        # 设计固定 reasoning → content 发射顺序：同一 delta 含两字段时，
        # content 先触发 UI 思考块折叠会把随后到达的 reasoning 丢失。
        stream = ChatStream()
        events = stream.feed({"choices": [{"delta": {
            "reasoning_content": "想法", "content": "答案",
        }}]})
        self.assertEqual(events, [("reasoning", "想法"), ("content", "答案")])
        self.assertEqual(stream.reasoning, "想法")
        self.assertEqual(stream.content, "答案")


class ResponsesStreamTests(unittest.TestCase):
    def test_deltas_and_final_payload(self) -> None:
        stream = ResponsesStream()
        events = []
        events += stream.feed("response.reasoning_summary_text.delta", {"delta": "分析"})
        events += stream.feed("response.output_text.delta", {"delta": "答案"})
        events += stream.feed("response.completed", {"response": {"id": "r1"}})
        self.assertEqual(events, [("reasoning", "分析"), ("content", "答案")])
        self.assertEqual(stream.final_payload, {"id": "r1"})

    def test_failed_event_raises(self) -> None:
        stream = ResponsesStream()
        with self.assertRaises(RuntimeError):
            stream.feed("response.failed", {"response": {"error": {"message": "boom"}}})

    def test_top_level_error_payload_becomes_reason(self) -> None:
        # error / response.incomplete 事件常见载荷：顶层 error（dict 或 str）。
        with self.assertRaises(RuntimeError) as ctx:
            ResponsesStream().feed("error", {"error": {"message": "配额不足"}})
        self.assertEqual(str(ctx.exception), "AI 服务流式响应失败：配额不足")
        with self.assertRaises(RuntimeError) as ctx:
            ResponsesStream().feed("response.incomplete", {"error": "上游中断"})
        self.assertEqual(str(ctx.exception), "AI 服务流式响应失败：上游中断")
        with self.assertRaises(RuntimeError) as ctx:
            ResponsesStream().feed("error", {"error": {"code": "rate_limited"}})
        self.assertEqual(str(ctx.exception), "AI 服务流式响应失败：rate_limited")


class ReasoningExtractionTests(unittest.TestCase):
    def test_chat_extracts_first_reasoning_field(self) -> None:
        self.assertEqual(chat_reasoning_text({"reasoning_content": "甲"}), "甲")
        self.assertEqual(chat_reasoning_text({"reasoning": "乙"}), "乙")
        self.assertEqual(chat_reasoning_text({"content": "正文"}), "")

    def test_responses_walks_summary_and_content(self) -> None:
        payload = {"output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "摘要"}]},
            {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "正文"}]},
        ]}
        self.assertEqual(responses_reasoning_text(payload), "摘要正文")


class DisplayLogicTests(unittest.TestCase):
    def test_heading_prefers_bold_title(self) -> None:
        self.assertEqual(reasoning_heading("**梳理目录**\n\n先看文件。"), "梳理目录")

    def test_heading_falls_back_to_first_line(self) -> None:
        self.assertEqual(reasoning_heading("## 看 `main.py`\n后面的内容"), "看 main.py")

    def test_heading_truncates_at_40(self) -> None:
        self.assertEqual(len(reasoning_heading("字" * 60)), 41)
        self.assertTrue(reasoning_heading("字" * 60).endswith("…"))

    def test_heading_empty(self) -> None:
        self.assertEqual(reasoning_heading("  \n\n"), "")

    def test_duration_formats(self) -> None:
        self.assertEqual(format_reasoning_duration(0.2), "1s")
        self.assertEqual(format_reasoning_duration(7.4), "7s")
        self.assertEqual(format_reasoning_duration(72), "1m 12s")

    def test_shimmer_colors_endpoints(self) -> None:
        colors = shimmer_colors("#a4a5b5", "#f1f1f5", 6)
        self.assertEqual(colors[0], "#a4a5b5")
        self.assertEqual(colors[-1], "#f1f1f5")
        self.assertEqual(len(colors), 6)

    def test_normalize_blocks_drops_junk(self) -> None:
        self.assertEqual(
            normalize_reasoning_blocks([
                {"text": "好", "seconds": 3},
                {"text": "", "seconds": 9},
                "x",
                {"seconds": 2},
                {"text": "坏", "seconds": "NaN"},
            ]),
            [{"text": "好", "seconds": 3.0}],
        )
        self.assertEqual(normalize_reasoning_blocks(None), [])

    def test_should_follow(self) -> None:
        self.assertTrue(should_follow(1.0))
        self.assertTrue(should_follow(0.98))
        self.assertFalse(should_follow(0.5))


class StreamBufferTests(unittest.TestCase):
    def test_wakeup_is_coalesced_and_drain_resets(self) -> None:
        buffer = StreamBuffer()
        self.assertTrue(buffer.request_wakeup())
        self.assertFalse(buffer.request_wakeup())
        buffer.push("content", "你好")
        events, closed = buffer.drain()
        self.assertEqual([kind for kind, _, _ in events], ["content"])
        self.assertFalse(closed)
        self.assertTrue(buffer.request_wakeup())
        buffer.push("round", "")
        buffer.close()
        _, closed = buffer.drain()
        self.assertTrue(closed)
