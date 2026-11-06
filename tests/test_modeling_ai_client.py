import base64
from pathlib import Path
from types import SimpleNamespace

from ai_client import (
    MODELING_REVIEW_SYSTEM_PROMPT,
    AIClient,
    build_modeling_preview_message,
    modeling_tool_list,
)
from skills.modeling import MODELING_SKILLS


def test_tool_list_only_with_session():
    assert modeling_tool_list(None) == []
    assert modeling_tool_list(object()) == MODELING_SKILLS


def test_preview_message_uses_png_data_url(tmp_path: Path):
    png = tmp_path / "front.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nabc")
    message = build_modeling_preview_message(png, "正面预览")
    assert message["role"] == "user"
    text_part, image_part = message["content"]
    assert "正面预览" in text_part["text"]
    assert "不可信数据" in text_part["text"]
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
    assert base64.b64decode(image_part["image_url"]["url"].split(",", 1)[1]) == png.read_bytes()


def test_review_modeling_operation_uses_effect_and_prompt():
    calls = []
    fake = SimpleNamespace(_review_sensitive_operation=lambda *a, **k: calls.append((a, k)))
    AIClient._review_modeling_operation(
        fake,
        "run_blender_python",
        {"code": "print(1)", "description": "x"},
        [],
    )
    args, kwargs = calls[0]
    assert "Blender" in kwargs["effect"]
    assert kwargs["system_prompt"] == MODELING_REVIEW_SYSTEM_PROMPT
