from __future__ import annotations

import json
import re
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Callable

import settings_store
from responses_adapter import RESPONSES_METADATA, valid_metadata

SESSION_VERSION = 1
SESSION_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
KIND_MAIN = "main"
KIND_BRAINSTORM = "brainstorm"
KIND_PARTICIPANT = "brainstorm_participant"
SESSION_KINDS = {KIND_MAIN, KIND_BRAINSTORM, KIND_PARTICIPANT}
STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_INTERRUPTED = "interrupted"
STATUS_FAILED = "failed"
SESSION_STATUSES = {
    STATUS_IDLE,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_INTERRUPTED,
    STATUS_FAILED,
}
MESSAGE_KINDS = {"text", "brainstorm", "brainstorm_summary"}
AUTO_TITLE_LENGTH = 30
MAX_TITLE_LENGTH = 60


def new_id() -> str:
    return uuid.uuid4().hex


def is_valid_session_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and SESSION_ID_PATTERN.fullmatch(value) is not None
    )


def sessions_directory() -> Path:
    return settings_store.settings_file_path().parent / "sessions"


def build_session_title(
    text: str,
    *,
    fallback: str = "新对话",
    limit: int = AUTO_TITLE_LENGTH,
) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed:
        return fallback
    return collapsed[:limit]


def new_message(
    role: str,
    content: object,
    *,
    display: str | None = None,
    attachments: list[str] | None = None,
    media_paths: list[Path | str] | None = None,
    error: str | None = None,
    muted: bool = False,
    visible: bool = True,
    in_api: bool = True,
    kind: str = "text",
    brainstorm_session_id: str | None = None,
    retry_user_message_id: str | None = None,
    tool_calls: list[dict[str, object]] | None = None,
    tool_results: list[dict[str, object]] | None = None,
    tool_call_id: str | None = None,
    responses_metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    message = {
        "id": new_id(),
        "role": role,
        "content": content,
        "display": (
            display
            if display is not None
            else (content if isinstance(content, str) else "")
        ),
        "attachments": list(attachments or []),
        "media_paths": [str(path) for path in (media_paths or [])],
        "error": error,
        "muted": muted,
        "visible": visible,
        "in_api": in_api,
        "kind": kind,
        "brainstorm_session_id": brainstorm_session_id,
        "retry_user_message_id": retry_user_message_id,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "tool_call_id": tool_call_id,
        "created_at": time.time(),
    }
    if responses_metadata is not None:
        message[RESPONSES_METADATA] = deepcopy(responses_metadata)
    return message


def build_api_messages(
    session: dict[str, object], system_prompt: str | None = None
) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    stored = session.get("messages")
    if not isinstance(stored, list):
        return messages
    for message in stored:
        if not isinstance(message, dict) or not message.get("in_api", True):
            continue
        role = message.get("role")
        if role not in {"system", "developer", "user", "assistant", "tool"}:
            continue
        if system_prompt and role == "system":
            continue
        api_message = {"role": role, "content": message.get("content")}
        if role == "assistant":
            if message.get("tool_calls"):
                api_message["tool_calls"] = deepcopy(message["tool_calls"])
            if RESPONSES_METADATA in message:
                api_message[RESPONSES_METADATA] = deepcopy(message[RESPONSES_METADATA])
        elif role == "tool":
            api_message["tool_call_id"] = message.get("tool_call_id")
        messages.append(api_message)
    return messages


class SessionStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else sessions_directory()
        self._lock = threading.RLock()
        self.last_scan_errors: list[str] = []

    def _path(self, session_id: str) -> Path:
        if not is_valid_session_id(session_id):
            raise ValueError("会话 id 无效。")
        return self.root / f"{session_id}.json"

    def new_session(
        self,
        kind: str = KIND_MAIN,
        parent_id: str | None = None,
        title: str = "",
        agent: str = "",
        brainstorm: dict[str, object] | None = None,
        permanent_workspace: str | None = None,
    ) -> dict[str, object]:
        if kind not in SESSION_KINDS:
            raise ValueError("会话类型无效。")
        if parent_id is not None and not is_valid_session_id(parent_id):
            raise ValueError("父会话 id 无效。")
        now = time.time()
        return {
            "version": SESSION_VERSION,
            "id": new_id(),
            "parent_id": parent_id,
            "kind": kind,
            "title": title.strip() or ("新对话" if kind == KIND_MAIN else ""),
            "agent": agent.strip(),
            "status": STATUS_IDLE,
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "brainstorm": brainstorm,
            "permanent_workspace": permanent_workspace,
        }

    def load(self, session_id: str) -> dict[str, object]:
        with self._lock:
            path = self._path(session_id)
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError as error:
                raise RuntimeError(f"会话不存在：{session_id}") from error
            except UnicodeDecodeError as error:
                raise RuntimeError(f"会话文件损坏：{session_id}") from error
            except OSError as error:
                raise RuntimeError(f"无法读取会话：{session_id}") from error
            try:
                session = json.loads(text)
                self._validate_session(session)
            except (json.JSONDecodeError, ValueError) as error:
                raise RuntimeError(f"会话文件损坏：{session_id}") from error
            if session["id"] != session_id:
                raise RuntimeError(f"会话文件损坏：{session_id}")
            return session

    def save(self, session: dict[str, object]) -> None:
        self._validate_session(session)
        session["updated_at"] = time.time()
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(str(session["id"]))
            temporary = path.with_name(path.name + ".tmp")
            try:
                temporary.write_text(
                    json.dumps(session, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temporary.replace(path)
            except OSError as error:
                temporary.unlink(missing_ok=True)
                raise RuntimeError(
                    f"无法保存会话：{session['id']}"
                ) from error

    def update(
        self,
        session_id: str,
        mutator: Callable[[dict[str, object]], None],
    ) -> dict[str, object]:
        with self._lock:
            session = self.load(session_id)
            mutator(session)
            self.save(session)
            return session

    def delete(self, session_id: str) -> None:
        with self._lock:
            path = self._path(session_id)
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                raise RuntimeError(
                    f"无法删除会话：{session_id}"
                ) from error

    def _load_all_unlocked(self) -> list[dict[str, object]]:
        self.last_scan_errors = []
        if not self.root.exists():
            return []
        sessions: list[dict[str, object]] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._validate_session(data)
                if path.stem != data["id"]:
                    raise ValueError(
                        f"会话 id 与文件名不一致：{data['id']}"
                    )
            except (
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                ValueError,
            ) as error:
                self.last_scan_errors.append(f"{path.name}: {error}")
                continue
            sessions.append(data)
        return sessions

    def list_sessions(
        self, kind: str | None = None
    ) -> list[dict[str, object]]:
        with self._lock:
            sessions = self._load_all_unlocked()
        if kind is not None:
            sessions = [
                session
                for session in sessions
                if session.get("kind") == kind
            ]
        summaries = [self._summarize(session) for session in sessions]
        summaries.sort(
            key=lambda item: float(item.get("updated_at", 0)),
            reverse=True,
        )
        return summaries

    @staticmethod
    def _summarize(session: dict[str, object]) -> dict[str, object]:
        messages = session.get("messages")
        stored = messages if isinstance(messages, list) else []
        visible_count = sum(
            1
            for message in stored
            if isinstance(message, dict) and message.get("visible", True)
        )
        brainstorm_count = sum(
            1
            for message in stored
            if isinstance(message, dict)
            and message.get("kind") == "brainstorm"
        )
        return {
            "id": session.get("id"),
            "parent_id": session.get("parent_id"),
            "kind": session.get("kind"),
            "title": session.get("title"),
            "agent": session.get("agent"),
            "status": session.get("status"),
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
            "message_count": visible_count,
            "brainstorm_count": brainstorm_count,
        }

    def delete_children(self, session_id: str) -> None:
        self._path(session_id)
        with self._lock:
            sessions = self._load_all_unlocked()
            descendants: set[str] = set()
            parents = {session_id}
            changed = True
            while changed:
                changed = False
                for session in sessions:
                    parent_id = session.get("parent_id")
                    child_id = session.get("id")
                    if (
                        parent_id in parents
                        and isinstance(child_id, str)
                        and child_id != session_id
                        and child_id not in descendants
                    ):
                        descendants.add(child_id)
                        changed = True
                parents |= descendants
            for target in descendants:
                self.delete(target)

    def delete_tree(self, session_id: str) -> None:
        self._path(session_id)
        with self._lock:
            self.delete_children(session_id)
            self.delete(session_id)

    @staticmethod
    def _validate_session(session: object) -> None:
        if not isinstance(session, dict):
            raise ValueError("会话格式无效。")
        if session.get("version") != SESSION_VERSION:
            raise ValueError("会话版本无效。")
        if not is_valid_session_id(session.get("id")):
            raise ValueError("会话 id 无效。")
        parent_id = session.get("parent_id")
        if parent_id is not None and not is_valid_session_id(parent_id):
            raise ValueError("父会话 id 无效。")
        if parent_id == session.get("id"):
            raise ValueError("父会话不能是自身。")
        kind = session.get("kind")
        if kind not in SESSION_KINDS:
            raise ValueError("会话类型无效。")
        if session.get("status") not in SESSION_STATUSES:
            raise ValueError("会话状态无效。")
        if not isinstance(session.get("title"), str):
            raise ValueError("会话标题格式无效。")
        if not isinstance(session.get("agent"), str):
            raise ValueError("会话代理格式无效。")
        pw = session.get("permanent_workspace")
        if pw is not None and not isinstance(pw, str):
            raise ValueError("会话字段 permanent_workspace 无效。")
        for field in ("created_at", "updated_at"):
            value = session.get(field)
            if isinstance(value, bool) or not isinstance(
                value, (int, float)
            ):
                raise ValueError("会话时间格式无效。")
        messages = session.get("messages")
        if not isinstance(messages, list):
            raise ValueError("会话消息格式无效。")

        if kind == KIND_BRAINSTORM:
            SessionStore._validate_turn_records(messages)
            SessionStore._validate_brainstorm_metadata(
                session.get("brainstorm")
            )
        else:
            SessionStore._validate_messages(messages)
            brainstorm = session.get("brainstorm")
            if brainstorm is not None and not isinstance(brainstorm, dict):
                raise ValueError("会话头脑风暴元数据格式无效。")

    @staticmethod
    def _validate_messages(messages: list[object]) -> None:
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("消息格式无效。")
            if not is_valid_session_id(message.get("id")):
                raise ValueError("消息 id 无效。")
            if message.get("role") not in {"system", "developer", "user", "assistant", "tool"}:
                raise ValueError("消息角色无效。")
            if message.get("role") == "tool":
                tool_call_id = message.get("tool_call_id")
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    raise ValueError("工具结果消息缺少 tool_call_id。")
            tool_calls = message.get("tool_calls")
            if tool_calls is not None:
                if not isinstance(tool_calls, list):
                    raise ValueError("工具调用列表格式无效。")
                for call in tool_calls:
                    if not isinstance(call, dict):
                        raise ValueError("工具调用项格式无效。")
                    if not isinstance(call.get("id"), str) or not call.get("id"):
                        raise ValueError("工具调用缺少 id。")
            if message.get("kind") not in MESSAGE_KINDS:
                raise ValueError("消息类型无效。")
            if RESPONSES_METADATA in message and (
                message.get("role") != "assistant"
                or not valid_metadata(message[RESPONSES_METADATA])
            ):
                raise ValueError("消息 Responses 元数据格式无效。")
            if not isinstance(message.get("content"), (str, list)) and not (
                message.get("role") == "assistant"
                and message.get("content") is None
                and tool_calls
            ):
                raise ValueError("消息内容格式无效。")
            if not isinstance(message.get("display"), str):
                raise ValueError("消息显示文本格式无效。")
            if not isinstance(message.get("attachments"), list):
                raise ValueError("消息附件格式无效。")
            if not isinstance(message.get("media_paths"), list):
                raise ValueError("消息媒体格式无效。")
            if not isinstance(message.get("muted"), bool):
                raise ValueError("消息静音标记格式无效。")
            if not isinstance(message.get("visible"), bool):
                raise ValueError("消息可见标记格式无效。")
            if not isinstance(message.get("in_api"), bool):
                raise ValueError("消息上下文标记格式无效。")
            error = message.get("error")
            if error is not None and not isinstance(error, str):
                raise ValueError("消息错误格式无效。")
            brainstorm_id = message.get("brainstorm_session_id")
            if brainstorm_id is not None and not is_valid_session_id(
                brainstorm_id
            ):
                raise ValueError("消息头脑风暴会话 id 无效。")
            retry_id = message.get("retry_user_message_id")
            if retry_id is not None and not is_valid_session_id(retry_id):
                raise ValueError("消息重试用户消息 id 无效。")
            created_at = message.get("created_at")
            if isinstance(created_at, bool) or not isinstance(
                created_at, (int, float)
            ):
                raise ValueError("消息时间格式无效。")

    @staticmethod
    def _validate_turn_records(messages: list[object]) -> None:
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("轮次记录格式无效。")
            if not is_valid_session_id(message.get("id")):
                raise ValueError("轮次记录 id 无效。")
            round_number = message.get("round")
            if isinstance(round_number, bool) or not isinstance(
                round_number, int
            ):
                raise ValueError("轮次编号格式无效。")
            if not is_valid_session_id(message.get("participant_session_id")):
                raise ValueError("参与者子会话 id 无效。")
            if not isinstance(message.get("participant_name"), str):
                raise ValueError("参与者名称格式无效。")
            if message.get("kind") not in {"speak", "skip", "error"}:
                raise ValueError("轮次记录类型无效。")
            if not isinstance(message.get("content"), str):
                raise ValueError("轮次记录内容格式无效。")
            error = message.get("error")
            if error is not None and not isinstance(error, str):
                raise ValueError("轮次记录错误格式无效。")
            created_at = message.get("created_at")
            if isinstance(created_at, bool) or not isinstance(
                created_at, (int, float)
            ):
                raise ValueError("轮次记录时间格式无效。")

    @staticmethod
    def _validate_brainstorm_metadata(metadata: object) -> None:
        if not isinstance(metadata, dict):
            raise ValueError("头脑风暴元数据格式无效。")
        for field in ("topic", "focus", "summary"):
            if not isinstance(metadata.get(field, ""), str):
                raise ValueError("头脑风暴文本字段格式无效。")
        rounds = metadata.get("rounds")
        if isinstance(rounds, bool) or not isinstance(rounds, int):
            raise ValueError("头脑风暴轮数格式无效。")
        if not isinstance(metadata.get("participants", []), list):
            raise ValueError("头脑风暴参与者格式无效。")
        if not isinstance(metadata.get("key_disagreements", []), list):
            raise ValueError("头脑风暴分歧格式无效。")
