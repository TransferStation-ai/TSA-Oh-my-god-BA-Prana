from pathlib import Path

import pytest

from skills.modeling import settings as modeling_settings


def test_resolve_explicit_tool_paths(tmp_path: Path):
    blender = tmp_path / "blender.exe"
    blockbench = tmp_path / "Blockbench.exe"
    gmod = tmp_path / "GarrysMod"
    sfm = tmp_path / "SourceFilmmaker"
    for path in (blender, blockbench):
        path.write_bytes(b"x")
    gmod.mkdir()
    sfm.mkdir()

    values = {
        "blender_path": str(blender),
        "blockbench_path": str(blockbench),
        "gmod_dir": str(gmod),
        "sfm_dir": str(sfm),
    }
    assert modeling_settings.resolve_blender(values) == blender
    assert modeling_settings.resolve_blockbench(values) == blockbench
    assert modeling_settings.resolve_gmod(values) == gmod
    assert modeling_settings.resolve_sfm(values) == sfm


def test_optional_sfm_is_none_when_not_configured(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(modeling_settings, "SFM_CANDIDATES", ())
    assert modeling_settings.resolve_sfm({}) is None


def test_missing_required_tool_raises_actionable_error(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(modeling_settings, "BLENDER_CANDIDATES", ())
    values = {"blender_path": str(tmp_path / "missing.exe")}
    with pytest.raises(modeling_settings.ModelingToolError, match="Blender"):
        modeling_settings.resolve_blender(values)
