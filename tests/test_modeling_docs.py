from pathlib import Path

import main
from help_content import HELP_SECTIONS

ROOT = Path(__file__).resolve().parents[1]


def test_system_prompt_mentions_modeling_workflow():
    text = main.SYSTEM_PROMPT
    assert "start_modeling_job" in text
    assert "render_modeling_preview" in text
    assert "compile_gmod_model" in text
    assert "老师自己安装" in text


def test_help_has_modeling_section():
    assert any("3D 建模" in title for title, _ in HELP_SECTIONS)


def test_readme_mentions_gmod_addon():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "GMod" in text or "Garry's Mod" in text
    assert "addons" in text
