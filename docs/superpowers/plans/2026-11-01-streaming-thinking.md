# 流式输出 + 思考过程 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 Prana 的回复随 token 流式显示，并把模型 reasoning 渲染成 OpenCode 桌面端 v1 风格的可折叠思考块（`Thinking` → `Thought 7s`）。

**架构：** 新增纯逻辑模块 `streaming.py`（SSE 解析、两个累加器、事件状态机），`ai_client` 只在调用方传 `on_event` 时发 `stream: true` 请求并把增量回调出去，失败自动回退到现有非流式路径；`main.py` 用 `ThinkingBlock` + `_StreamView` 在工作线程写缓冲、UI 线程按 40ms 泵渲染，收尾时复用 `_add_message` 重建最终气泡并落盘 `reasoning_blocks`。

**技术栈：** Python 3.10+、标准库（`urllib`、`json`、`threading`、`time`）、Tkinter、unittest + pytest 运行器。

**规格：** `docs/superpowers/specs/2026-11-01-streaming-thinking-design.md`（执行者两份都读）

## 全局约束

- **只在 `on_event is not None` 时**给请求体加 `"stream": True`；`complete()` 的既有调用方（头脑风暴、审查模型、子代理）payload 必须逐字节不变。
- 思考内容**永不**写入 API 消息（`build_api_messages` 不复制 `reasoning_blocks`，`_responses` sidecar 逻辑不动）。
- 不改 `MAX_TOOL_ROUNDS`、`MAX_CHAT_REQUEST_ATTEMPTS`、`RETRY_DELAY_SECONDS`。
- Tk 控件只在 UI 线程改动；工作线程只允许写 `StreamBuffer`（自带锁）。
- 思考块文案固定为英文 `Thinking` / `Thought`；无 reasoning 时不渲染、不留空白；完成后默认折叠。
- 新增依赖为零；`streaming.py` 不得 import `main`、`ai_client`、`tkinter`、`urllib`。
- 所有回调签名固定为 `Callable[[str, str], None]`（kind, text），kind ∈ `reasoning` / `content` / `round`。

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `streaming.py`（新建） | SSE 解析、`ChatStream` / `ResponsesStream` 累加器、reasoning 提取、`reasoning_heading`、`format_reasoning_duration`、`shimmer_colors`、`normalize_reasoning_blocks`、`should_follow`、`StreamBuffer`、`StreamState` |
| `ai_client.py` | `_build_request_payload` 抽取；`_complete_message_once_stream`；`on_event` / `should_stop` 透传；`_streaming_disabled` 回退 |
| `sessions.py` | `new_message(..., reasoning_blocks=...)` |
| `main.py` | `ThinkingBlock` 组件、`_StreamView`、`_pump_stream` / `_apply_stream_action`、`_finalize_stream`、`_handle_reply` / `_stop_generation` / 失败中断接线 |
| `tests/test_streaming.py`（新建） | 上述纯逻辑的全部单测 |
| `tests/test_ai_client.py` | 流式请求体、回调序列、回退、`[DONE]`、增量后不重试 |
| `tests/test_sessions.py` | `reasoning_blocks` 往返与 API 隔离 |

---

## 任务 1：`streaming.py` — SSE 解析与两个累加器

**文件：**
- 创建：`streaming.py`
- 测试：`tests/test_streaming.py`

**接口：**

```python
class StreamUnsupported(RuntimeError): ...

def iter_sse(lines: Iterable[bytes | str]) -> Iterator[tuple[str, dict]]: ...
def chat_reasoning_text(message: dict[str, object]) -> str: ...
def responses_reasoning_text(payload: dict[str, object]) -> str: ...

class ChatStream:
    content: str
    reasoning: str
    tool_calls: list[dict[str, object]]
    finish_reason: str | None
    def feed(self, chunk: dict[str, object]) -> list[tuple[str, str]]: ...
    def message(self) -> dict[str, object]: ...

class ResponsesStream:
    content: str
    reasoning: str
    final_payload: dict[str, object] | None
    def feed(self, name: str, data: dict[str, object]) -> list[tuple[str, str]]: ...
```

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_streaming.py` 顶部：

```python
import json
import unittest

from streaming import (
    ChatStream,
    ResponsesStream,
    chat_reasoning_text,
    iter_sse,
    responses_reasoning_text,
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
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_streaming.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'streaming'`

- [ ] **步骤 3：实现 `streaming.py`**

`iter_sse`：按 `\n` 切行并 `rstrip("\r")`；`event:` 记录当前事件名；`data:` 追加到缓冲区（多行用 `\n` 拼接）；空行且有 data 时 `json.loads` → `yield (event_name, obj)`，随后重置；`data: [DONE]` 直接 `return`；JSON 失败抛 `RuntimeError("AI 服务返回了无法解析的流式数据。")`。`bytes` 输入先 `decode("utf-8", errors="replace")`。

`ChatStream.feed`：只读 `choices[0].delta`；`content` 为非空 str 时累加并产出；reasoning 按 `reasoning_content` → `reasoning` → `thinking` 取第一个 str；`tool_calls` 按 `index` 合并（`id`/`function.name` 取首次、`function.arguments` 拼接、`type` 缺省 `function`）；`finish_reason` 记录最后一次非空。`message()` 返回 `{"role": "assistant", "content": content or None}`，有 tool_calls 时附加，且**不**带 reasoning 字段。

`ResponsesStream.feed`：`response.reasoning_summary_text.delta` / `response.reasoning_text.delta` → reasoning；`response.output_text.delta` → content；`response.completed` → 保存 `data["response"]`（非 dict 则视为非法，抛 RuntimeError）；`response.failed` / `response.incomplete` / `error` → `RuntimeError("AI 服务流式响应失败：{安全 reason}")`，不拼接原始 output。

`chat_reasoning_text`：对 `reasoning_content` / `reasoning` / `thinking` 取第一个非空 str，否则 `""`。
`responses_reasoning_text`：遍历 `payload["output"]` 中 `type == "reasoning"` 的项，拼接 `summary[].text` 与 `content[].text`（各自要求 `isinstance(text, str)`）。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_streaming.py -q`
预期：PASS（10 passed）

- [ ] **步骤 5：Commit**

```bash
git add streaming.py tests/test_streaming.py
git commit -m "feat(streaming): add SSE parser and chat/responses accumulators"
```

---

## 任务 2：`streaming.py` — 展示逻辑与线程缓冲

**文件：**
- 修改：`streaming.py`
- 测试：`tests/test_streaming.py`

**接口：**

```python
def reasoning_heading(text: str) -> str: ...
def format_reasoning_duration(seconds: float) -> str: ...
def shimmer_colors(start: str, end: str, steps: int = 6) -> list[str]: ...
def normalize_reasoning_blocks(value: object) -> list[dict[str, object]]: ...
def should_follow(viewport_bottom: float, threshold: float = 0.98) -> bool: ...

class StreamBuffer:
    def __init__(self) -> None: ...
    def push(self, kind: str, text: str) -> None: ...
    def request_wakeup(self) -> bool: ...
    def drain(self) -> tuple[list[tuple[str, str, float]], bool]: ...
    def close(self) -> None: ...
```

- [ ] **步骤 1：编写失败的测试**

```python
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
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_streaming.py -q`
预期：FAIL，`ImportError: cannot import name 'reasoning_heading'`

- [ ] **步骤 3：实现**

- `reasoning_heading`：正则 `^\*\*([^*\n]+)\*\*(?:\r?\n\r?\n|$)` 命中取第 1 组；否则取第一条非空行，去掉 `` ` ``、`*`、`#`，`re.sub(r"\s+", " ", ...)`，超过 40 字 → `text[:40] + "…"`。
- `format_reasoning_duration`：`total = max(1, int(seconds))`；`>= 60` → `f"{total // 60}m {total % 60}s"`，否则 `f"{total}s"`。
- `shimmer_colors`：解析两个 `#rrggbb`，对每个通道线性插值 `steps` 个值（含两端），返回大写十六进制列表；`steps < 2` 时抛 `ValueError`。
- `normalize_reasoning_blocks`：只保留 `dict` 且 `text` 为非空 str、`seconds` 为数字（`bool` 排除）的元素，输出 `{"text": text, "seconds": float(seconds)}`；其他一律丢弃。
- `should_follow`：`viewport_bottom >= threshold`。
- `StreamBuffer`：`threading.Lock` + `events: list[tuple[str, str, float]]` + `closed` + `wakeup_posted`；`push` 在锁内 `time.monotonic()` 打时间戳；`drain` 在锁内取走事件、复位 `wakeup_posted`、返回 `(events, closed)`。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_streaming.py -q`
预期：PASS（18 passed）

- [ ] **步骤 5：Commit**

```bash
git add streaming.py tests/test_streaming.py
git commit -m "feat(streaming): add reasoning heading, durations and event buffer"
```

---

## 任务 3：`streaming.py` — 流式事件状态机

**文件：**
- 修改：`streaming.py`
- 测试：`tests/test_streaming.py`

**接口：**

```python
class StreamState:
    content: str                        # 已显示的完整文本（含轮次间空行）
    blocks: list[dict[str, object]]     # 已完成的思考块
    def apply(self, kind: str, text: str, timestamp: float) -> list[tuple[str, object]]: ...
    def finish(self, timestamp: float) -> list[tuple[str, object]]: ...
```

动作（UI 侧据此操作控件）：

| action | payload | 含义 |
| --- | --- | --- |
| `begin_reasoning` | `None` | 本轮第一个 reasoning 增量，创建思考块 |
| `reasoning` | `str` | 追加思考正文 |
| `finish_reasoning` | `{"text": str, "seconds": float}` | 折叠为 `Thought` |
| `content` | `str` | 追加回答文本（可能带 `"\n\n"` 前缀） |

- [ ] **步骤 1：编写失败的测试**

```python
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
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_streaming.py -q`
预期：FAIL，`ImportError: cannot import name 'StreamState'`

- [ ] **步骤 3：实现**

`apply` 按 kind 分派：

- `reasoning`：无进行中块时开块、记 `started = timestamp`，产出 `begin_reasoning`；累加 `current`；`last = timestamp`；产出 `reasoning`。
- `content`：若存在进行中块 → 先按 `max(1.0, last - started)` 收块（追加进 `blocks`，产出 `finish_reasoning`）；若 `content` 非空且 `separator_pending` → 前缀 `"\n\n"`，复位标志；追加到 `self.content`；产出 `content`。
- `round`：收掉进行中块（若上一轮已有 content 则 `separator_pending = True`）；无进行中块时返回 `[]`。
- 其他 kind：忽略，返回 `[]`。

`finish(timestamp)`：有进行中块时按 `max(1.0, last - started)` 收块并返回 `[("finish_reasoning", ...)]`，否则 `[]`。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_streaming.py -q`
预期：PASS（24 passed）

- [ ] **步骤 5：Commit**

```bash
git add streaming.py tests/test_streaming.py
git commit -m "feat(streaming): add per-round thinking state machine"
```

---

## 任务 4：会话持久化 `reasoning_blocks`

**文件：**
- 修改：`sessions.py:65-109`
- 测试：`tests/test_sessions.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_new_message_stores_reasoning_blocks(self) -> None:
    message = new_message(
        "assistant", "答案", display="答案",
        reasoning_blocks=[{"text": "想", "seconds": 3.0}],
    )
    self.assertEqual(message["reasoning_blocks"], [{"text": "想", "seconds": 3.0}])


def test_new_message_omits_empty_reasoning_blocks(self) -> None:
    self.assertNotIn("reasoning_blocks", new_message("assistant", "答案"))


def test_build_api_messages_never_sends_reasoning_blocks(self) -> None:
    session = {"messages": [new_message(
        "assistant", "答案", reasoning_blocks=[{"text": "想", "seconds": 1.0}],
    )]}
    self.assertNotIn("reasoning_blocks", build_api_messages(session)[0])
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_sessions.py -q`
预期：FAIL，`TypeError: new_message() got an unexpected keyword argument 'reasoning_blocks'`

- [ ] **步骤 3：实现**

`new_message` 参数表末尾加 `reasoning_blocks: list[dict[str, object]] | None = None`；构造 `message` 后：

```python
if reasoning_blocks:
    message["reasoning_blocks"] = [dict(block) for block in reasoning_blocks]
```

`build_api_messages` 不动（验证它本来就不会复制该字段）。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_sessions.py -q`
预期：PASS（23 passed）

- [ ] **步骤 5：Commit**

```bash
git add sessions.py tests/test_sessions.py
git commit -m "feat(sessions): persist assistant reasoning blocks"
```

---

## 任务 5：`ai_client` 流式请求与自动回退

**文件：**
- 修改：`ai_client.py`（`__init__` 535 附近、`complete` 2292、`complete_with_failure_fallback` 2302、`_complete_message` 2321、`_complete_message_once` 2346）
- 测试：`tests/test_ai_client.py`

**接口：**

```python
StreamEventCallback = Callable[[str, str], None]

def complete(self, messages, *, on_event: StreamEventCallback | None = None,
             should_stop: Callable[[], bool] | None = None) -> str
def complete_with_failure_fallback(self, messages, *, failure_reason=None,
                                   on_event=None, should_stop=None) -> str
def _complete_message(self, messages, *, tools=None, tool_choice=None,
                      on_event=None, should_stop=None) -> dict
def _complete_message_once(self, messages, *, tools=None, tool_choice=None,
                           reasoning_out: list[str] | None = None) -> dict
def _build_request_payload(self, messages, *, tools, tool_choice) -> tuple[dict, str, bool]
def _parse_complete_body(self, body: bytes, *, use_responses: bool,
                         reasoning_out: list[str]) -> dict
def _complete_message_once_stream(self, messages, *, tools, tool_choice, on_event,
                                  should_stop, emitted: list[bool]) -> dict
```

- [ ] **步骤 1：编写失败的测试**

```python
class FakeStreamResponse:
    def __init__(self, lines, content_type="text/event-stream"):
        self.lines = [line.encode("utf-8") for line in lines]
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def __iter__(self):
        return iter(self.lines)


class StreamRequestTests(unittest.TestCase):
    def test_stream_request_emits_events_then_returns_message(self) -> None:
        body = [
            'data: {"choices":[{"delta":{"reasoning_content":"想"}}]}\n',
            "\n",
            'data: {"choices":[{"delta":{"content":"答案"}}]}\n',
            "\n",
            "data: [DONE]\n",
            "\n",
        ]
        events = []
        with patch("ai_client.urllib.request.urlopen",
                   return_value=FakeStreamResponse(body)) as urlopen:
            reply = AIClient({"api_key": "k", "base_url": "https://x.test/v1",
                              "model": "m"}).complete(
                [{"role": "user", "content": "你好"}],
                on_event=lambda kind, text: events.append((kind, text)),
            )
        self.assertEqual(reply, "答案")
        self.assertEqual(events, [("reasoning", "想"), ("content", "答案")])
        self.assertTrue(json.loads(urlopen.call_args.args[0].data)["stream"])

    def test_non_stream_body_falls_back_and_replays_once(self) -> None:
        body = json.dumps({"choices": [{"message": {
            "content": "答案", "reasoning_content": "想"}}]}).encode("utf-8")
        events = []
        client = AIClient({"api_key": "k", "base_url": "https://x.test/v1", "model": "m"})
        with patch("ai_client.urllib.request.urlopen",
                   return_value=FakeResponse(json.loads(body))) as urlopen:
            reply = client.complete([{"role": "user", "content": "你好"}],
                                    on_event=lambda k, t: events.append((k, t)))
        self.assertEqual(reply, "答案")
        self.assertEqual(events, [("reasoning", "想"), ("content", "答案")])
        self.assertTrue(client._streaming_disabled)
        self.assertEqual(urlopen.call_count, 1)

    def test_http_400_disables_streaming_and_retries_non_stream(self) -> None:
        error = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error":{"message":"no stream"}}'))
        client = AIClient({"api_key": "k", "base_url": "https://x.test/v1", "model": "m"})
        events = []
        responses = [error, FakeResponse({"choices": [{"message": {"content": "兜底"}}]})]
        with patch("ai_client.urllib.request.urlopen", side_effect=responses):
            reply = client.complete([{"role": "user", "content": "你好"}],
                                    on_event=lambda k, t: events.append((k, t)))
        self.assertEqual(reply, "兜底")
        self.assertEqual(events, [("content", "兜底")])
        self.assertTrue(client._streaming_disabled)

    def test_no_retry_after_partial_content(self) -> None:
        class BreakingStream(FakeStreamResponse):
            def __iter__(self):
                yield 'data: {"choices":[{"delta":{"content":"半"}}]}\n'.encode("utf-8")
                raise OSError("connection reset")

        client = AIClient({"api_key": "k", "base_url": "https://x.test/v1", "model": "m"})
        with patch("ai_client.urllib.request.urlopen",
                   return_value=BreakingStream([])) as urlopen, \
             patch("ai_client.time.sleep"):
            with self.assertRaises(RuntimeError):
                client.complete([{"role": "user", "content": "你好"}],
                                on_event=lambda k, t: None)
        self.assertEqual(urlopen.call_count, 1)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_ai_client.py -k Stream -q`
预期：FAIL，`TypeError: complete() got an unexpected keyword argument 'on_event'`

- [ ] **步骤 3：实现**

1. `__init__` 加 `self._streaming_disabled = False`（放在 `_validate_subagents` 之后）。
2. 把 `_complete_message_once` 的 payload 构造（2353-2369）抽成 `_build_request_payload(messages, *, tools, tool_choice)`，返回 `(payload, endpoint, use_responses)`，原方法改为调用它，行为不变。
3. `_complete_message_once` 增加 `reasoning_out`：Chat 分支在 `return response_message` 前 `reasoning_out.append(chat_reasoning_text(response_message))`；Responses 分支 `reasoning_out.append(responses_reasoning_text(payload))`。
4. `_complete_message`：`on_event is None or self._streaming_disabled` → 走原有重试循环（逐字节不变）；否则走流式循环：每轮新建 `emitted=[False]`，`RuntimeError` 时若 `emitted[0]` 为真直接 `raise`，否则照旧重试。
5. `_complete_message_once_stream`：
   - 用 `_build_request_payload`，`payload["stream"] = True`；
   - `HTTPError`：code ∈ `{400, 404, 415, 501}` → `_streaming_disabled = True`，调用 `_complete_message_once(..., reasoning_out=...)`，按 reasoning → content 顺序回调并置 `emitted[0]`，返回结果；其他 code 沿用原错误文案；
   - `Content-Type` 不含 `event-stream` → 读完整 body、按普通 JSON 解析（Responses 走 `parse_response`，Chat 取 `choices[0].message`）、禁用流式、一次性回调、返回；
   - 否则按 `use_responses` 选累加器，`for name, data in iter_sse(response)`；每收到非空增量置 `emitted[0] = True` 并 `on_event(kind, text)`；`should_stop()` 为真则 `break`；
   - 一个增量都没收到 → `RuntimeError("AI 服务返回了空的流式响应。")`；
   - Chat 返回 `stream.message()`；Responses 要求 `final_payload` 非空，交给 `responses_adapter.parse_response`；
   - `finish_reason == "length"` 且有 content → 照常返回（保留已显示文本）；无 content → 抛错。
6. `complete` / `complete_with_failure_fallback` 增加 `on_event`、`should_stop` 并透传。
7. 流循环内的读取异常（`OSError`、`UnicodeDecodeError`）统一包成 `RuntimeError("AI 服务流式连接中断。")`；`emitted[0]` 已为真时 `_complete_message` 直接上抛，不再重试。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_ai_client.py -q`
预期：PASS（原有 100+ 项 + 新增 4 项全绿）

- [ ] **步骤 5：Commit**

```bash
git add ai_client.py tests/test_ai_client.py
git commit -m "feat(ai_client): stream chat and responses deltas with fallback"
```

---

## 任务 6：工具循环接线（`round` 事件与取消）

**文件：**
- 修改：`ai_client.py:1221-1293`
- 测试：`tests/test_ai_client.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_media_skills_emits_round_per_model_call(self) -> None:
    client = AIClient({"api_key": "k", "base_url": "https://x.test/v1", "model": "m"})
    rounds = []
    with patch.object(client, "_complete_message",
                      side_effect=[{"role": "assistant", "content": "好"}]):
        client.complete_with_media_skills(
            [{"role": "user", "content": "在吗"}],
            on_event=lambda kind, text: rounds.append(kind),
        )
    self.assertEqual(rounds, ["round"])


def test_media_skills_forwards_should_stop(self) -> None:
    client = AIClient({"api_key": "k", "base_url": "https://x.test/v1", "model": "m"})
    seen = {}
    def fake_complete(messages, **kwargs):
        seen.update(kwargs)
        return {"role": "assistant", "content": "好"}
    with patch.object(client, "_complete_message", side_effect=fake_complete):
        client.complete_with_media_skills(
            [{"role": "user", "content": "在吗"}], should_stop=lambda: True,
            on_event=lambda kind, text: None,
        )
    self.assertIs(seen["should_stop"](), True)
    self.assertIsNotNone(seen["on_event"])
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_ai_client.py -k rounds or forwards -q`
预期：FAIL，`TypeError: complete_with_media_skills() got an unexpected keyword argument 'on_event'`

- [ ] **步骤 3：实现**

`complete_with_media_skills` 签名末尾加 `on_event: StreamEventCallback | None = None`。循环体内、调用 `_complete_message` 之前：

```python
if on_event is not None:
    on_event("round", "")
```

并把 `on_event=on_event, should_stop=should_stop` 传给 `_complete_message`。`should_stop` 为空时传 `None`。

媒体拒绝兜底 `complete_with_media_fallback` 保持非流式（它返回替代文案时 UI 已有内容）。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_ai_client.py -q`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add ai_client.py tests/test_ai_client.py
git commit -m "feat(ai_client): emit round events and forward cancellation"
```

---

## 任务 7：`ThinkingBlock` 组件

**文件：**
- 修改：`main.py`（模块常量 110-119 之后、`class PranaApp` 之前）
- 测试：`tests/test_main_messages.py`（仅导入与纯静态校验）

- [ ] **步骤 1：编写失败的测试**

```python
def test_thinking_block_is_available(self) -> None:
    import main
    assert hasattr(main, "ThinkingBlock")
    assert main.THINKING_LINE == "#3a3a4a"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_main_messages.py -q`
预期：FAIL，`AssertionError`

- [ ] **步骤 3：实现**

先在 `main.py` 顶部 import 区加 `import streaming`。

模块常量：`THINKING_LINE = "#3a3a4a"`、`THINKING_ICON_ACTIVE = "✳"`、`THINKING_ICON_COLLAPSED = "▸"`、`THINKING_ICON_EXPANDED = "▾"`。

```python
class ThinkingBlock:
    def __init__(self, parent: tk.Misc, wraplength: int) -> None: ...
    def begin(self) -> None: ...
    def append(self, text: str) -> None: ...
    def finish(self, seconds: float) -> None: ...
    def toggle(self) -> None: ...
    def set_wraplength(self, value: int) -> None: ...
    def destroy(self) -> None: ...
    labels: list[tuple[tk.Label, int]]
```

布局：`frame(bg=BACKGROUND)` → `header(bg=BACKGROUND)` 内 `icon`/`title`/`subtitle`（Segoe UI 9，MUTED，title 半粗）；`body_frame` 内 2px `bar`（`THINKING_LINE`，`fill=tk.Y`）+ `body_label`（Segoe UI 9，MUTED，`justify=LEFT`，`wraplength`）。header 与 frame 绑 `<Button-1>` 到 `toggle`，`cursor="hand2"`；标题 hover 转 `TEXT`（`<Enter>`/`<Leave>`）。

- `begin()`：`title="Thinking"`、`icon=✳`、`body_frame.pack(fill=tk.X)`、启动 `after(90)` 微光（`shimmer_colors(MUTED, TEXT)` 往返索引；`finish`/`destroy` 取消）。
- `append(text)`：拼接内部 `self.text`，`body_label.configure(text=self.text.strip())`，`subtitle.configure(text=reasoning_heading(self.text))`。
- `finish(seconds)`：停微光、`title="Thought"`、`subtitle=format_reasoning_duration(seconds)`、`icon=▸`、`body_frame.pack_forget()`、`self._finished = True`；若 `self.text` 含 code fence，按 `split_message_segments` 重建 `body_frame` 内容（code 用 `CODE_BACKGROUND` + `Consolas 9`），并把新 label 写入 `self.labels`（`(label, 30)`）。
- `toggle()`：已映射则 `pack_forget()` + `▸`，否则 `pack(fill=tk.X)` + `▾`。
- `set_wraplength(value)`：`body_label.configure(wraplength=max(200, value - 70))`，并对 code label 用 `max(180, value - 90)`。
- `labels` 初始含 `(subtitle, 0)` 与 `(body_label, 70)`；`finish` 重建后替换为 code/text label 列表。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_main_messages.py -q`
预期：PASS

- [ ] **步骤 5：手动看一眼观感**

运行：`python -c "import main; print(main.ThinkingBlock)"`（无 Tk 实例化，只验导入）。
预期：打印 `<class 'main.ThinkingBlock'>`

- [ ] **步骤 6：Commit**

```bash
git add main.py tests/test_main_messages.py
git commit -m "feat(ui): add collapsible thinking block widget"
```

---

## 任务 8：渲染已完成思考块

**文件：**
- 修改：`main.py:3994-4071`（`_add_message`）、`main.py:2152-2179`（`_add_message_from_record`）、`main.py:4488-4492`（`_resize_messages`）
- 测试：`tests/test_main_messages.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_add_message_accepts_reasoning_blocks_signature(self) -> None:
    import inspect
    import main
    params = inspect.signature(main.PranaApp._add_message).parameters
    assert "reasoning_blocks" in params
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_main_messages.py -q`
预期：FAIL，`AssertionError`

- [ ] **步骤 3：实现**

`_add_message` 增加 `reasoning_blocks: list[dict[str, object]] | None = None`：

```python
blocks = streaming.normalize_reasoning_blocks(reasoning_blocks)
container = row          # 默认直接把 bubble 放进 row
if blocks:
    container = tk.Frame(row, bg=BACKGROUND)
    container.pack(side=tk.LEFT, anchor=tk.W)
    for block in blocks:
        widget = ThinkingBlock(container, wraplength)
        widget.append(str(block["text"]))
        widget.finish(float(block["seconds"]))
        widget.frame.pack(fill=tk.X, pady=(0, 4))
        self.message_text_labels.extend(widget.labels)
bubble = tk.Frame(container, bg=bubble_bg, padx=14, pady=10)
```

其余渲染逻辑不变（`bubble.pack(side=tk.RIGHT if role == "user" else tk.LEFT)`）。

`_add_message_from_record`：`reasoning_blocks=message.get("reasoning_blocks")` 传给 `_add_message`。

`_resize_messages`：改成逐项 `try/except tk.TclError`，并把存活项写回列表：

```python
alive = []
for label, offset in self.message_text_labels:
    try:
        label.configure(wraplength=max(200, wraplength - offset))
    except tk.TclError:
        continue
    alive.append((label, offset))
self.message_text_labels[:] = alive
```

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_main_messages.py -q`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add main.py tests/test_main_messages.py
git commit -m "feat(ui): render persisted reasoning blocks above replies"
```

---

## 任务 9：流式视图与增量渲染

**文件：**
- 修改：`main.py`（`__init__` 184 附近、`_process_request` 3659 之前、`_show_streamed_message` 3734 之后）

- [ ] **步骤 1：实现 `_StreamView`（模块级 dataclass）**

```python
@dataclass
class _StreamView:
    request_id: int
    session_id: str
    user_message_id: str
    buffer: streaming.StreamBuffer
    state: streaming.StreamState
    row: tk.Frame | None = None
    column: tk.Frame | None = None
    bubble: tk.Frame | None = None
    label: tk.Label | None = None
    thinking: ThinkingBlock | None = None
    finalized: bool = False
```

`__init__` 增加 `self.stream_views: dict[int, _StreamView] = {}`（用 `self.state_lock` 保护）。

- [ ] **步骤 2：工作线程侧接线**

在 `_process_request` 的 `reply, generated_media = (`（3659）之前：

```python
view = self._new_stream_view(request)
...
on_event=on_event,
should_stop=request.cancel_event.is_set,
```

新增方法：

```python
def _new_stream_view(self, request: ChatRequest) -> _StreamView:
    view = _StreamView(request.request_id, request.session_id,
                       request.user_message_id, streaming.StreamBuffer(),
                       streaming.StreamState())
    with self.state_lock:
        self.stream_views[request.request_id] = view
    return view

def _stream_event_callback(self, view: _StreamView) -> Callable[[str, str], None]:
    def on_event(kind: str, text: str) -> None:
        view.buffer.push(kind, text)
        if view.buffer.request_wakeup():
            self._post_to_ui(lambda current=view: self._pump_stream(current))
    return on_event
```

- [ ] **步骤 3：行与气泡的懒创建**

```python
def _wraplength(self) -> int:            # 复用 _add_message 的口径
def _dismiss_welcome(self) -> None:      # 从 _add_message 抽出的 2 行
def _ensure_stream_row(self, view: _StreamView) -> None:
def _ensure_stream_bubble(self, view: _StreamView) -> None:
```

`_wraplength`：`max(240, self.canvas.winfo_width() - 180)`。
`_ensure_stream_row`：已存在则返回；`follow = should_follow(self.canvas.yview()[1])`；创建 `row`（`bg=BACKGROUND, pady=6`，`pack(fill=tk.X)`）与 `column`（`bg=BACKGROUND`，`pack(side=tk.LEFT, anchor=tk.W)`）；调用 `_dismiss_welcome()`；`follow` 为真时 `after_idle(yview_moveto(1.0))`。
`_ensure_stream_bubble`：已存在则返回；`bubble = tk.Frame(view.column, bg=SURFACE, padx=14, pady=10)`（`pack(anchor=tk.W, pady=(4, 0))`）；`label = tk.Label(bubble, text="", bg=SURFACE, fg=TEXT, justify=tk.LEFT, anchor=tk.W, wraplength=self._wraplength(), font=("Segoe UI", 10))`（`pack(anchor=tk.W, pady=(2, 2))`）；`self.message_text_labels.append((label, 0))`；`view.label = label`。

- [ ] **步骤 4：UI 线程侧泵与事件应用**

```python
def _pump_stream(self, view: _StreamView) -> None:
    if self.closing or view.finalized:
        return
    events, _closed = view.buffer.drain()
    for kind, text, timestamp in events:
        for action, payload in view.state.apply(kind, text, timestamp):
            self._apply_stream_action(view, action, payload)

def _apply_stream_action(self, view, action: str, payload: object) -> None:
    ...
```

`_apply_stream_action` 规则：

- `begin_reasoning`：`self._ensure_stream_row(view)`；创建 `ThinkingBlock(view.column, self._wraplength())`，`pack(fill=tk.X, pady=(0, 4))`，`begin()`，存入 `view.thinking`。
- `reasoning`：`view.thinking.append(str(payload))`。
- `finish_reasoning`：`view.thinking.finish(float(payload["seconds"]))`；`self.message_text_labels.extend(view.thinking.labels)`；`view.thinking = None`。
- `content`：`self._ensure_stream_row(view)`、`self._ensure_stream_bubble(view)`，`view.label.configure(text=view.state.content)`；若 `should_follow(self.canvas.yview()[1])` 则 `after_idle(yview_moveto(1.0))`。

- [ ] **步骤 5：手动验证**

运行：`python main.py`，发一条会触发工具的消息。
预期：思考块先出现并逐段增长，`✳` 呼吸；内容开始后折叠为 `▸ Thought Xs`；回答逐字增长；工具轮的思考块依次堆叠。

- [ ] **步骤 6：Commit**

```bash
git add main.py
git commit -m "feat(ui): render live streamed thinking and reply text"
```

---

## 任务 10：收尾、停止与失败中断

**文件：**
- 修改：`main.py`（`_handle_reply` 3746、`_stop_generation` 3947、`_process_request` 3686 异常分支）
- 测试：`tests/test_main_messages.py`（签名/静态）

- [ ] **步骤 1：实现 `_finalize_stream`**

```python
def _finalize_stream(self, view: _StreamView) -> tuple[str, list[dict[str, object]]]:
    if view.finalized:
        return view.state.content, list(view.state.blocks)
    view.finalized = True
    events, _ = view.buffer.drain()
    for kind, text, timestamp in events:
        for action, payload in view.state.apply(kind, text, timestamp):
            self._apply_stream_action(view, action, payload)
    for action, payload in view.state.finish(time.monotonic()):
        self._apply_stream_action(view, action, payload)
    with self.state_lock:
        if self.stream_views.get(view.request_id) is view:
            self.stream_views.pop(view.request_id, None)
    if view.row is not None:
        view.row.destroy()
        view.row = None
    return view.state.content, list(view.state.blocks)
```

**注意**：这里故意销毁流式行，由调用方用 `_add_message` 按最终文本 + `reasoning_blocks` 重建，保证流式与重载后的渲染逐像素一致。

- [ ] **步骤 2：改造 `_handle_reply`**

在 `active = ...` 之前：

```python
with self.state_lock:
    view = self.stream_views.get(request.request_id)
display, blocks = self._finalize_stream(view) if view else ("", [])
```

分支改为：

- `error is not None` 且 `display` 为空 → 现有错误气泡不变。
- `reply.strip()` 非空 → `final_display = display or reply`；`new_message("assistant", reply, display=final_display, media_paths=..., reasoning_blocks=blocks or None)`；渲染 `_add_message("assistant", final_display, media_paths=..., reasoning_blocks=blocks or None)`。
- `reply` 为空但 `display` 非空 → 落盘 `new_message("assistant", "", display=display, reasoning_blocks=blocks or None, in_api=False)`，渲染 `_add_message("assistant", display, reasoning_blocks=blocks or None)`。
- 仅媒体 → 现有逻辑，`reasoning_blocks` 照带。

- [ ] **步骤 3：改造 `_stop_generation`**

每个 pending request 先收尾：

```python
with self.state_lock:
    view = self.stream_views.get(request.request_id)
display, blocks = self._finalize_stream(view) if view else ("", [])
if display:
    record = new_message("assistant", display, display=display,
                         reasoning_blocks=blocks or None, in_api=False)
    self._append_session_message(request.session_id, record,
                                 after_user_message_id=request.user_message_id)
    if self.active_session_id == request.session_id:
        self._add_message("assistant", display, reasoning_blocks=blocks or None)
```

之后再照旧追加 `STOPPED_MESSAGE`。

- [ ] **步骤 4：改造失败中断**

`_process_request` 的 `except Exception` 分支、调用 `complete_with_failure_fallback` 之前：

```python
self._post_to_ui(lambda current=view: self._commit_partial_stream(current))
fallback_view = self._new_stream_view(request)
```

`_commit_partial_stream(view)`：若 `view.finalized` 直接返回；`display, blocks = self._finalize_stream(view)`；`display` 为空则返回；写 `in_api=False` 的中断记录并渲染。

兜底调用传入 `on_event=self._stream_event_callback(fallback_view), should_stop=request.cancel_event.is_set`。UI 队列 FIFO 保证中断记录先于兜底内容出现。

- [ ] **步骤 5：运行全量测试**

运行：`python -m pytest tests -q`
预期：PASS，无失败

- [ ] **步骤 6：手动验证三条路径**

1. 正常流式：思考块 → 折叠 → 回答 → 重载会话后观感一致。
2. 生成中点「停止」：半截文本 + 思考块保留，追加「（已停止生成）」，重载后一致。
3. 把 base_url 改成不支持流式的中转 → 请求仍成功（一次性出现），控制台无异常。

- [ ] **步骤 7：Commit**

```bash
git add main.py tests/test_main_messages.py
git commit -m "feat(ui): finalize streams on reply, stop and failure"
```

---

## 任务 11：全量回归与文档

**文件：**
- 修改：`docs/prana-vs-opencode.md`（如已有"流式/思考"对比行则更新）
- 测试：全部

- [ ] **步骤 1：跑全量测试**

运行：`python -m pytest tests -q`
预期：全部通过，退出码 0

- [ ] **步骤 2：静态自检**

运行：`python -m compileall -q main.py ai_client.py sessions.py streaming.py`
预期：无输出

- [ ] **步骤 3：更新对比文档**

在 `docs/prana-vs-opencode.md` 相应小节补一行：Prana 现已支持流式输出与可折叠思考块（对齐 OpenCode 桌面端 v1 观感），并注明 `reasoning_blocks` 持久化、旧会话兼容。

- [ ] **步骤 4：Commit**

```bash
git add docs/prana-vs-opencode.md
git commit -m "docs: note streaming thinking support"
```

---

## 审查重点（Review Focus）

1. **中转服务忽略 `stream` 但返回 200 + application/json** → 必须自动回退并一次性显示完整内容，不能卡死、不能重复显示。（由任务 5 `test_non_stream_body_falls_back_and_replays_once` 钉住）
2. **流中途断流 / 只收到半截内容** → 已显示文本不回滚、不重复，错误冒泡到 `_handle_reply`。（由任务 5 `test_no_retry_after_partial_content` 钉住）
3. **工具轮次之间思考块反复开合** → 不能出现空思考块，也不能让 `Thinking` 一直不结束；第二轮思考必须是独立块。（由任务 3 `test_round_event_closes_block_without_content` 与 `test_second_round_gets_its_own_block_and_blank_line` 钉住）
4. **用户上滚阅读时新内容到达** → 不得强制跳到底部。（由任务 2 `test_should_follow` 钉住判定；`_ensure_stream_row` 只在开始时采样一次）
5. **停止生成时已有半截文本** → 必须落盘、重载后与停止瞬间一致，且不重复计入 API 上下文。（由任务 3 状态机测试保证内容不丢；`in_api=False` 走既有的 `build_api_messages` 过滤，已由 `tests/test_sessions.py` 中的 `in_api=False` 用例覆盖；最终行为由任务 10 步骤 5/6 手动验收确认）
