# 流式输出 + 思考过程 设计规格

日期：2026-11-01
参考：OpenCode 桌面端 v1（https://github.com/anomalyco/opencode，`v1.19.5`）

## 目标

1. **思考内容可见**：把模型返回的 reasoning / thinking 内容按 OpenCode 桌面端 v1 的观感渲染成一个可折叠块。
2. **真正的流式输出**：思考与回答都随 token 到达逐段显示，而不是等整个 JSON 返回后才出现。

两项必须同时成立：思考块的 `Thinking` 动态态只有流式才有意义。

## 现状

- `ai_client._complete_message_once` 用 `urllib.request.urlopen` 一次性读完整个响应体，`stream` 未开启，也没有 SSE 解析。
- Chat Completions 分支只取 `choices[0].message`；`reasoning_content`（DeepSeek/Kimi/Qwen）、`reasoning`（OpenRouter）从未被读取，直接丢弃。
- Responses 分支的 `parse_response` 在第 150 行显式 `continue` 掉 `reasoning` item（只保留 sidecar 的原始 output 用于回放）。
- `main._add_message` 只接受纯文本，按 code fence 分段渲染，没有可折叠控件。
- 取消生成时，已到达 UI 的内容不会落盘。

## 观感（对齐 OpenCode 桌面端 v1）

一次回复最多一个回答气泡；每个模型轮次的思考块依次排在气泡上方（先思考、后回答）。

> 实现约定：**每个模型轮次（round）一个思考块**。各轮可见文本按顺序拼接进同一个气泡。工具执行期间不显示 `Thinking`：上一轮的块在轮到 content/tool 时结束并折叠，下一轮的块在它的第一个 reasoning 增量到达时才出现。

### 三态

| 状态 | 标题 | 副标题 | 正文 | 图标 |
| --- | --- | --- | --- | --- |
| 思考中 | `Thinking` | 实时小标题（由思考正文推导） | 展开，灰色，实时追加 | `✳`，前景色呼吸闪烁 |
| 回答开始后 | `Thought` | 耗时，如 `7s`、`1m 12s` | 自动折叠 | `▸` |
| 用户点开 | `Thought` | 耗时 | 展开，灰色 Markdown/代码分段 | `▾` |

- 标题与副标题同一行，标题 `Segoe UI 9` 半粗，颜色 MUTED；副标题 MUTED，最多 40 字，超出以 `…` 截断。
- 正文左侧 2px 竖线（`#3a3a4a`），左内边距 10，字体 `Segoe UI 9`，颜色 MUTED；代码块沿用 `CODE_BACKGROUND` + `Consolas 9`。
- 点击整行（标题 + 副标题）切换展开/折叠，鼠标指针 `hand2`，悬停时标题转 TEXT 色。
- 无 reasoning 内容时不渲染该块，也不留空白。
- 完成后默认折叠。生成期间默认展开。
- **计时口径（每个块独立）**：从该轮第一个 reasoning 增量到达 UI 起，到该轮最后一个 reasoning 增量（或该轮结束）为止；不含等待首 token、不含工具执行。时间戳由 `on_event` 回调所在线程用 `time.monotonic()` 记录（与网络回调同线程，不经过 40ms 泵）。`>= 60s` 显示 `Xm Ys`，否则 `Xs`，下限 `1s`。
- **块结束时机**：该轮第一个 `content` 增量到达时立即折叠为该轮的 `Thought`；若该轮只有 reasoning + tool_calls 而没有 content，则在收到下一个 `round` 事件或请求结束时结束。

### 实时小标题

与 OpenCode TUI 的 `reasoningSummary()` 一致：

1. 匹配 `^\*\*([^*\n]+)\*\*(?:\r?\n\r?\n|$)` → 取第 1 组；
2. 否则取第一段非空行，去掉行内 `` ` ``、`*`、`#`，截断 40 字；
3. 都没有则副标题留空。

## 协议层设计

新增纯逻辑模块 `streaming.py`，不依赖 Tk 与 urllib，可单独测试。

### SSE 解析

```python
def iter_sse(lines: Iterable[bytes | str]) -> Iterator[tuple[str, dict]]:
    """逐行解析 SSE；返回 (event_name, data)。注释行与 event: 之外的字段忽略。"""
```

- 兼容 `\r\n` / `\n`；`data:` 多行按 SSE 规范用 `\n` 拼接。
- `data: [DONE]` 结束迭代。
- JSON 解析失败 → `RuntimeError("AI 服务返回了无法解析的流式数据。")`。

### Chat Completions 累加器

```python
class ChatStream:
    def feed(self, chunk: dict) -> list[tuple[str, str]]: ...
    content: str
    reasoning: str
    tool_calls: list[dict]   # 按 index 合并 id/name/arguments
    finish_reason: str | None
```

- 只读 `choices[0].delta`；`delta.content` → `("content", text)`。
- reasoning 字段按以下顺序取第一个为str 的：`reasoning_content`、`reasoning`、`thinking` → `("reasoning", text)`。
- `delta.tool_calls` 按 `index` 累积：`id` 取首次出现值，`function.name` 取首次，`function.arguments` 逐段拼接；`type` 缺省 `function`。
- `finish_reason` 记录最后一次非空值；`length` 视为未完成，交给错误分支。

### Responses 累加器

```python
class ResponsesStream:
    def feed(self, name: str, data: dict) -> list[tuple[str, str]]: ...
    final_payload: dict | None
```

- `response.reasoning_summary_text.delta` / `response.reasoning_text.delta` → `("reasoning", data["delta"])`。
- `response.output_text.delta` → `("content", data["delta"])`。
- `response.completed` → 保存 `data["response"]` 作为最终 payload（工具调用与加密 reasoning 从完整 output 取，不从 delta 拼）。
- `response.failed` / `response.incomplete` / `error` → RuntimeError，消息里带上安全的 reason/status，不拼接原始 output。

### 事件类型

`(kind, text)`，kind 取值：

| kind | 含义 |
| --- | --- |
| `reasoning` | 思考增量 |
| `content` | 回答增量 |
| `round` | 新的一轮模型调用开始（tool-calling 循环用），无文本 |

## 客户端层（ai_client.py）

- `_complete_message(..., on_event=None)`、`_complete_message_once(..., on_event=None)` 增加可选回调，签名 `Callable[[str, str], None]`。
- `complete_with_media_skills(..., on_event=None)`：每轮 `_complete_message` 前发 `("round", "")`，回调原样透传给本轮的流式请求。
- `complete_with_failure_fallback(..., on_event=None)`：兜底回复同样流式。
- 新增 `_complete_message_once_stream(messages, tools, tool_choice, on_event)`：
  1. 请求体在原有基础上加 `"stream": True`；
  2. `urlopen(..., timeout=60)` 后逐行迭代响应；
  3. 每个增量立即 `on_event(kind, text)`；
  4. 结束后组装 assistant message：
     - Chat：`{"role": "assistant", "content": 全文或 None, "tool_calls": [...]}`；
     - Responses：`responses_adapter.parse_response(final_payload, ...)`，保留 sidecar 回放；
  5. 空回复仍按原逻辑抛 `RuntimeError`（由重试循环处理）。
- **回退**：出现以下情况时抛内部异常 `StreamUnsupported`，把 `self._streaming_disabled` 置 True 并改走原有非流式路径，同时把完整结果通过 `on_event` 一次性发出：
  - HTTP 400 / 404 / 415 / 501；
  - Content-Type 不是 `text/event-stream` 且响应体能按普通 JSON 解析出完整结果；
  - 一条增量都没有收到就 EOF。
  - 发送顺序固定为 `reasoning` → `content`，保证 UI 的"首个 content 触发折叠"逻辑成立。
  - Responses 非流式回退时，思考文本由 `_complete_message_once` 从 `payload["output"]` 中 `type == "reasoning"` 项的 `summary[].text` / `content[].text` 提取；`responses_adapter.parse_response` 的契约不变，仍不返回 reasoning。
- **重试**：沿用 `MAX_CHAT_REQUEST_ATTEMPTS`。若本轮已经发出过任何增量，则不重试（避免重复文本），直接把错误抛给上层。
- **取消**：流循环内检查 `should_stop()`（由 `complete_with_media_skills` 外层传入的同一回调），为真则结束读取并返回已累积内容。

## 文本归属与失败路径

- 屏幕上看到的文本（`_StreamBuffer.content` 的各轮拼接）是 `display` 的唯一来源，保证重载会话后与流式过程一致。
- 最终 assistant 记录的 `content` 只用最后一轮的 `reply`：中间轮的文本已经作为 `visible=False` 的 tool-exchange 记录进入 API 历史，重复写入会污染上下文。
- `content` 为空但 `display` 非空（例如只调用了工具）→ `in_api=False`，`display` 照常保留。
- 主请求抛错进入 `complete_with_failure_fallback` 之前，先把当前流式视图收尾为一条 **中断记录**（`in_api=False`，仅当已有文本时写入），然后为兜底回复开一个全新的流式视图与 buffer。用户看到的顺序是：半截文本（中断）→ 兜底回复，而不是两段拼在一起。

## UI 层（main.py）

### 纯逻辑放 `streaming.py`

- `reasoning_heading(text) -> str`
- `format_reasoning_duration(seconds: float) -> str`

### 思考块组件

`main.py` 模块级类 `ThinkingBlock`：

```python
class ThinkingBlock:
    def __init__(self, parent, wraplength, palette): ...
    def append(self, text: str) -> None      # 追加正文，更新副标题
    def begin(self) -> None                  # ✳ Thinking + 启动微光
    def finish(self, seconds: float) -> None # ▸ Thought + 耗时 + 折叠
    def toggle(self) -> None
    def set_wraplength(self, value: int) -> None
    labels: list[tk.Label]                   # 供 _resize_messages 注册
```

- 微光用 `root.after(90, ...)` 循环切换标题 fg，颜色在 MUTED → TEXT → MUTED 间取预计算阶梯；`finish`/`destroy` 取消 after 回调。
- 正文流式期用单个 Label 逐次 `configure(text=...)`；`finish` 时按 `split_message_segments` 重建（代码块用等宽样式）。
- 标题行 `cursor="hand2"`，绑定 `<Button-1>` 折叠。

### 消息渲染

`_add_message` 增加参数：

```python
reasoning_blocks: list[dict[str, object]] | None = None,
```

- 有 `reasoning_blocks` 时先在上方依次插入 `ThinkingBlock`（已完成态，折叠），再渲染 bubble。
- 注册每个 `ThinkingBlock.labels` 到 `self.message_text_labels`，`_resize_messages` 一并更新 wraplength。

`_add_message_from_record` 透传 `message.get("reasoning_blocks")`。

### 流式视图

新增 `_StreamView`（dataclass，UI 线程持有）与 `_StreamBuffer`（线程安全，工作线程写入）：

```python
@dataclass
class _StreamBuffer:
    lock: threading.Lock
    reasoning: list[str]     # 当前轮未消费的 reasoning 增量
    content: list[str]       # 当前轮未消费的 content 增量
    round_started: bool      # 收到过 round 事件且尚未消费
    closed: bool
```

- `_process_request` 创建 buffer 与视图占位；`on_event` 写入 buffer 并在消费线程记录 `time.monotonic()`（`reasoning`/`round` 用于计时），再通过 `_post_to_ui` 唤醒一次泵（同一时刻最多一个待处理唤醒）。
- `_pump_stream(request_id)`（每 40ms）：
  - 取走 buffer 增量；
  - 遇到 `round`：若当前思考块仍处于 `Thinking`，先 `finish()`；
  - 当前轮还没有思考块且有 reasoning → 创建 `ThinkingBlock` 并 `begin()`，记录本轮首/末增量时间；
  - `content` → 首次出现时 `finish()` 当前思考块（本轮结束），再创建或更新 live 气泡（单 Label 覆盖写累计文本）；轮次之间插入空行；
  - 思考块 `append`，副标题随正文更新；
  - 接近底部时自动滚到底部（`_is_near_bottom`）。
- `_handle_reply` 在 `active` 时调用 `_finalize_stream(request, reply, media, error)`：
  - 用 buffer 的累计文本替换 live 气泡内容，按 `split_message_segments` 重建（消除纯文本流式与代码块渲染的差异）；
  - 补齐 media、重试按钮、右键复制菜单；
  - 若流式期间没收到任何内容（回退/失败路径），退回原有 `_add_message`。
- 多个 tool round 的可见文本直接追加在同一气泡中（轮次之间空一行），与 OpenCode 的连续性一致；思考块按轮次依次堆叠在气泡上方。
- 每个已完成思考块的 `{"text", "seconds"}` 收进 `_StreamView.reasoning_blocks`，供落盘与 `_finalize_stream` 使用。

### 取消

`_stop_generation` 在追加 `STOPPED_MESSAGE` 前，先 `_finalize_stream(..., interrupted=True)`：

- 把已收到的部分文本落盘为 assistant 记录（`in_api=False`，避免污染上下文），`display` 保留；
- 思考块 `finish()`；
- 之后照旧追加 muted 的“已停止”记录。

## 会话持久化（sessions.py）

- `new_message(..., reasoning_blocks: list[dict[str, object]] | None = None)`；每个元素为 `{"text": str, "seconds": float}`，按轮次顺序保存。仅当列表非空时写入字段，避免旧会话/普通消息多出空字段。
- `build_api_messages` 不复制该字段，reasoning 永不回传给模型（DeepSeek 明确禁止 `reasoning_content` 入参，其他兼容服务也无需它）。
- `_handle_reply` 写最终 assistant 记录时带上 buffer 里已完成的思考块。
- 旧会话无该字段 → 渲染时按无思考块处理，不需要迁移。

## 非目标

- 不做 Markdown 语义渲染（表格、列表样式），沿用现有 code fence 分段。
- 不做逐字打字机节奏（OpenCode 的 `PacedMarkdown`），按网络到达节奏显示。
- 不做思考块显隐设置项；默认折叠，点击展开。
- 不展示与不持久化工具轮次的 reasoning 之外的内部细节（工具调用参数保持不可见）。
- 不改变 Responses sidecar 的加密 reasoning 回放逻辑。
- 头脑风暴参与者的思考内容不进入主对话 UI。

## 测试计划

新增 `tests/test_streaming.py`（unittest 风格，与仓库一致）：

- SSE 分块：一行被拆成两次 `read`、CRLF、注释行、多行 data、`[DONE]`。
- Chat 累加器：`reasoning_content` + `content` 混合；tool_calls 跨 chunk 拼接 id/name/arguments；`finish_reason`。
- Chat 累加器：`reasoning`（OpenRouter）与 `thinking` 字段。
- Responses 累加器：reasoning summary delta、output_text delta、completed 时取完整 output。
- `reasoning_heading`：`**标题**\n\n正文`、无标题、超长截断。
- `format_reasoning_duration`：<60s、>=60s、下限 1s。

扩展 `tests/test_ai_client.py`：

- 伪流式响应（逐行 `data:`）→ 断言回调序列与最终 message；
- 请求体含 `"stream": True`；
- HTTP 400 时自动回退非流式，且回调收到完整内容一次；
- 已发出增量后不重试（urlopen 只调用一次）。

扩展 `tests/test_sessions.py`：

- `new_message` 带 `reasoning_blocks`；`build_api_messages` 不含该字段。

## 风险

| 风险 | 处理 |
| --- | --- |
| 各家 SSE 格式差异大 | 只依赖标准 SSE 行格式；未知事件忽略；有完整回退路径 |
| 部分中转服务不支持 `stream` | 首次失败后整进程禁用流式，用户无感 |
| Tk 高频刷新卡顿 | 40ms 聚合泵 + 单 Label 覆盖写；每请求同时最多一个待处理 UI 回调 |
| 工具轮次间文本归属 | 同气泡顺序追加，最终记录 `display` 与所见一致 |
| 停止时内容丢失 | 停止前把部分文本显式落盘 |

## 实现状态

已实现（2026-11-01，分支 `feat/streaming-thinking`）：`streaming.py` 纯逻辑层、`ai_client` 流式请求与自动回退、`sessions` 的 `reasoning_blocks` 持久化、`main` 的 `ThinkingBlock` 折叠思考块与 `_StreamView` 增量渲染，以及回复/停止/失败三条收尾路径。

- 观感对齐 OpenCode 桌面端 v1：生成中 `Thinking` + 实时小标题微光，结束后折叠为 `▸ Thought Xs`，点击展开灰色思考正文。
- `reasoning_blocks` 随会话消息落盘，且**不进入 API 消息**（`build_api_messages` 不复制该字段）。
- 旧会话兼容：没有 `reasoning_blocks` 的历史消息按原样渲染，不出现空思考块。
