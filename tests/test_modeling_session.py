from pathlib import Path
from types import SimpleNamespace

import pytest

import skills.modeling.session as session_module
from skills.modeling.session import ModelingSession, ModelingSessionError


class FakeBody:
    def __init__(self):
        self.calls = []
        self.running = False
        self.started = False

    def start(self):
        self.started = True
        self.running = True

    def exec(self, code, *, description, timeout=120.0):
        self.calls.append(("exec", description, code))
        return {"stdout": "ok"}

    def render(self, *, view, resolution, output, timeout=120.0):
        self.calls.append(("render", view))
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"\x89PNG\r\n\x1a\n")
        return {"path": str(output)}

    def save(self, path, timeout=120.0):
        self.calls.append(("save", str(path)))
        Path(path).write_bytes(b"BLEND")

    def quit(self):
        self.calls.append(("quit",))
        self.running = False


def _skeleton(count: int, include: str):
    return SimpleNamespace(
        version=48,
        bones=[SimpleNamespace(name=f"Bone{i}", parent=None, pos=(0, 0, 0), rot=(0, 0, 0)) for i in range(count)],
        includes=[include],
        source="fake",
    )


@pytest.fixture(autouse=True)
def fake_externals(monkeypatch):
    model = SimpleNamespace(
        path=Path("main.json"),
        identifier="geometry.test",
        texture_width=1024,
        texture_height=1024,
        bones=[object() for _ in range(186)],
        cubes=[object() for _ in range(424)],
        height=80.0,
        hands=(
            SimpleNamespace(
                side="left",
                wrist_bone="LeftForeArm",
                finger_groups=("a", "b", "c", "d"),
                finger_count=4,
                segments_per_finger=2,
                has_thumb=False,
                warning="参考模型左手只有 4 根手指且没有拇指，建模时必须补足为 5 指。",
            ),
            SimpleNamespace(
                side="right",
                wrist_bone="RightForeArm",
                finger_groups=("e", "f", "g", "h"),
                finger_count=4,
                segments_per_finger=2,
                has_thumb=False,
                warning="参考模型右手只有 4 根手指且没有拇指，建模时必须补足为 5 指。",
            ),
        ),
        warnings=(),
        scale_to_units=lambda target=72.0: target / 80.0,
    )
    monkeypatch.setattr(session_module, "parse_ysm", lambda path: model)
    monkeypatch.setattr(session_module, "load_player_skeleton", lambda gmod: _skeleton(68, "models/m_anm.mdl"))
    monkeypatch.setattr(session_module, "load_hands_skeleton", lambda gmod: _skeleton(43, "models/weapons/c_arms_animations.mdl"))
    monkeypatch.setattr(session_module, "resolve_blender", lambda settings: Path("blender.exe"))
    monkeypatch.setattr(session_module, "resolve_gmod", lambda settings: Path("gmod"))
    monkeypatch.setattr(session_module, "resolve_blockbench", lambda settings: Path("blockbench.exe"))
    monkeypatch.setattr(session_module, "resolve_sfm", lambda settings: None)


def test_start_creates_job_and_source_json(tmp_path: Path):
    (tmp_path / "models").mkdir()
    session = ModelingSession(
        session_id="s1",
        settings={},
        data_dir=tmp_path,
        body_factory=lambda *a, **k: FakeBody(),
    )
    result = session.start(str(tmp_path / "models"), "hero")
    assert result["ok"] is True
    job_dir = Path(result["job_dir"])
    assert (job_dir / "source.json").is_file()
    assert (job_dir / "work.blend").parent.is_dir()
    assert session.status()["state"] == "modeling"


def test_output_name_must_be_ascii_lowercase(tmp_path: Path):
    (tmp_path / "models").mkdir()
    session = ModelingSession("s1", {}, tmp_path, body_factory=lambda *a, **k: FakeBody())
    with pytest.raises(ModelingSessionError, match="output_name"):
        session.start(str(tmp_path / "models"), "Hero Model")


def test_only_one_active_job(tmp_path: Path):
    (tmp_path / "models").mkdir()
    session = ModelingSession("s1", {}, tmp_path, body_factory=lambda *a, **k: FakeBody())
    session.start(str(tmp_path / "models"), "hero")
    with pytest.raises(ModelingSessionError, match="已有"):
        session.start(str(tmp_path / "models"), "hero2")


def test_render_rejects_unknown_view(tmp_path: Path):
    (tmp_path / "models").mkdir()
    session = ModelingSession("s1", {}, tmp_path, body_factory=lambda *a, **k: FakeBody())
    session.start(str(tmp_path / "models"), "hero")
    with pytest.raises(ModelingSessionError, match="view"):
        session.render_preview("top")


def test_stop_saves_and_quits(tmp_path: Path):
    (tmp_path / "models").mkdir()
    session = ModelingSession("s1", {}, tmp_path, body_factory=lambda *a, **k: FakeBody())
    session.start(str(tmp_path / "models"), "hero")
    result = session.stop()
    assert result["ok"] is True
    assert session.status()["state"] == "stopped"
