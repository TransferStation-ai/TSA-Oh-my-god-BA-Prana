import json
import os
import re
import subprocess
from pathlib import Path

from skills.workspace import default_workspace_root, validate_workspace_path

MAX_GIT_OUTPUT_LENGTH = 20000
MAX_GIT_REF_LENGTH = 200
GIT_TIMEOUT_SECONDS = 15
SAFE_GIT_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/~^@{}-]*$")

GIT_SKILLS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "read_git_repository",
            "description": (
                "Read read-only Git information from the workspace repository: "
                "status, recent log entries, a diff, or the branch list. Only "
                "call when the user explicitly asks to inspect Git state or "
                "changes. This skill never stages, commits, pushes, checks "
                "out, or modifies files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "log", "diff", "branch"],
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": (
                            "Number of log entries to return when action is "
                            "log. Defaults to 10."
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Optional workspace-relative file or directory to "
                            "restrict a diff to."
                        ),
                    },
                    "ref": {
                        "type": "string",
                        "description": (
                            "Optional Git ref or revision range for a diff, "
                            "such as HEAD~1 or main..HEAD."
                        ),
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    },
]


def _validate_limit(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 50
    ):
        raise ValueError("limit 必须是 1 到 50 之间的整数。")
    return value


def _validate_ref(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Git ref 必须是字符串。")
    value = value.strip()
    if not value or len(value) > MAX_GIT_REF_LENGTH:
        raise ValueError("Git ref 格式无效。")
    if SAFE_GIT_REF_PATTERN.fullmatch(value) is None:
        raise ValueError("Git ref 格式无效。")
    return value


def validate_git_arguments(
    arguments: object,
) -> tuple[str, int, str | None, str | None]:
    if not isinstance(arguments, dict) or not set(arguments).issubset(
        {"action", "limit", "path", "ref"}
    ):
        raise ValueError("Git 读取 skill 参数无效。")
    action = arguments.get("action")
    if action not in {"status", "log", "diff", "branch"}:
        raise ValueError("action 只能是 status、log、diff 或 branch。")
    allowed_keys = {"action"}
    if action == "log":
        allowed_keys.add("limit")
    if action == "diff":
        allowed_keys.update({"path", "ref"})
    if not set(arguments).issubset(allowed_keys):
        raise ValueError("该 Git 操作不接受这些参数。")
    limit = _validate_limit(arguments.get("limit", 10))
    path = validate_workspace_path(arguments.get("path"))
    ref = _validate_ref(arguments.get("ref"))
    return action, limit, path, ref


def _run_git(
    root: Path, arguments: list[str]
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "--no-pager", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            ),
        )
    except FileNotFoundError as error:
        raise RuntimeError("未找到 Git 命令，请确认已安装 Git。") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Git 命令执行超时。") from error


def _ensure_repository(root: Path) -> None:
    result = _run_git(root, ["rev-parse", "--is-inside-work-tree"])
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise ValueError("当前工作目录不是 Git 仓库。")


def _resolve_repository_path(root: Path, path: str | None) -> str | None:
    if path is None:
        return None
    resolved_root = root.resolve()
    target = (resolved_root / path).resolve()
    try:
        relative = target.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("只能访问应用工作目录内的路径。") from error
    return relative.as_posix()


def _build_git_arguments(
    action: str,
    limit: int,
    path: str | None,
    ref: str | None,
) -> list[str]:
    if action == "status":
        return ["status", "--porcelain=v1", "--branch"]
    if action == "log":
        return [
            "log",
            f"-n{limit}",
            "--date=short",
            "--pretty=format:%h %ad %s",
        ]
    if action == "diff":
        arguments = ["diff", "--no-color", "--no-ext-diff"]
        if ref is not None:
            arguments.append(ref)
        if path is not None:
            arguments.extend(["--", path])
        return arguments
    return ["branch", "--no-color", "--format=%(refname:short)"]


def invoke_git_skill(
    arguments: object,
    *,
    workspace_root: Path | None = None,
) -> str:
    action, limit, path, ref = validate_git_arguments(arguments)
    root = (workspace_root or default_workspace_root()).resolve()
    if not root.is_dir():
        raise ValueError("工作目录不存在或不是目录。")
    _ensure_repository(root)
    relative_path = _resolve_repository_path(root, path)
    result = _run_git(
        root, _build_git_arguments(action, limit, relative_path, ref)
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Git 命令失败：{details[:500]}")
    output = result.stdout
    truncated = len(output) > MAX_GIT_OUTPUT_LENGTH
    if truncated:
        output = output[:MAX_GIT_OUTPUT_LENGTH] + "\n…输出已截断"
    return json.dumps(
        {
            "repository": str(root),
            "action": action,
            "output": output,
            "truncated": truncated,
        },
        ensure_ascii=False,
    )
