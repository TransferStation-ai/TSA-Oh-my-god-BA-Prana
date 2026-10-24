import json
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


def _workspace_path(
    workspace_root: Path, path: str | None, *, allow_outside_workspace: bool = False
) -> Path:
    root = workspace_root.resolve()
    candidate = root if path is None else (root / path).resolve()
    if allow_outside_workspace:
        return candidate
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("终端命令只能访问应用工作目录内的路径。") from error
    return candidate


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
            result = target.read_text(encoding="utf-8")

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
