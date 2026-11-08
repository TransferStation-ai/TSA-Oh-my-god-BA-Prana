import json
import unittest

from streaming import (
    ChatStream,
    ResponsesStream,
    StreamState,
    chat_reasoning_text,
    format_reasoning_duration,
    iter_sse,
    normalize_activity_blocks,
    normalize_public_progress_blocks,
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

    def test_normalize_activity_blocks_validates_saved_tool_details(self) -> None:
        self.assertEqual(
            normalize_activity_blocks([
                {
                    "id": "call-1",
                    "name": "read_git_repository",
                    "arguments": '{"action":"status"}',
                    "status": "completed",
                },
                {"name": "incomplete"},
                {
                    "id": "call-2",
                    "name": "bad",
                    "arguments": "{}",
                    "status": "unknown",
                },
            ]),
            [{
                "id": "call-1",
                "name": "read_git_repository",
                "arguments": '{"action":"status"}',
                "status": "completed",
            }],
        )
        self.assertEqual(normalize_activity_blocks(None), [])

    def test_public_progress_hides_legacy_private_reasoning(self) -> None:
        self.assertEqual(
            normalize_public_progress_blocks([
                {"text": "旧版内部推理原文", "seconds": 2},
                {
                    "text": "正在分析请求并规划下一步。",
                    "seconds": 3,
                },
            ]),
            [{
                "text": "正在分析请求并规划下一步。",
                "seconds": 3.0,
            }],
        )

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


class StreamStateTests(unittest.TestCase):
    def test_single_round_thinking_then_content(self) -> None:
        state = StreamState()
        actions = state.apply("reasoning", "想", 10.0)
        actions += state.apply("reasoning", "完了", 13.0)
        actions += state.apply("content", "答案", 14.0)
        self.assertEqual(actions[0], ("begin_reasoning", None))
        self.assertEqual(actions[-2],
                         ("finish_reasoning", {"text": "想完了", "seconds": 3.0}))
        self.assertEqual(actions[-1], ("content", "答案"))
        self.assertEqual(state.content, "答案")
        self.assertEqual(state.blocks, [{"text": "想完了", "seconds": 3.0}])

    def test_round_event_closes_block_without_content(self) -> None:
        state = StreamState()
        state.apply("reasoning", "挑工具", 1.0)
        actions = state.apply("round", "", 9.0)
        self.assertEqual(actions, [("finish_reasoning", {"text": "挑工具", "seconds": 1.0})])

    def test_second_round_gets_its_own_block_and_blank_line(self) -> None:
        state = StreamState()
        state.apply("reasoning", "第一轮", 1.0)
        state.apply("content", "先做一点", 2.0)
        state.apply("round", "", 3.0)
        actions = state.apply("reasoning", "第二轮", 4.0)
        self.assertEqual(actions, [("begin_reasoning", None), ("reasoning", "第二轮")])
        actions = state.apply("content", "收尾", 5.0)
        self.assertEqual(actions[-1], ("content", "\n\n收尾"))
        self.assertEqual(state.content, "先做一点\n\n收尾")

    def test_no_reasoning_means_no_block(self) -> None:
        state = StreamState()
        self.assertEqual(state.apply("content", "直接回答", 1.0), [("content", "直接回答")])
        self.assertEqual(state.blocks, [])

    def test_finish_without_active_block_is_noop(self) -> None:
        state = StreamState()
        state.apply("content", "好", 1.0)
        self.assertEqual(state.finish(2.0), [])

    def test_duration_never_below_one_second(self) -> None:
        state = StreamState()
        state.apply("reasoning", "短", 5.0)
        actions = state.apply("content", "答", 5.1)
        self.assertEqual(actions[0][1]["seconds"], 1.0)

    def test_finish_closes_active_block(self) -> None:
        state = StreamState()
        state.apply("reasoning", "想透了", 2.0)
        self.assertEqual(
            state.finish(9.0),
            [("finish_reasoning", {"text": "想透了", "seconds": 7.0})],
        )
        self.assertEqual(state.blocks, [{"text": "想透了", "seconds": 7.0}])
        # finish 路径同样执行 1 秒下限 clamp，并保证重复 finish 为 noop。
        state.apply("reasoning", "再想一遍", 20.0)
        self.assertEqual(
            state.finish(20.1),
            [("finish_reasoning", {"text": "再想一遍", "seconds": 1.0})],
        )
        self.assertEqual(state.finish(21.0), [])

    def test_tool_activity_tracks_arguments_and_completion(self) -> None:
        state = StreamState()
        actions = state.apply(
            "tool_start",
            json.dumps({
                "id": "call-1",
                "name": "search_workspace_files",
                "arguments": '{"query":"README"}',
            }),
            1.0,
        )
        self.assertEqual(actions[0][0], "tool_start")
        self.assertEqual(state.activity_blocks[0]["status"], "running")
        actions = state.apply(
            "tool_finish",
            json.dumps({"id": "call-1", "status": "completed"}),
            2.0,
        )
        self.assertEqual(actions[0][0], "tool_finish")
        self.assertEqual(state.activity_blocks[0]["status"], "completed")

    def test_finish_marks_unfinished_tool_as_interrupted(self) -> None:
        state = StreamState()
        state.apply(
            "tool_start",
            json.dumps({
                "id": "call-1",
                "name": "search_workspace_files",
                "arguments": "{}",
            }),
            1.0,
        )
        state.finish(2.0)
        self.assertEqual(state.activity_blocks[0]["status"], "interrupted")
