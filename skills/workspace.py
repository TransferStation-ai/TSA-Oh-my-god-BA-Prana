import fnmatch
import json
import os
from pathlib import Path

MAX_PROJECT_STRUCTURE_ENTRIES = 300
MAX_SEARCH_RESULTS = 100
DEFAULT_SEARCH_RESULTS = 30
MAX_SEARCH_QUERY_LENGTH = 200
MAX_SEARCH_FILE_BYTES = 512 * 1024
MAX_SEARCH_FILES = 2000
MAX_SEARCH_TOTAL_BYTES = 20 * 1024 * 1024
MAX_SEARCH_LINE_LENGTH = 300
MAX_WORKSPACE_PATH_LENGTH = 500
IGNORED_DIRECTORY_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
    ".venv",
    "venv",
    "env",
    "node_modules",
    ".idea",
}

WORKSPACE_SKILLS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "get_project_structure",
            "description": (
                "List a directory tree inside the app workspace. Only call when "
                "the user explicitly asks to inspect the project structure or "
                "directory layout. This skill is read-only, rejects paths "
                "outside the workspace, and omits common build, cache, and "
                "dependency directories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Optional directory path relative to the app "
                            "workspace. Defaults to the workspace root."
                        ),
                    },
                    "max_depth": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 4,
                        "description": (
                            "Maximum directory depth to display. Defaults to 2."
                        ),
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_workspace_files",
            "description": (
                "Search files inside the app workspace by file name or by "
                "literal text content. Use mode='filename' with an optional "
                "glob pattern, or mode='content' to search text inside files. "
                "Only call when the user explicitly asks to find files or "
                "search the project. This skill is read-only and rejects paths "
                "outside the workspace."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["filename", "content"],
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "File name glob or literal text to search for, "
                            "depending on mode."
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Optional directory path relative to the app "
                            "workspace. Defaults to the workspace root."
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_SEARCH_RESULTS,
                        "description": (
                            "Maximum number of results to return. Defaults to "
                            f"{DEFAULT_SEARCH_RESULTS}."
                        ),
                    },
                },
                "required": ["mode", "query"],
                "additionalProperties": False,
            },
        },
    },
]


def default_workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent


def validate_workspace_path(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("路径参数必须是字符串。")
    value = value.strip()
    if not value:
        raise ValueError("路径不能为空。")
    if len(value) > MAX_WORKSPACE_PATH_LENGTH:
        raise ValueError("路径过长。")
    if any(
        ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        raise ValueError("路径不能包含控制字符。")
    return value


def _validate_project_structure_arguments(
    arguments: object,
) -> tuple[str | None, int]:
    if not isinstance(arguments, dict) or not set(arguments).issubset(
        {"path", "max_depth"}
    ):
        raise ValueError("项目结构 skill 参数无效。")
    path = validate_workspace_path(arguments.get("path"))
    max_depth = arguments.get("max_depth", 2)
    if (
        isinstance(max_depth, bool)
        or not isinstance(max_depth, int)
        or not 1 <= max_depth <= 4
    ):
        raise ValueError("max_depth 必须是 1 到 4 之间的整数。")
    return path, max_depth


def _validate_search_arguments(
    arguments: object,
) -> tuple[str, str, str | None, int]:
    if not isinstance(arguments, dict) or not set(arguments).issubset(
        {"mode", "query", "path", "max_results"}
    ):
        raise ValueError("工作区搜索 skill 参数无效。")
    mode = arguments.get("mode")
    if mode not in {"filename", "content"}:
        raise ValueError("mode 只能是 filename 或 content。")
    query = arguments.get("query")
    if not isinstance(query, str):
        raise ValueError("搜索内容必须是字符串。")
    query = query.strip()
    if not query:
        raise ValueError("搜索内容不能为空。")
    if len(query) > MAX_SEARCH_QUERY_LENGTH:
        raise ValueError(
            f"搜索内容不能超过 {MAX_SEARCH_QUERY_LENGTH} 个字符。"
        )
    if any(
        ord(character) < 32 or ord(character) == 127
        for character in query
    ):
        raise ValueError("搜索内容不能包含控制字符。")
    path = validate_workspace_path(arguments.get("path"))
    max_results = arguments.get("max_results", DEFAULT_SEARCH_RESULTS)
    if (
        isinstance(max_results, bool)
        or not isinstance(max_results, int)
        or not 1 <= max_results <= MAX_SEARCH_RESULTS
    ):
        raise ValueError(
            f"max_results 必须是 1 到 {MAX_SEARCH_RESULTS} 之间的整数。"
        )
    return mode, query, path, max_results


def validate_workspace_skill_arguments(
    name: str, arguments: object
) -> None:
    if name == "get_project_structure":
        _validate_project_structure_arguments(arguments)
        return
    if name == "search_workspace_files":
        _validate_search_arguments(arguments)
        return
    raise ValueError(f"未知的工作区 skill：{name}")


def _resolve_workspace_directory(
    root: Path, path: str | None
) -> tuple[Path, Path]:
    resolved_root = root.resolve()
    target = (
        resolved_root
        if path in (None, ".")
        else (resolved_root / path).resolve()
    )
    try:
        target.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("只能访问应用工作目录内的路径。") from error
    if not target.is_dir():
        raise ValueError("目录不存在或不是目录。")
    return resolved_root, target


def _relative_workspace_path(root: Path, target: Path) -> str:
    relative = target.relative_to(root)
    return relative.as_posix() if relative.parts else "."


def _entry_is_directory(entry: Path) -> bool:
    try:
        return entry.is_dir()
    except OSError:
        return False


def _list_structure(target: Path, max_depth: int) -> tuple[list[str], bool]:
    lines: list[str] = []
    state = {"entries": 0, "truncated": False}

    def walk(directory: Path, depth: int) -> None:
        if state["truncated"] or depth > max_depth:
            return
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda entry: (
                    not _entry_is_directory(entry),
                    entry.name.lower(),
                ),
            )
        except OSError:
            return
        for entry in entries:
            if state["truncated"]:
                return
            try:
                if entry.is_symlink():
                    continue
            except OSError:
                continue
            is_directory = _entry_is_directory(entry)
            if (
                is_directory
                and entry.name.lower() in IGNORED_DIRECTORY_NAMES
            ):
                continue
            if state["entries"] >= MAX_PROJECT_STRUCTURE_ENTRIES:
                state["truncated"] = True
                return
            lines.append(
                f"{'  ' * (depth - 1)}{entry.name}"
                f"{'/' if is_directory else ''}"
            )
            state["entries"] += 1
            if is_directory:
                walk(entry, depth + 1)

    walk(target, 1)
    return lines, state["truncated"]


def _iter_workspace_files(target: Path, root: Path):
    for directory, directory_names, file_names in os.walk(
        target, followlinks=False
    ):
        current = Path(directory)
        directory_names[:] = sorted(
            (
                name
                for name in directory_names
                if name.lower() not in IGNORED_DIRECTORY_NAMES
                and not (current / name).is_symlink()
            ),
            key=str.lower,
        )
        for file_name in sorted(file_names, key=str.lower):
            path = current / file_name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                path.resolve().relative_to(root)
            except (OSError, ValueError):
                continue
            yield path


def _search_filenames(
    target: Path,
    root: Path,
    query: str,
    max_results: int,
) -> tuple[list[str], bool, int]:
    pattern = (
        query
        if any(character in query for character in "*?")
        else f"*{query}*"
    ).casefold()
    results: list[str] = []
    scanned = 0
    truncated = False
    for path in _iter_workspace_files(target, root):
        if scanned >= MAX_SEARCH_FILES:
            truncated = True
            break
        scanned += 1
        relative = path.relative_to(root).as_posix()
        if not fnmatch.fnmatch(relative.casefold(), pattern):
            continue
        if len(results) >= max_results:
            truncated = True
            break
        results.append(relative)
    return results, truncated, scanned


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030", "cp1252"):
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _search_contents(
    target: Path,
    root: Path,
    query: str,
    max_results: int,
) -> tuple[list[dict[str, object]], bool, int]:
    needle = query.casefold()
    results: list[dict[str, object]] = []
    scanned = 0
    total_bytes = 0
    truncated = False
    for path in _iter_workspace_files(target, root):
        if scanned >= MAX_SEARCH_FILES:
            truncated = True
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > MAX_SEARCH_FILE_BYTES:
            continue
        if total_bytes + size > MAX_SEARCH_TOTAL_BYTES:
            truncated = True
            break
        try:
            data = path.read_bytes()
        except OSError:
            continue
        scanned += 1
        total_bytes += len(data)
        if (
            not data.startswith((b"\xff\xfe", b"\xfe\xff"))
            and b"\0" in data[:1024]
        ):
            continue
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(
            _decode_text(data).splitlines(), 1
        ):
            if needle not in line.casefold():
                continue
            if len(results) >= max_results:
                truncated = True
                break
            text = line.rstrip()
            if len(text) > MAX_SEARCH_LINE_LENGTH:
                text = text[:MAX_SEARCH_LINE_LENGTH] + "…"
            results.append(
                {
                    "path": relative,
                    "line": line_number,
                    "text": text,
                }
            )
        if truncated and len(results) >= max_results:
            break
    return results, truncated, scanned


def invoke_workspace_skill(
    name: str,
    arguments: object,
    *,
    workspace_root: Path | None = None,
) -> str:
    validate_workspace_skill_arguments(name, arguments)
    root = (workspace_root or default_workspace_root()).resolve()
    if name == "get_project_structure":
        path, max_depth = _validate_project_structure_arguments(arguments)
        root, target = _resolve_workspace_directory(root, path)
        relative = _relative_workspace_path(root, target)
        lines, truncated = _list_structure(target, max_depth)
        return json.dumps(
            {
                "path": relative,
                "tree": "\n".join([relative, *lines]),
                "entry_count": len(lines),
                "truncated": truncated,
            },
            ensure_ascii=False,
        )

    mode, query, path, max_results = _validate_search_arguments(arguments)
    root, target = _resolve_workspace_directory(root, path)
    if mode == "filename":
        results, truncated, scanned = _search_filenames(
            target, root, query, max_results
        )
    else:
        results, truncated, scanned = _search_contents(
            target, root, query, max_results
        )
    return json.dumps(
        {
            "mode": mode,
            "query": query,
            "results": results,
            "scanned": scanned,
            "truncated": truncated,
        },
        ensure_ascii=False,
    )
