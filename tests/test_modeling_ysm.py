import json
from pathlib import Path

import pytest

from skills.modeling.ysm import YsmParseError, parse_ysm


def write_model(root: Path, data: dict) -> Path:
    models = root / "models"
    models.mkdir(parents=True)
    (models / "main.json").write_text(json.dumps(data), encoding="utf-8")
    (root / "textures").mkdir()
    (root / "textures" / "default.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (64).to_bytes(4, "big") + (64).to_bytes(4, "big")
    )
    return models


def minimal_geometry() -> dict:
    return {
        "format_version": "1.12.0",
        "minecraft:geometry": [{
            "description": {
                "identifier": "geometry.test",
                "texture_width": 64,
                "texture_height": 64,
                "visible_bounds_width": 4,
                "visible_bounds_height": 4,
                "visible_bounds_offset": [0, 2, 0],
            },
            "bones": [{
                "name": "Root",
                "pivot": [0, 0, 0],
                "cubes": [{
                    "origin": [0, 0, 0],
                    "size": [8, 8, 8],
                    "uv": {"north": {"uv": [0, 0], "uv_size": [8, 8]}},
                }],
            }],
        }],
    }


def test_parse_minimal_geometry(tmp_path: Path):
    model = parse_ysm(write_model(tmp_path, minimal_geometry()))
    assert model.identifier == "geometry.test"
    assert model.texture_width == 64
    assert model.texture_height == 64
    assert len(model.bones) == 1
    assert len(model.cubes) == 1
    assert model.height == 8
    assert model.scale_to_units(72) == 9.0


def test_missing_main_json_raises(tmp_path: Path):
    with pytest.raises(YsmParseError, match="main.json"):
        parse_ysm(tmp_path / "models")


def test_invalid_json_raises(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "main.json").write_text("{", encoding="utf-8")
    with pytest.raises(YsmParseError, match="JSON"):
        parse_ysm(models)


def test_bone_cycle_raises(tmp_path: Path):
    data = minimal_geometry()
    data["minecraft:geometry"][0]["bones"][0]["parent"] = "Root"
    with pytest.raises(YsmParseError, match="环"):
        parse_ysm(write_model(tmp_path, data))


def test_uv_outside_texture_is_warning(tmp_path: Path):
    data = minimal_geometry()
    data["minecraft:geometry"][0]["bones"][0]["cubes"][0]["uv"]["north"]["uv"] = [999, 999]
    model = parse_ysm(write_model(tmp_path, data))
    assert any("UV" in warning for warning in model.warnings)


REAL_MODELS = Path(r"D:\mc\.minecraft\versions\测试端&异界征途-1.20.1\config\yes_steve_model\custom\模块化的神秘士兵们\终焉盾兵\models")


@pytest.mark.skipif(not (REAL_MODELS / "main.json").exists(), reason="real YSM model not installed")
def test_real_model_hands_have_four_fingers_without_thumb():
    model = parse_ysm(REAL_MODELS)
    assert len(model.bones) == 186
    assert len(model.cubes) == 424
    assert model.texture_width == 1024
    assert len(model.hands) == 2
    for hand in model.hands:
        assert hand.finger_count == 4
        assert hand.segments_per_finger == 2
        assert hand.has_thumb is False
        assert "拇指" in hand.warning
