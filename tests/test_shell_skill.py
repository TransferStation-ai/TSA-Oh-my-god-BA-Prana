import json
import pytest
from pathlib import Path


def test_validate_rejects_empty_command():
    from skills.terminal import validate_shell_command

    with pytest.raises(ValueError):
        validate_shell_command({"command": "", "description": "x"})


def test_validate_rejects_missing_description():
    from skills.terminal import validate_shell_command

    with pytest.raises(ValueError):
        validate_shell_command({"command": "echo 1"})


def test_validate_rejects_bad_timeout():
    from skills.terminal import validate_shell_command

    with pytest.raises(ValueError):
        validate_shell_command(
            {"command": "echo 1", "description": "x", "timeout_seconds": 9999}
        )


def test_validate_accepts_valid():
    from skills.terminal import validate_shell_command

    validate_shell_command(
        {
            "command": "Get-ChildItem",
            "description": "list",
            "timeout_seconds": 30,
            "workspace": "scratch",
            "workdir": "sub",
        }
    )


def test_invoke_runs_real_powershell_command(tmp_path):
    from skills.terminal import invoke_shell_skill

    result = json.loads(
        invoke_shell_skill(
            {"command": "Write-Output hi", "description": "输出 hi"},
            permanent_root=tmp_path,
        )
    )

    assert result["exit_code"] == 0
    assert result["canceled"] is False
    assert result["timed_out"] is False
    assert "hi" in result["stdout"].strip()


def test_invoke_rejects_workdir_outside_workspace(tmp_path):
    import pytest
    from skills.terminal import invoke_shell_skill

    with pytest.raises(ValueError):
        invoke_shell_skill(
            {
                "command": "Get-Location",
                "description": "越界工作目录",
                "workdir": "..",
            },
            permanent_root=tmp_path,
        )


def test_invoke_cancels_running_command_when_stopped(tmp_path):
    from skills.terminal import invoke_shell_skill

    result = json.loads(
        invoke_shell_skill(
            {
                "command": "Start-Sleep -Seconds 10",
                "description": "等待并应被取消",
                "timeout_seconds": 30,
            },
            permanent_root=tmp_path,
            should_stop=lambda: True,
        )
    )

    assert result["canceled"] is True
    assert result["timed_out"] is False
