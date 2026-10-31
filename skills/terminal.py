import json
import os
import re
from datetime import datetime
from pathlib import Path

MAX_TERMINAL_OUTPUT_LENGTH = 20000
MAX_TERMINAL_FILE_BYTES = 64 * 1024
MAX_TERMINAL_DIRECTORY_ENTRIES = 100
MAX_SHELL_OUTPUT_BYTES = 16 * 1024
MAX_SHELL_COMMAND_LENGTH = 5000
MAX_SHELL_TIMEOUT_SECONDS = 300
DEFAULT_SHELL_TIMEOUT_SECONDS = 300

TERMINAL_SKILLS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "run_terminal_command",
            "description": (
                "Run a read-only inspection command in the app workspace using "
                "PowerShell or Command Prompt syntax. Supported commands are "
                "PowerShell Get-Location/pwd, Get-ChildItem/dir/ls, "
                "Get-Content/type/cat, or CMD cd, dir, type. "
                "Only call when the user explicitly asks to inspect the workspace "
                "or a specific file path the user has clearly authorized. "
                "No scripts, command chaining, writes, or other unsafe actions. "
                "If the user explicitly approves access outside the app workspace, "
                "set allow_outside_workspace to true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "terminal": {
                        "type": "string",
                        "enum": ["powershell", "cmd"],
                    },
                    "command": {
                        "type": "string",
                        "description": (
                            "One supported inspection command with an optional "
                            "workspace-relative path."
                        ),
                    },
                    "allow_outside_workspace": {
                        "type": "boolean",
                        "description": (
                            "Set to true only when the user explicitly approves "
                            "reading files or directories outside the application workspace."
                        ),
                    },
                },
                "required": ["terminal", "command"],
                "additionalProperties": False,
            },
        },
    },
]

TIME_SKILLS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "get_system_time",
            "description": (
                "Get the current local system date and time in the computer's timezone. "
                "Only call when the user explicitly asks for the current time, date, "
                "or related time information."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]

_COMMAND_PATTERN = re.compile(
    r"""\s*([A-Za-z-]+)(?:\s+("(?:[^"]*)"|'(?:[^']*)'|[^\s;&|<>]+))?\s*"""
)
_LOCATION_COMMANDS = {"get-location", "pwd", "cd"}
_LIST_COMMANDS = {"get-childitem", "dir", "ls"}
_READ_COMMANDS = {"get-content", "type", "cat"}


def _parse_terminal_command(command: str) -> tuple[str, str | None]:
    if not isinstance(command, str) or len(command) > 2000:
        raise ValueError("终端命令格式无效。")
    match = _COMMAND_PATTERN.fullmatch(command)
    if match is None:
        raise ValueError("仅支持单条只读查询命令，不支持脚本或命令链。")
    name = match.group(1).lower()
    path = match.group(2)
    if path is not None and path[:1] in {"'", '"'}:
        path = path[1:-1]
    if name not in _LOCATION_COMMANDS | _LIST_COMMANDS | _READ_COMMANDS:
        raise ValueError("该终端命令不在只读命令白名单中。")
    if name in _LOCATION_COMMANDS and path is not None:
        raise ValueError("位置查询命令不接受路径参数。")
    if name in _READ_COMMANDS and not path:
        raise ValueError("读取文件命令必须指定工作目录内的文件路径。")
    return name, path


def validate_terminal_command(arguments: object) -> tuple[str, str]:
    if not isinstance(arguments, dict):
        raise ValueError("终端命令参数无效。")
    terminal = arguments.get("terminal")
    allow_outside_workspace = arguments.get("allow_outside_workspace", False)
    if (
        set(arguments) not in ({"terminal", "command"}, {"terminal", "command", "allow_outside_workspace"})
        or not isinstance(terminal, str)
        or terminal not in {"powershell", "cmd"}
        or not isinstance(allow_outside_workspace, bool)
    ):
        raise ValueError("终端命令参数无效。")
    name, _ = _parse_terminal_command(arguments["command"])
    if terminal == "cmd" and name not in {"cd", "dir", "type"}:
        raise ValueError("该命令不受 CMD 终端支持。")
    if terminal == "powershell" and name == "cd":
        raise ValueError("该命令不受 PowerShell 终端支持。")
    return terminal, arguments["command"]


def _normalize_windows_path(path: str) -> str:
    """Expand common Windows user-folder references used in desktop/file inspection."""
    if not path:
        return path
    home_dir = Path.home().resolve()
    user_profile = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(home_dir)
    expanded = os.path.expandvars(path)
    expanded = expanded.replace("%USERPROFILE%", user_profile)
    expanded = expanded.replace("%HOMEDRIVE%%HOMEPATH%", user_profile)
    expanded = expanded.replace("%HOME%", user_profile)
    expanded = os.path.expanduser(expanded)
    if expanded.startswith("~"):
        return expanded

    if os.name == "nt":
        normalized = os.path.normpath(expanded)
        if normalized.startswith("\\\\") or normalized.startswith("//"):
            return normalized
        if re.match(r"^[A-Za-z]:", normalized):
            return normalized
        if normalized.startswith("~"):
            return normalized
        if normalized.lower() in {"desktop", "documents", "downloads", "music", "pictures", "videos"}:
            return str((home_dir / normalized).resolve())
    return expanded


def _resolve_path_target(root: Path, path: str | None, *, allow_outside_workspace: bool) -> Path:
    if path is None:
        return root.resolve()

    normalized = _normalize_windows_path(path)
    if _looks_like_absolute_path(normalized):
        if allow_outside_workspace:
            return Path(normalized).resolve()
        raise ValueError("终端命令只能访问应用工作目录内的路径。")

    if normalized.lower() in {"desktop", "documents", "downloads", "music", "pictures", "videos"} and os.name == "nt":
        candidate = (Path.home() / normalized).resolve()
        if allow_outside_workspace:
            return candidate
        raise ValueError("终端命令只能访问应用工作目录内的路径。")

    candidate = (root / normalized).resolve()
    if allow_outside_workspace:
        return candidate
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("终端命令只能访问应用工作目录内的路径。") from error
    return candidate


def _workspace_path(
    workspace_root: Path, path: str | None, *, allow_outside_workspace: bool = False
) -> Path:
    root = workspace_root.resolve()
    if path is None:
        return root
    return _resolve_path_target(root, path, allow_outside_workspace=allow_outside_workspace)


def _looks_like_absolute_path(path: str) -> bool:
    if not path:
        return False
    if path.startswith("\\\\") or path.startswith("//"):
        return True
    if re.match(r"^[A-Za-z]:", path):
        return True
    if path.startswith("~"):
        return True
    if path.startswith("%"):
        return True
    return Path(path).is_absolute()


def _read_text_file(target: Path) -> str:
    """Read UTF-8/UTF-16 Windows text files without assuming a single encoding."""
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1252"):
        try:
            return target.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return target.read_bytes().decode("utf-8", errors="replace")


def invoke_terminal_skill(
    arguments: object, *, workspace_root: Path | None = None
) -> str:
    terminal, command = validate_terminal_command(arguments)
    name, path = _parse_terminal_command(command)
    root = workspace_root or Path(__file__).resolve().parent.parent
    allow_outside_workspace = bool(
        isinstance(arguments, dict) and arguments.get("allow_outside_workspace", False)
    )

    if name in _LOCATION_COMMANDS:
        result = str(root.resolve())
    else:
        target = _workspace_path(root, path, allow_outside_workspace=allow_outside_workspace)
        if name in _LIST_COMMANDS:
            if not target.is_dir():
                raise ValueError("目录不存在或不是目录。")
            entries = sorted(target.iterdir(), key=lambda item: item.name.lower())
            result = "\n".join(
                f"{'[目录]' if entry.is_dir() else '[文件]'} {entry.name}"
                for entry in entries[:MAX_TERMINAL_DIRECTORY_ENTRIES]
            )
            if len(entries) > MAX_TERMINAL_DIRECTORY_ENTRIES:
                result += (
                    f"\n…仅显示前 {MAX_TERMINAL_DIRECTORY_ENTRIES} 项"
                )
        else:
            if not target.is_file():
                raise ValueError("文件不存在或不是普通文件。")
            if target.stat().st_size > MAX_TERMINAL_FILE_BYTES:
                raise ValueError(
                    f"文件超过 {MAX_TERMINAL_FILE_BYTES // 1024} KB 读取限制。"
                )
            result = _read_text_file(target)

    if len(result) > MAX_TERMINAL_OUTPUT_LENGTH:
        result = result[:MAX_TERMINAL_OUTPUT_LENGTH] + "\n…输出已截断"
    return json.dumps({"terminal": terminal, "output": result})


def validate_system_time_arguments(arguments: object) -> None:
    if arguments is None:
        return
    if not isinstance(arguments, dict) or set(arguments) != set():
        raise ValueError("系统时间查询不接受额外参数。")


def invoke_system_time_skill(arguments: object = None) -> str:
    validate_system_time_arguments(arguments)
    now = datetime.now()
    return json.dumps(
        {
            "datetime": now.isoformat(timespec="seconds"),
            "timezone": "local",
            "formatted": now.strftime("%Y-%m-%d %H:%M:%S"),
        },
        ensure_ascii=False,
    )


SHELL_SKILLS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "run_shell_command",
            "description": (
                "Run one PowerShell command in the selected workspace to "
                "inspect, build, lint, test, or run the project. Use only "
                "for explicit coding/verification tasks. The command runs "
                "in the OS shell under the user's account and must be "
                "approved by the review model. Do not run destructive "
                "commands unless the user explicitly asks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "One PowerShell command.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Why the command is run.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_SHELL_TIMEOUT_SECONDS,
                    },
                    "workdir": {
                        "type": "string",
                        "description": (
                            "Optional workspace-relative subdirectory."
                        ),
                    },
                    "workspace": {
                        "type": "string",
                        "enum": ["permanent", "scratch"],
                    },
                },
                "required": ["command", "description"],
                "additionalProperties": False,
            },
        },
    },
]


def validate_shell_command(arguments: object) -> None:
    if not isinstance(arguments, dict):
        raise ValueError("Shell 命令参数无效。")
    if not set(arguments).issubset(
        {"command", "description", "timeout_seconds", "workdir", "workspace"}
    ):
        raise ValueError("Shell 命令参数无效。")
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command 必须是非空字符串。")
    if len(command) > MAX_SHELL_COMMAND_LENGTH:
        raise ValueError("command 过长。")
    description = arguments.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description 必须是非空字符串。")
    timeout = arguments.get("timeout_seconds", DEFAULT_SHELL_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise ValueError("timeout_seconds 必须是整数。")
    if not 1 <= timeout <= MAX_SHELL_TIMEOUT_SECONDS:
        raise ValueError("timeout_seconds 必须在 1 到 300 之间。")
    workdir = arguments.get("workdir")
    if workdir is not None and (
        not isinstance(workdir, str) or not workdir.strip()
    ):
        raise ValueError("workdir 必须是非空字符串。")
    workspace = arguments.get("workspace", "permanent")
    if workspace not in {"permanent", "scratch"}:
        raise ValueError("workspace 只能是 permanent 或 scratch。")


def invoke_shell_skill(
    arguments: object,
    *,
    permanent_root: Path | None = None,
    scratch_root: Path | None = None,
) -> str:
    validate_shell_command(arguments)
    from skills.workspace import resolve_workspace_root

    assert isinstance(arguments, dict)
    command = str(arguments["command"])
    timeout = int(arguments.get("timeout_seconds", DEFAULT_SHELL_TIMEOUT_SECONDS))
    workdir = str(arguments.get("workdir") or "")
    workspace = str(arguments.get("workspace", "permanent"))

    root = resolve_workspace_root(workspace, permanent_root, scratch_root)
    target = (root / workdir).resolve() if workdir else root
    if workdir:
        try:
            target.relative_to(root)
        except ValueError as error:
            raise ValueError("workdir 必须位于所选工作区内。") from error
        if not target.is_dir():
            raise ValueError("workdir 不存在或不是目录。")

    import os
    import subprocess

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NoLogo", "-Command", command],
            cwd=target,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            ),
        )
        timed_out = False
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        exit_code = result.returncode
    except subprocess.TimeoutExpired as error:
        timed_out = True
        stdout = error.stdout or "" if error.stdout else ""
        stderr = error.stderr or "" if error.stderr else ""
        exit_code = -1

    stdout_b = stdout.encode("utf-8", "replace")[:MAX_SHELL_OUTPUT_BYTES]
    stderr_b = stderr.encode("utf-8", "replace")[:MAX_SHELL_OUTPUT_BYTES]
    truncated = (
        len(stdout.encode("utf-8", "replace")) > MAX_SHELL_OUTPUT_BYTES
        or len(stderr.encode("utf-8", "replace")) > MAX_SHELL_OUTPUT_BYTES
    )
    return json.dumps(
        {
            "exit_code": exit_code,
            "stdout": stdout_b.decode("utf-8", "replace"),
            "stderr": stderr_b.decode("utf-8", "replace"),
            "timed_out": timed_out,
            "truncated": truncated,
            "workspace": workspace,
        },
        ensure_ascii=False,
    )
