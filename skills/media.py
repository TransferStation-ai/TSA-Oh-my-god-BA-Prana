import json
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ai_client import AIClient


MEDIA_SKILLS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "send_local_media",
            "description": (
                "Ask the user to choose one or more existing local images or "
                "videos in the native file picker, then show the chosen files in "
                "the chat. Only call when the user explicitly asks to send a "
                "local image or video. Never search the computer or upload/read "
                "the selected files."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_web_image",
            "description": (
                "Download and show an image listed among the current message's "
                "webpage image candidates. Only call when the user explicitly "
                "asks to send or show an image from a webpage; do not use it to "
                "generate images or to fetch any other URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Exact URL of a listed webpage image.",
                    }
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_image",
            "description": (
                "Generate an image from the user's requested description and send "
                "it in the chat. Only call when the user explicitly asks to create "
                "or send an image; image generation may incur API charges."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detailed description of the image to create.",
                    }
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_video",
            "description": (
                "Generate a short video from the user's requested description and "
                "send it in the chat. Only call when the user explicitly asks to "
                "create or send a video; video generation may incur API charges."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detailed description of the video to create.",
                    }
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
        },
    },
]


def invoke_media_skill(
    client: "AIClient",
    name: str,
    arguments: object,
    *,
    local_media_selector: Callable[[], list[Path]] | None = None,
    webpage_image_urls: set[str] | None = None,
) -> tuple[str, list[Path]]:
    if name == "send_local_media":
        if not isinstance(arguments, dict) or arguments:
            raise ValueError("本地媒体 skill 不接受参数。")
        if local_media_selector is None:
            raise RuntimeError("当前界面无法选择本地媒体。")
        paths = local_media_selector()
        if not paths:
            raise RuntimeError("老师取消了本地媒体选择。")
        return (
            json.dumps(
                {"type": "local_media", "files": [path.name for path in paths]},
                ensure_ascii=False,
            ),
            paths,
        )

    if name == "send_web_image":
        if not isinstance(arguments, dict) or set(arguments) != {"url"}:
            raise ValueError("网页图片 skill 参数无效，必须只提供 url。")
        url = arguments["url"]
        if not isinstance(url, str) or not url.strip():
            raise ValueError("网页图片 URL 不能为空。")
        path = client.download_webpage_image(
            url, webpage_image_urls if webpage_image_urls is not None else set()
        )
        return (
            json.dumps({"type": "web_image", "file": path.name}, ensure_ascii=False),
            [path],
        )

    if name not in {"send_image", "send_video"}:
        raise ValueError(f"未知的媒体 skill：{name}")
    if not isinstance(arguments, dict) or set(arguments) != {"prompt"}:
        raise ValueError("媒体 skill 参数无效，必须只提供 prompt。")
    prompt = arguments["prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("媒体描述不能为空。")

    if name == "send_image":
        path = client.generate_image(prompt)
        return json.dumps({"type": "image", "file": path.name}), [path]
    path = client.generate_video(prompt)
    return json.dumps({"type": "video", "file": path.name}), [path]
