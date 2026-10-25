MAX_CHAT_MESSAGE_LENGTH = 4000

CHAT_SKILLS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "send_chat_message",
            "description": (
                "Send one short chat message to the user immediately, before the "
                "final reply. Use only when the user's request is better answered "
                "as several separate short messages, such as a brief acknowledgement "
                "or progress note followed by details. Do not use it to repeat the "
                "final reply or to send unrelated chatter."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The short message to show in the chat.",
                    }
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    },
]


def validate_chat_message_arguments(arguments: object) -> str:
    if not isinstance(arguments, dict) or set(arguments) != {"message"}:
        raise ValueError("发送消息参数无效。")
    message = arguments["message"]
    if not isinstance(message, str) or not message.strip():
        raise ValueError("发送的消息不能为空。")
    if len(message) > MAX_CHAT_MESSAGE_LENGTH:
        raise ValueError(
            f"发送的消息不能超过 {MAX_CHAT_MESSAGE_LENGTH} 个字符。"
        )
    return message.strip()
