from pathlib import Path

import pytest

from skills.modeling.body import BlenderBody

BLENDER = Path(r"D:\mc\blender-3.0.1-windows-x64\blender.exe")
ROOT = Path(__file__).resolve().parents[1]
BODY_SCRIPT = ROOT / "skills" / "modeling" / "blender_body.py"


@pytest.mark.skipif(not BLENDER.exists(), reason="Blender 3.0 not installed")
def test_blender_body_ping_exec_render_save(tmp_path: Path):
    body = BlenderBody(
        [str(BLENDER), "-b", "--factory-startup", "--python", str(BODY_SCRIPT)],
        cwd=ROOT,
        log_path=tmp_path / "blender.log",
    )
    body.start()
    try:
        assert body.request("ping", timeout=30)["blender"].startswith("3.0")
        result = body.exec(
            "prana_api.reset_scene()\n"
            "prana_api.add_box('Body', (0, 0, 0), (2, 2, 2))\n"
            "print(prana_api.scene_summary()['objects'])",
            description="create cube",
            timeout=60,
        )
        assert result["stdout"].strip() == "1"
        png = tmp_path / "front.png"
        body.render(view="front", resolution="256x256", output=png, timeout=60)
        assert png.is_file() and png.stat().st_size > 100
        blend = tmp_path / "work.blend"
        body.save(blend, timeout=60)
        assert blend.is_file()
    finally:
        body.quit()
