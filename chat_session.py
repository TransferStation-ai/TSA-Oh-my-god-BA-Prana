import re
from typing import Any

MAX_HISTORY_CHARS = 32_000
MAX_HISTORY_MESSAGES = 80
IMAGE_PART_WEIGHT = 1_000

_CODE_FENCE_PATTERN = re.compile(r"```[ \t]*([^\n`]*)\n(.*?)```", re.DOTALL)


def split_message_segments(message: str) -> list[tuple[str, str]]:
    text = message.replace("\r\n", "\n").replace("\r", "\n")
    segments: list[tuple[str, str]] = []
    cursor = 0
    for match in _CODE_FENCE_PATTERN.finditer(text):
        before = text[cursor : match.start()].strip("\n")
        if before:
            segments.append(("text", before))
        code = match.group(2).rstrip("\n")
        if code:
            segments.append(("code", code))
        cursor = match.end()
    remaining = text[cursor:].strip("\n")
    if remaining:
        segments.append(("text", remaining))
    if not segments:
        segments.append(("text", text.strip("\n")))
    return segments


def message_weight(message: dict[str, Any]) -> int:
    content = message.get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                total += 1
            elif part.get("type") == "text" and isinstance(part.get("text"), str):
                total += len(part["text"])
            elif part.get("type") == "image_url":
                total += IMAGE_PART_WEIGHT
            else:
                total += 200
        return total
    return len(str(content))


def trim_history(
    messages: list[dict[str, Any]],
    *,
    max_chars: int = MAX_HISTORY_CHARS,
    max_messages: int = MAX_HISTORY_MESSAGES,
) -> list[dict[str, Any]]:
    if not messages:
        return []

    split_at = 0
    while split_at < len(messages) and messages[split_at].get("role") == "system":
        split_at += 1
    system_messages = list(messages[:split_at])
    conversation = list(messages[split_at:])
    if not conversation:
        return system_messages

    allowed_count = max(1, max_messages - len(system_messages))
    kept = [conversation[-1]]
    total = message_weight(conversation[-1])
    for message in reversed(conversation[:-1]):
        if len(kept) >= allowed_count:
            break
        weight = message_weight(message)
        if total + weight > max_chars:
            break
        kept.append(message)
        total += weight
    kept.reverse()
    while len(kept) > 1 and kept[0].get("role") != "user":
        kept.pop(0)
    return system_messages + kept


def insert_assistant_messages(
    history: list[dict[str, Any]],
    user_entry: dict[str, Any],
    contents: list[str],
) -> bool:
    index = next(
        (position for position, message in enumerate(history) if message is user_entry),
        None,
    )
    if index is None:
        return False
    insert_at = index + 1
    while (
        insert_at < len(history)
        and history[insert_at].get("role") == "assistant"
    ):
        insert_at += 1
    for content in contents:
        if not content.strip():
            continue
        history.insert(insert_at, {"role": "assistant", "content": content})
        insert_at += 1
    return True
