from pathlib import Path

import pytest

from skills.modeling.source import (
    SourceAssetError,
    VpkArchive,
    load_hands_skeleton,
    load_player_skeleton,
    parse_mdl,
)

GMOD = Path(r"D:\Administrator\Steam\steamapps\common\GarrysMod")
VPK = GMOD / "garrysmod" / "garrysmod_dir.vpk"


@pytest.mark.skipif(not VPK.exists(), reason="Garry's Mod not installed")
def test_read_real_mdl_from_vpk():
    data = VpkArchive(VPK).read("models/player/group01/male_01.mdl")
    assert data[:4] == b"IDST"
    assert len(data) == 142196


@pytest.mark.skipif(not VPK.exists(), reason="Garry's Mod not installed")
def test_parse_male_01_has_68_bones_and_include():
    info = parse_mdl(VpkArchive(VPK).read("models/player/group01/male_01.mdl"))
    assert info.version == 48
    assert len(info.bones) == 68
    assert info.bones[0].name == "ValveBiped.Bip01_Pelvis"
    assert info.bones[0].parent == -1
    assert info.includes == ("models/m_anm.mdl",)


@pytest.mark.skipif(not VPK.exists(), reason="Garry's Mod not installed")
def test_parse_c_arms_has_43_bones_and_include():
    info = parse_mdl(VpkArchive(VPK).read("models/weapons/c_arms_citizen.mdl"))
    assert len(info.bones) == 43
    assert info.includes == ("models/weapons/c_arms_animations.mdl",)


@pytest.mark.skipif(not VPK.exists(), reason="Garry's Mod not installed")
def test_parse_hostage_04_locks_three_include_entries():
    info = parse_mdl(VpkArchive(VPK).read("models/player/hostage/hostage_04.mdl"))
    assert info.includes == (
        "models/m_anm.mdl",
        "models/m_shd.mdl",
        "models/m_gst.mdl",
    )


@pytest.mark.skipif(not VPK.exists(), reason="Garry's Mod not installed")
def test_load_skeletons_validate_counts_and_includes():
    body = load_player_skeleton(GMOD)
    hands = load_hands_skeleton(GMOD)
    assert len(body.bones) == 68
    assert body.includes == ("models/m_anm.mdl",)
    assert len(hands.bones) == 43
    assert hands.includes == ("models/weapons/c_arms_animations.mdl",)


def test_missing_vpk_raises(tmp_path: Path):
    with pytest.raises(SourceAssetError, match="garrysmod_dir.vpk"):
        VpkArchive(tmp_path / "garrysmod_dir.vpk")


def test_missing_asset_raises(tmp_path: Path):
    archive = VpkArchive(VPK) if VPK.exists() else None
    if archive is None:
        pytest.skip("Garry's Mod not installed")
    with pytest.raises(SourceAssetError, match="不存在"):
        archive.read("models/player/not_found.mdl")
