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
