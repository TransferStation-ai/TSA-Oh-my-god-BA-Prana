from pathlib import Path

from skills.modeling.gmod import (
    SmdBone,
    SmdTriangle,
    SmdVertex,
    build_addon_lua,
    build_body_qc,
    build_hands_qc,
    build_vmt,
    package_addon,
    write_smd,
    write_vtf,
)


def test_write_smd_contains_nodes_and_triangle(tmp_path: Path):
    out = tmp_path / "body.smd"
    write_smd(
        out,
        bones=[SmdBone("ValveBiped.Bip01_Pelvis", -1)],
        vertices=[
            SmdVertex(0, (0, 0, 0), (0, 0, 1), (0, 0), ((0, 1.0),)),
            SmdVertex(0, (1, 0, 0), (0, 0, 1), (1, 0), ((0, 1.0),)),
            SmdVertex(0, (0, 1, 0), (0, 0, 1), (0, 1), ((0, 1.0),)),
        ],
        triangles=[SmdTriangle("body", 0, 1, 2)],
    )
    text = out.read_text(encoding="utf-8")
    assert "version 1" in text
    assert "ValveBiped.Bip01_Pelvis" in text
    assert "body" in text


def test_body_qc_has_collision_and_include():
    qc = build_body_qc("hero")
    assert '$modelname "player/hero.mdl"' in qc
    assert '$includemodel "models/m_anm.mdl"' in qc
    assert "$collisionjoints" in qc
    assert '$surfaceprop "flesh"' in qc


def test_hands_qc_has_arms_include_and_no_collision():
    qc = build_hands_qc("hero")
    assert '$modelname "player/hero_arms.mdl"' in qc
    assert '$includemodel "models/weapons/c_arms_animations.mdl"' in qc
    assert "$collisionjoints" not in qc


def test_vmt_is_vertex_lit_generic():
    text = build_vmt("models/player/hero/hero_body")
    assert '"VertexLitGeneric"' in text
    assert "$model 1" in text


def test_write_vtf_has_header_and_mipmaps(tmp_path: Path):
    out = tmp_path / "hero.vtf"
    write_vtf(out, width=4, height=4, rgba=b"\x00" * (4 * 4 * 4))
    data = out.read_bytes()
    assert data[:4] == b"VTF\0"
    assert len(data) > 16
    assert out.stat().st_size > 80


def test_lua_registers_model_and_hands():
    text = build_addon_lua("hero", "终焉盾兵")
    assert 'player_manager.AddValidModel("hero", "models/player/hero.mdl"' in text
    assert 'player_manager.AddValidHands("hero", "models/player/hero_arms.mdl", 0, "0000000")' in text


def test_package_addon_layout(tmp_path: Path):
    body = tmp_path / "hero.mdl"
    hands = tmp_path / "hero_arms.mdl"
    body.write_bytes(b"body")
    hands.write_bytes(b"hands")
    addon = package_addon(
        addon_dir=tmp_path / "addon",
        name="hero",
        body_files=[body],
        hands_files=[hands],
        materials=[],
        title="终焉盾兵",
    )
    assert (addon / "models" / "player" / "hero.mdl").is_file()
    assert (addon / "models" / "player" / "hero_arms.mdl").is_file()
    assert (addon / "lua" / "autorun" / "hero.lua").is_file()
