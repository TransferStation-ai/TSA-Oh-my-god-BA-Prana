import json

import pytest

from skills.modeling import (
    MODELING_SKILLS,
    ModelingSessionAccess,
    invoke_modeling_skill,
    validate_modeling_skill_arguments,
)


class FakeAccess:
    def __init__(self):
        self.calls = []

    def start(self, **kwargs): self.calls.append(("start", kwargs)); return {"ok": True}
    def open_reference(self, **kwargs): self.calls.append(("reference", kwargs)); return {"ok": True}
    def run_python(self, **kwargs): self.calls.append(("python", kwargs)); return {"stdout": "ok"}
    def render_preview(self, **kwargs): self.calls.append(("render", kwargs)); return {"path": "x.png"}
    def compile(self, **kwargs): self.calls.append(("compile", kwargs)); return {"ok": True}
    def status(self, **kwargs): self.calls.append(("status", kwargs)); return {"state": "modeling"}
    def stop(self, **kwargs): self.calls.append(("stop", kwargs)); return {"ok": True}


def test_schemas_are_closed_and_complete():
    names = {item["function"]["name"] for item in MODELING_SKILLS}
    assert names == {
        "start_modeling_job", "open_modeling_reference", "run_blender_python",
        "render_modeling_preview", "compile_gmod_model", "modeling_status", "stop_modeling_job",
    }
    for item in MODELING_SKILLS:
        assert item["function"]["parameters"]["additionalProperties"] is False


@pytest.mark.parametrize("name,arguments,message", [
    ("start_modeling_job", {"output_name": "hero"}, "source_models_dir"),
    ("start_modeling_job", {"source_models_dir": "x", "output_name": "Hero"}, "output_name"),
    ("run_blender_python", {"code": "", "description": "x"}, "code"),
    ("run_blender_python", {"code": "x", "description": "x", "timeout_seconds": 301}, "timeout"),
    ("render_modeling_preview", {"view": "top"}, "view"),
    ("render_modeling_preview", {"view": "front", "resolution": "5000x5000"}, "resolution"),
    ("open_modeling_reference", {"app": "blender"}, "app"),
])
def test_arguments_are_validated(name, arguments, message):
    with pytest.raises(ValueError, match=message):
        validate_modeling_skill_arguments(name, arguments)


def test_unknown_argument_and_tool_are_rejected():
    with pytest.raises(ValueError, match="未知参数"):
        validate_modeling_skill_arguments("modeling_status", {"x": 1})
    with pytest.raises(ValueError, match="未知的建模 skill"):
        validate_modeling_skill_arguments("nope", {})


def test_invoke_returns_utf8_json_and_dispatches():
    access = FakeAccess()
    result = invoke_modeling_skill("modeling_status", {}, access=access)
    assert json.loads(result) == {"state": "modeling"}
    assert "\\u" not in result
    assert access.calls == [("status", {})]
