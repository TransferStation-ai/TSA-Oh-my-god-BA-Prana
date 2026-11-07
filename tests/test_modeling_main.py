import threading
from types import SimpleNamespace

import main
from main import _ModelingSessionAccessAdapter


class FakeSession:
    def __init__(self):
        self.calls = []

    def start(self, **kwargs): self.calls.append(("start", kwargs)); return {"ok": True}
    def open_reference(self, **kwargs): self.calls.append(("reference", kwargs)); return {"ok": True}
    def run_python(self, **kwargs): self.calls.append(("python", kwargs)); return {"stdout": "ok"}
    def render_preview(self, **kwargs): self.calls.append(("render", kwargs)); return {"path": "x.png"}
    def compile(self, **kwargs): self.calls.append(("compile", kwargs)); return {"ok": True}
    def status(self, **kwargs): self.calls.append(("status", kwargs)); return {"state": "modeling"}
    def stop(self, **kwargs): self.calls.append(("stop", kwargs)); return {"ok": True}


def test_adapter_forwards_to_session():
    session = FakeSession()
    app = SimpleNamespace(_modeling_session=lambda sid: session)
    adapter = _ModelingSessionAccessAdapter(app, SimpleNamespace(session_id="s1"))
    assert adapter.status() == {"state": "modeling"}
    assert adapter.start(source_models_dir="x", output_name="hero") == {"ok": True}
    assert session.calls[0] == ("status", {})


def test_modeling_session_is_created_lazily_and_reused(monkeypatch, tmp_path):
    created = []

    class FakeModelingSession:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(main, "ModelingSession", FakeModelingSession)
    monkeypatch.setattr(
        main.settings_store,
        "settings_file_path",
        lambda: tmp_path / "settings.json",
    )
    events = []
    app = SimpleNamespace(
        state_lock=threading.RLock(),
        modeling_sessions={},
        ai_client=SimpleNamespace(modeling_settings={"blender_path": "b"}),
        _post_to_ui=events.append,
    )

    first = main.PranaApp._modeling_session(app, "s1")
    second = main.PranaApp._modeling_session(app, "s1")

    assert first is second
    assert len(created) == 1
    assert created[0]["session_id"] == "s1"
    assert created[0]["settings"] == {"blender_path": "b"}
    assert created[0]["data_dir"] == tmp_path


def test_on_close_closes_modeling_sessions():
    calls = []

    class Session:
        def close(self):
            calls.append("close")

    app = SimpleNamespace(
        closing=False,
        state_lock=threading.RLock(),
        pending_requests={},
        active_request=None,
        desktop_sessions={},
        game_sessions={},
        modeling_sessions={"s1": main._ModelingSessionState(session=Session())},
        request_queue=SimpleNamespace(put=lambda value: None),
        root=SimpleNamespace(destroy=lambda: calls.append("destroy")),
    )

    main.PranaApp._on_close(app)

    assert calls == ["close", "destroy"]
    assert app.modeling_sessions == {}


def test_collect_modeling_settings_merges_entries():
    collected = main._collect_modeling_settings(
        {"blender_path": "old", "blockbench_path": "bb", "gmod_dir": "", "sfm_dir": ""},
        {"blender_path": " new ", "gmod_dir": " D:/GMod "},
    )
    assert collected == {
        "blender_path": "new",
        "blockbench_path": "bb",
        "gmod_dir": "D:/GMod",
        "sfm_dir": "",
    }


def test_save_configuration_forwards_modeling_settings(monkeypatch):
    import ai_client as ai_client_module

    captured = {}

    def fake_save(*args, **kwargs):
        captured["kwargs"] = kwargs

    monkeypatch.setattr(ai_client_module.settings_store, "load_settings", lambda: {})
    monkeypatch.setattr(ai_client_module.settings_store, "save_settings", fake_save)
    client = ai_client_module.AIClient()
    client.save_configuration(
        "key",
        "https://example.test/v1",
        "model",
        game={"mc_name": "Prana"},
        modeling={"blender_path": "D:/blender.exe"},
    )

    assert captured["kwargs"]["game"]["mc_name"] == "Prana"
    assert captured["kwargs"]["modeling"]["blender_path"] == "D:/blender.exe"
    assert client.modeling_settings["blender_path"] == "D:/blender.exe"
