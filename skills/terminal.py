import json
import os
import re
from datetime import datetime
from pathlib import Path

MAX_TERMINAL_OUTPUT_LENGTH = 20000
MAX_TERMINAL_FILE_BYTES = 64 * 1024
MAX_TERMINAL_DIRECTORY_ENTRIES = 100

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
