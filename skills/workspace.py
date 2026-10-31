import fnmatch
import json
import os
import tempfile
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
MAX_WRITE_CONTENT_BYTES = 512 * 1024
MAX_EDIT_OLD_STRING_LENGTH = 4000
MAX_EDIT_NEW_STRING_LENGTH = 4000
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
    {
        "type": "function",
        "function": {
            "name": "write_workspace_file",
            "description": (
                "Create or overwrite a file inside the app workspace. Use when "
                "the user explicitly asks to create a new file or replace an "
                "existing one. For a file that already exists, set "
                "overwrite=true to replace it; without overwrite the call is "
                "refused. This skill is restricted to the app workspace root "
                "and must be approved by the workspace review model."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "File path relative to the app workspace. "
                            "Intermediate directories are created as needed."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete file content to write.",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": (
                            "Set true to replace a file that already exists. "
                            "Defaults to false, which refuses existing files."
                        ),
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_workspace_file",
            "description": (
                "Replace an exact string inside an existing file in the app "
                "workspace. oldString must match exactly one location; set "
                "replaceAll=true to replace every occurrence. Use this for "
                "targeted changes instead of rewriting a whole file. This "
                "skill is restricted to the app workspace root and must be "
                "approved by the workspace review model."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "File path relative to the app workspace. The "
                            "file must already exist."
                        ),
                    },
                    "oldString": {
                        "type": "string",
                        "description": "Exact existing text to replace.",
                    },
                    "newString": {
                        "type": "string",
                        "description": "Replacement text; must differ from oldString.",
                    },
                    "replaceAll": {
                        "type": "boolean",
                        "description": (
                            "Set true to replace every occurrence of "
                            "oldString. Defaults to false (single match)."
                        ),
                    },
                },
                "required": ["path", "oldString", "newString"],
                "additionalProperties": False,
            },
        },
    },
]


def default_workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent


def scratch_workspace_root() -> Path:
    import tempfile
    return Path(tempfile.gettempdir()) / "prana-scratch"


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
    if name == "write_workspace_file":
        _validate_write_file_arguments(arguments)
        return
    if name == "edit_workspace_file":
        _validate_edit_file_arguments(arguments)
        return
    raise ValueError(f"未知的工作区 skill：{name}")


def _validate_write_file_arguments(arguments: object) -> None:
    if not isinstance(arguments, dict) or not set(arguments).issubset(
        {"path", "content", "overwrite"}
    ):
        raise ValueError("写文件 skill 参数无效。")
    path = arguments.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("路径必须是有效字符串。")
    content = arguments.get("content")
    if not isinstance(content, str) or not content:
        raise ValueError("文件内容不能为空。")
    if len(content.encode("utf-8")) > MAX_WRITE_CONTENT_BYTES:
        raise ValueError(
            f"文件内容不能超过 {MAX_WRITE_CONTENT_BYTES} 字节。"
        )
    overwrite = arguments.get("overwrite", False)
    if not isinstance(overwrite, bool):
        raise ValueError("overwrite 必须是布尔值。")


def _validate_edit_file_arguments(arguments: object) -> None:
    if not isinstance(arguments, dict) or not set(arguments).issubset(
        {"path", "oldString", "newString", "replaceAll"}
    ):
        raise ValueError("编辑文件 skill 参数无效。")
    path = arguments.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("路径必须是有效字符串。")
    old_string = arguments.get("oldString")
    if not isinstance(old_string, str) or not old_string:
        raise ValueError("oldString 不能为空。")
    if len(old_string.encode("utf-8")) > MAX_EDIT_OLD_STRING_LENGTH:
        raise ValueError(
            f"oldString 不能超过 {MAX_EDIT_OLD_STRING_LENGTH} 字节。"
        )
    new_string = arguments.get("newString")
    if not isinstance(new_string, str):
        raise ValueError("newString 必须是字符串。")
    if len(new_string.encode("utf-8")) > MAX_EDIT_NEW_STRING_LENGTH:
        raise ValueError(
            f"newString 不能超过 {MAX_EDIT_NEW_STRING_LENGTH} 字节。"
        )
    if old_string == new_string:
        raise ValueError("newString 必须与 oldString 不同。")
    replace_all = arguments.get("replaceAll", False)
    if not isinstance(replace_all, bool):
        raise ValueError("replaceAll 必须是布尔值。")


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


def _resolve_workspace_file(
    root: Path, path: str
) -> tuple[Path, Path]:
    resolved_root = root.resolve()
    target = (resolved_root / path).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("只能访问应用工作目录内的路径。") from error
    return resolved_root, target


def _atomic_write_text(target: Path, content: str) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    with tempfile.NamedTemporaryFile(
        dir=target.parent, delete=False, suffix=".tmp", prefix=".write-"
    ) as handle:
        handle.write(data)
        temporary_path = Path(handle.name)
    try:
        os.replace(temporary_path, target)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return len(data)


def _write_workspace_file(root: Path, arguments: object) -> str:
    assert isinstance(arguments, dict)
    path = str(arguments["path"])
    content = str(arguments["content"])
    overwrite = bool(arguments.get("overwrite", False))
    resolved_root, target = _resolve_workspace_file(root, path)
    existed = target.is_file()
    if existed and not overwrite:
        raise ValueError(
            f"文件已存在：{path}。如需覆盖，请设置 overwrite=true。"
        )
    if target.is_dir():
        raise ValueError("目标路径是一个目录，无法写入文件。")
    relative = target.relative_to(resolved_root).as_posix()
    byte_count = _atomic_write_text(target, content)
    return json.dumps(
        {
            "path": relative,
            "created": not existed,
            "overwritten": existed,
            "bytes_written": byte_count,
        },
        ensure_ascii=False,
    )


def _count_occurrences(text: str, old_string: str, start: int = 0) -> int:
    count = 0
    index = text.find(old_string, start)
    while index != -1:
        count += 1
        index = text.find(old_string, index + max(len(old_string), 1))
    return count


def _edit_workspace_file(root: Path, arguments: object) -> str:
    assert isinstance(arguments, dict)
    path = str(arguments["path"])
    old_string = str(arguments["oldString"])
    new_string = str(arguments["newString"])
    replace_all = bool(arguments.get("replaceAll", False))
    resolved_root, target = _resolve_workspace_file(root, path)
    relative = target.relative_to(resolved_root).as_posix()
    if not target.is_file():
        raise ValueError(f"文件未找到：{path}。")
    text = target.read_text(encoding="utf-8-sig", errors="strict")
    occurrences = _count_occurrences(text, old_string)
    if occurrences == 0:
        raise ValueError(f"oldString 未找到：{path}。")
    if occurrences > 1 and not replace_all:
        raise ValueError(
            f"oldString 在 {path} 中出现 {occurrences} 处，"
            "但期望恰好 1 处。请提供更多上下文，或设置 replaceAll=true。"
        )
    replacements = occurrences if replace_all else 1
    updated = (
        text.replace(old_string, new_string)
        if replace_all
        else text.replace(old_string, new_string, 1)
    )
    _atomic_write_text(target, updated)
    return json.dumps(
        {
            "path": relative,
            "replacements": replacements,
        },
        ensure_ascii=False,
    )


def resolve_workspace_root(
    workspace: str,
    permanent_root: Path | None = None,
    scratch_root: Path | None = None,
) -> Path:
    if workspace not in {"permanent", "scratch"}:
        raise ValueError("workspace 只能是 permanent 或 scratch。")
    if workspace == "scratch":
        root = (scratch_root or scratch_workspace_root()).resolve()
    else:
        root = (permanent_root or default_workspace_root()).resolve()
    if not root.is_dir():
        raise ValueError("工作目录不存在或不是目录。")
    return root


def invoke_workspace_skill(
    name: str,
    arguments: object,
    *,
    workspace_root: Path | None = None,
) -> str:
    validate_workspace_skill_arguments(name, arguments)
    root = (workspace_root or default_workspace_root()).resolve()
    if name == "write_workspace_file":
        return _write_workspace_file(root, arguments)
    if name == "edit_workspace_file":
        return _edit_workspace_file(root, arguments)
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
