import sys
from pathlib import Path

import pytest

from skills.modeling.body import BlenderBody, BlenderBodyError

FAKE = Path(__file__).parent / "fixtures" / "fake_blender_body.py"


def test_ping_exec_and_quit(tmp_path: Path):
    body = BlenderBody([sys.executable, str(FAKE)], cwd=tmp_path, log_path=tmp_path / "blender.log")
    body.start()
    try:
        assert body.request("ping", timeout=5)["blender"] == "fake"
        result = body.exec("print('hello')", description="test", timeout=5)
        assert result["stdout"] == "hello\n"
    finally:
        body.quit()
    assert body.running is False


def test_non_json_stdout_is_ignored(tmp_path: Path):
    body = BlenderBody([sys.executable, str(FAKE)], cwd=tmp_path, log_path=tmp_path / "blender.log")
    body.start()
    try:
        result = body.exec("noise", description="noise", timeout=5)
        assert result["stdout"] == ""
    finally:
        body.quit()


def test_timeout_raises(tmp_path: Path):
    body = BlenderBody([sys.executable, str(FAKE)], cwd=tmp_path, log_path=tmp_path / "blender.log")
    body.start()
    try:
        with pytest.raises(BlenderBodyError, match="超时"):
            body.request("sleep", timeout=0.5)
    finally:
        body.quit()


def test_crash_marks_not_running_and_restart(tmp_path: Path):
    body = BlenderBody([sys.executable, str(FAKE)], cwd=tmp_path, log_path=tmp_path / "blender.log")
    body.start()
    with pytest.raises(BlenderBodyError):
        body.request("crash", timeout=5)
    assert body.running is False
    body.start()
    assert body.request("ping", timeout=5)["ok"] is True
    body.quit()
