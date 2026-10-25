import ctypes
import json
import sys
import time
import unicodedata
from ctypes import wintypes
from typing import Protocol


QQ_MUSIC_SKILLS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "control_qq_music",
            "description": (
                "Control the already-open QQ Music desktop app. Supported actions "
                "are 'play_pause', 'previous', 'next', 'stop', 'volume_up', "
                "'volume_down', 'mute_toggle', 'search', 'search_and_play', "
                "'play', and 'play_song'. Use 'search' to search for a song, artist, "
                "or album in the QQ Music window; searching does not automatically "
                "start playback. Use 'search_and_play'/'play'/'play_song' to search "
                "for a target and play the selected search suggestion. Optionally "
                "set 'result_index' (1-10, default 1) to choose which suggestion to "
                "play. Playback is verified from the QQ Music window title; if the "
                "target cannot be confirmed, the skill reports an error instead of "
                "claiming success. Only call when the user explicitly asks to "
                "control or search QQ Music."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "play_pause",
                            "previous",
                            "next",
                            "stop",
                            "volume_up",
                            "volume_down",
                            "mute_toggle",
                            "search",
                            "search_and_play",
                            "play",
                            "play_song",
                        ],
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Song, artist, or album to search for; required only "
                            "when action is a search-based action such as 'search' or "
                            "'search_and_play'."
                        ),
                    },
                    "result_index": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": (
                            "Which search suggestion to play (1-based). Optional; "
                            "only allowed for search_and_play/play/play_song. "
                            "Defaults to 1."
                        ),
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    }
]


_PLAY_ACTIONS = {"search_and_play", "play", "play_song"}
_SEARCH_ONLY_ACTIONS = {"search"}


class _QQMusicController(Protocol):
    def execute(
        self,
        action: str,
        query: str | None = None,
        result_index: int = 1,
    ) -> str: ...


def _validate_result_index(arguments: dict[str, object]) -> int:
    result_index = arguments.get("result_index", 1)
    if (
        isinstance(result_index, bool)
        or not isinstance(result_index, int)
        or not 1 <= result_index <= 10
    ):
        raise ValueError("QQ 音乐 result_index 必须是 1 到 10 之间的整数。")
    return result_index


def validate_qq_music_result_index(arguments: object) -> int:
    if not isinstance(arguments, dict):
        raise ValueError("QQ 音乐 skill 参数无效。")
    return _validate_result_index(arguments)


def _validate_query(query: object) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("QQ 音乐搜索内容不能为空。")
    query = query.strip()
    if len(query) > 200:
        raise ValueError("QQ 音乐搜索内容不能超过 200 个字符。")
    if any(
        ord(character) < 32 or 127 <= ord(character) <= 159
        for character in query
    ):
        raise ValueError("QQ 音乐搜索内容不能包含控制字符。")
    return query


def validate_qq_music_arguments(arguments: object) -> tuple[str, str | None]:
    if not isinstance(arguments, dict):
        raise ValueError("QQ 音乐 skill 参数无效。")
    action = arguments.get("action")
    if action in {
        "play_pause",
        "previous",
        "next",
        "stop",
        "volume_up",
        "volume_down",
        "mute_toggle",
    }:
        if set(arguments) != {"action"}:
            raise ValueError(
                "该 QQ 音乐操作不接受额外参数。"
            )
        return action, None
    if action in _PLAY_ACTIONS:
        if not (
            {"action", "query"}
            <= set(arguments)
            <= {"action", "query", "result_index"}
        ):
            raise ValueError(
                "播放操作必须提供 action 和 query，并且只能额外提供 result_index。"
            )
        query = _validate_query(arguments["query"])
        _validate_result_index(arguments)
        return action, query
    if action in _SEARCH_ONLY_ACTIONS:
        if set(arguments) != {"action", "query"}:
            raise ValueError("搜索操作必须只提供 action 和 query。")
        return action, _validate_query(arguments["query"])
    raise ValueError(
        "QQ 音乐操作仅支持播放/暂停、上一首/下一首、停止、音量调节、"
        "静音切换、搜索、搜索并播放，或按搜索建议播放。"
    )


def invoke_qq_music_skill(
    arguments: object,
    *,
    controller: _QQMusicController | None = None,
) -> str:
    action, query = validate_qq_music_arguments(arguments)
    music_controller = controller or _WindowsQQMusicController()
    if action in _PLAY_ACTIONS:
        result = music_controller.execute(
            action,
            query,
            validate_qq_music_result_index(arguments),
        )
    else:
        result = music_controller.execute(action, query)
    return json.dumps(
        {"application": "QQ 音乐", "action": action, "result": result},
        ensure_ascii=False,
    )


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", _INPUT_UNION)]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class _WindowsQQMusicController:
    INPUT_KEYBOARD = 1
    INPUT_MOUSE = 0
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    VK_BACK = 0x08
    VK_CONTROL = 0x11
    VK_RETURN = 0x0D
    VK_A = 0x41
    VK_DOWN = 0x28
    VK_MEDIA_PLAY_PAUSE = 0xB3
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    SW_RESTORE = 9
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    QQ_MUSIC_EXECUTABLES = {
        "qqmusic.exe",
        "qqmusicmain.exe",
        "qqmusicplayer.exe",
    }

    def execute(
        self, action: str, query: str | None = None, result_index: int = 1
    ) -> str:
        if sys.platform != "win32":
            raise RuntimeError("QQ 音乐桌面 skill 仅支持 Windows。")
        if action == "play_pause":
            if not self._is_qq_music_running():
                raise RuntimeError("未检测到正在运行的 QQ 音乐主程序，请先启动 QQ 音乐。")
            self._send_key(self.VK_MEDIA_PLAY_PAUSE)
            return "已发送 Windows 标准媒体播放/暂停按键。"
        if action in {"previous", "next", "stop", "volume_up", "volume_down", "mute_toggle"}:
            if not self._is_qq_music_running():
                raise RuntimeError("未检测到正在运行的 QQ 音乐主程序，请先启动 QQ 音乐。")
            return self._send_media_action(action)

        if action in _PLAY_ACTIONS:
            if not query:
                raise ValueError("QQ 音乐搜索并播放必须提供歌曲、歌手或专辑名称。")
            if not 1 <= result_index <= 10:
                raise ValueError("QQ 音乐 result_index 必须是 1 到 10 之间的整数。")
            hwnd = self._focus_search_box()
            self._clear_and_type_search(query)
            time.sleep(0.9)
            for _ in range(result_index):
                self._send_key(self.VK_DOWN)
                time.sleep(0.15)
            self._send_key(self.VK_RETURN)
            title = self._wait_for_playback(hwnd, query)
            if title is None:
                current_title = self._window_title(hwnd) or "（无法读取）"
                raise RuntimeError(
                    f"未确认 QQ 音乐开始播放《{query}》。"
                    "可能未匹配到目标歌曲、歌曲需要购买或 VIP，"
                    f"或 QQ 音乐界面未就绪。当前窗口标题：{current_title}"
                )
            return f"已在 QQ 音乐中播放：{query}（当前：{title}）"

        if action == "search":
            if not query:
                raise ValueError("QQ 音乐搜索内容不能为空。")
            self._focus_search_box()
            self._clear_and_type_search(query)
            time.sleep(0.9)
            self._send_key(self.VK_RETURN)
            time.sleep(0.5)
            return f"已在 QQ 音乐中搜索：{query}"

        raise ValueError(f"不支持的 QQ 音乐控制动作：{action}")

    def _focus_qq_music_window(self) -> None:
        self._activate_window(self._find_qq_music_window())

    def _focus_search_box(self) -> int:
        self._ensure_dpi_awareness()
        hwnd = self._find_qq_music_window()
        self._activate_window(hwnd)
        bounds = self._window_bounds(hwnd)
        width = bounds.right - bounds.left
        if width < 500:
            raise RuntimeError(
                "QQ 音乐窗口过窄，无法定位搜索框；请将窗口调宽后重试。"
            )
        self._click(bounds.left + 30, bounds.top + 35)
        time.sleep(0.35)
        bounds = self._window_bounds(hwnd)
        search_x = bounds.left + min(400, max(0, width - 120))
        search_y = bounds.top + 37
        self._click(search_x, search_y)
        time.sleep(0.15)
        self._click(search_x, search_y)
        time.sleep(0.15)
        return hwnd

    @classmethod
    def _activate_window(cls, hwnd: int) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AttachThreadInput.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.BOOL,
        ]
        user32.AttachThreadInput.restype = wintypes.BOOL
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        bounds = cls._window_bounds(hwnd)
        if (
            user32.IsIconic(wintypes.HWND(hwnd))
            or bounds.left <= -30000
            or bounds.top <= -30000
        ):
            user32.ShowWindow(wintypes.HWND(hwnd), cls.SW_RESTORE)
            time.sleep(0.35)

        user32.SetForegroundWindow(wintypes.HWND(hwnd))
        time.sleep(0.2)
        if int(user32.GetForegroundWindow()) == hwnd:
            return

        foreground = user32.GetForegroundWindow()
        foreground_process_id = wintypes.DWORD()
        foreground_thread = user32.GetWindowThreadProcessId(
            wintypes.HWND(foreground),
            ctypes.byref(foreground_process_id),
        )
        current_thread = kernel32.GetCurrentThreadId()
        attached = False
        if foreground_thread and foreground_thread != current_thread:
            attached = bool(
                user32.AttachThreadInput(
                    current_thread,
                    foreground_thread,
                    True,
                )
            )
        try:
            user32.SetForegroundWindow(wintypes.HWND(hwnd))
            time.sleep(0.2)
        finally:
            if attached:
                user32.AttachThreadInput(
                    current_thread,
                    foreground_thread,
                    False,
                )
        if int(user32.GetForegroundWindow()) != hwnd:
            raise RuntimeError("无法将 QQ 音乐窗口切换到前台，请先手动激活该窗口。")

    @staticmethod
    def _window_bounds(hwnd: int) -> wintypes.RECT:
        rect = wintypes.RECT()
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        get_attribute = dwmapi.DwmGetWindowAttribute
        get_attribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        get_attribute.restype = ctypes.c_long
        if get_attribute(
            wintypes.HWND(hwnd),
            _WindowsQQMusicController.DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        ) != 0:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetWindowRect.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.RECT),
            ]
            user32.GetWindowRect.restype = wintypes.BOOL
            if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                raise RuntimeError(
                    "无法读取 QQ 音乐窗口位置"
                    f"（Windows 错误 {ctypes.get_last_error()}）。"
                )
        return rect

    @staticmethod
    def _window_title(hwnd: int) -> str:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_window_text = user32.GetWindowTextW
        get_window_text.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        get_window_text.restype = ctypes.c_int
        buffer = ctypes.create_unicode_buffer(512)
        get_window_text(wintypes.HWND(hwnd), buffer, len(buffer))
        return buffer.value.strip()

    def _clear_and_type_search(self, query: str) -> None:
        self._send_hotkey(self.VK_CONTROL, self.VK_A)
        time.sleep(0.1)
        self._send_key(self.VK_BACK)
        time.sleep(0.15)
        self._send_unicode_text(query)

    def _wait_for_playback(self, hwnd: int, query: str) -> str | None:
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            title = self._window_title(hwnd)
            if title and self._title_matches_query(title, query):
                return title
            time.sleep(0.3)
        return None

    @staticmethod
    def _title_matches_query(title: str, query: str) -> bool:
        normalized_title = unicodedata.normalize("NFKC", title).casefold()
        tokens = [
            token
            for token in unicodedata.normalize("NFKC", query).casefold().split()
            if token
        ]
        meaningful = [
            token for token in tokens if len(token) >= 2 or not token.isascii()
        ]
        if meaningful:
            return all(token in normalized_title for token in meaningful)
        return bool(tokens) and tokens[0] in normalized_title

    @classmethod
    def _click(cls, x: int, y: int) -> None:
        cls._ensure_dpi_awareness()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        user32.SetCursorPos.restype = wintypes.BOOL
        if not user32.SetCursorPos(x, y):
            raise RuntimeError(
                "无法移动鼠标到 QQ 音乐窗口"
                f"（Windows 错误 {ctypes.get_last_error()}）。"
            )
        cls._send_inputs(
            [
                _INPUT(
                    type=cls.INPUT_MOUSE,
                    mi=_MOUSEINPUT(dwFlags=cls.MOUSEEVENTF_LEFTDOWN),
                ),
                _INPUT(
                    type=cls.INPUT_MOUSE,
                    mi=_MOUSEINPUT(dwFlags=cls.MOUSEEVENTF_LEFTUP),
                ),
            ]
        )

    @staticmethod
    def _ensure_dpi_awareness() -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        try:
            set_context = user32.SetProcessDpiAwarenessContext
        except AttributeError:
            try:
                user32.SetProcessDPIAware()
            except AttributeError:
                pass
            return
        set_context.argtypes = [wintypes.HANDLE]
        set_context.restype = wintypes.BOOL
        try:
            set_context(wintypes.HANDLE(-4))
        except (ctypes.ArgumentError, OverflowError, OSError):
            pass

    @classmethod
    def _send_media_action(cls, action: str) -> str:
        key_map = {
            "previous": (0xB1,),
            "next": (0xB0,),
            "stop": (0xB2,),
            "volume_up": (0xAF,),
            "volume_down": (0xAE,),
            "mute_toggle": (0xAD,),
        }
        if action not in key_map:
            raise ValueError(f"不支持的 QQ 音乐控制动作：{action}")
        cls._send_key(key_map[action][0])
        labels = {
            "previous": "上一首",
            "next": "下一首",
            "stop": "停止",
            "volume_up": "音量加",
            "volume_down": "音量减",
            "mute_toggle": "静音切换",
        }
        return f"已发送 QQ 音乐{labels[action]}命令。"

    @classmethod
    def _qq_music_process_ids(cls) -> set[int]:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_snapshot = kernel32.CreateToolhelp32Snapshot
        create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        create_snapshot.restype = wintypes.HANDLE
        process_first = kernel32.Process32FirstW
        process_first.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_PROCESSENTRY32W),
        ]
        process_first.restype = wintypes.BOOL
        process_next = kernel32.Process32NextW
        process_next.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_PROCESSENTRY32W),
        ]
        process_next.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        snapshot = create_snapshot(cls.TH32CS_SNAPPROCESS, 0)
        if snapshot == cls.INVALID_HANDLE_VALUE:
            error_code = ctypes.get_last_error()
            raise RuntimeError(
                f"无法检查 QQ 音乐进程（Windows 错误 {error_code}）。"
            )
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not process_first(snapshot, ctypes.byref(entry)):
                error_code = ctypes.get_last_error()
                if error_code == 18:
                    return set()
                raise RuntimeError(
                    f"无法读取运行中的进程（Windows 错误 {error_code}）。"
                )

            process_ids: set[int] = set()
            while True:
                if entry.szExeFile.casefold() in cls.QQ_MUSIC_EXECUTABLES:
                    process_ids.add(entry.th32ProcessID)
                entry.dwSize = ctypes.sizeof(entry)
                if not process_next(snapshot, ctypes.byref(entry)):
                    error_code = ctypes.get_last_error()
                    if error_code != 18:
                        raise RuntimeError(
                            f"无法读取运行中的进程（Windows 错误 {error_code}）。"
                        )
                    return process_ids
        finally:
            close_handle(snapshot)

    @classmethod
    def _is_qq_music_running(cls) -> bool:
        return bool(cls._qq_music_process_ids())

    @staticmethod
    def _find_qq_music_window() -> int:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        process_ids = _WindowsQQMusicController._qq_music_process_ids()
        if not process_ids:
            raise RuntimeError("未检测到正在运行的 QQ 音乐主程序，请先启动 QQ 音乐。")

        enum_windows = user32.EnumWindows
        enum_windows.argtypes = [
            ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
            wintypes.LPARAM,
        ]
        enum_windows.restype = wintypes.BOOL
        is_window_visible = user32.IsWindowVisible
        is_window_visible.argtypes = [wintypes.HWND]
        is_window_visible.restype = wintypes.BOOL
        get_window_process_id = user32.GetWindowThreadProcessId
        get_window_process_id.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        get_window_process_id.restype = wintypes.DWORD
        get_window_text = user32.GetWindowTextW
        get_window_text.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        get_window_text.restype = ctypes.c_int
        get_class_name = user32.GetClassNameW
        get_class_name.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        get_class_name.restype = ctypes.c_int
        get_window_rect = user32.GetWindowRect
        get_window_rect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        get_window_rect.restype = wintypes.BOOL

        matches: list[tuple[int, int]] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def collect_window(hwnd: int, _parameter: int) -> bool:
            if not is_window_visible(hwnd):
                return True
            process_id = wintypes.DWORD()
            get_window_process_id(hwnd, ctypes.byref(process_id))
            if process_id.value not in process_ids:
                return True
            class_name = ctypes.create_unicode_buffer(256)
            get_class_name(hwnd, class_name, len(class_name))
            if class_name.value != "TXGuiFoundation":
                return True
            title = ctypes.create_unicode_buffer(512)
            get_window_text(hwnd, title, len(title))
            rect = wintypes.RECT()
            get_window_rect(hwnd, ctypes.byref(rect))
            area = max(0, rect.right - rect.left) * max(
                0, rect.bottom - rect.top
            )
            if not title.value.strip() and area < 100_000:
                return True
            matches.append((area, int(hwnd)))
            return True

        enum_windows(collect_window, 0)
        if not matches:
            raise RuntimeError(
                "检测到 QQ 音乐主程序正在运行，但未找到可操作的主窗口；"
                "请先打开 QQ 音乐主窗口。"
            )
        return max(matches)[1]

    @classmethod
    def _input(
        cls, key: int, *, key_up: bool = False, unicode: bool = False
    ) -> _INPUT:
        flags = (cls.KEYEVENTF_KEYUP if key_up else 0) | (
            cls.KEYEVENTF_UNICODE if unicode else 0
        )
        return _INPUT(
            type=cls.INPUT_KEYBOARD,
            ki=_KEYBDINPUT(
                wVk=0 if unicode else key,
                wScan=key if unicode else 0,
                dwFlags=flags,
            ),
        )

    @classmethod
    def _send_inputs(cls, inputs: list[_INPUT]) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        send_input = user32.SendInput
        send_input.argtypes = [
            wintypes.UINT,
            ctypes.POINTER(_INPUT),
            ctypes.c_int,
        ]
        send_input.restype = wintypes.UINT
        input_array = (_INPUT * len(inputs))(*inputs)
        sent = send_input(len(inputs), input_array, ctypes.sizeof(_INPUT))
        if sent != len(inputs):
            error_code = ctypes.get_last_error()
            raise RuntimeError(
                f"QQ 音乐按键输入失败（Windows 错误 {error_code}，"
                f"已发送 {sent}/{len(inputs)} 个事件）。"
            )

    @classmethod
    def _send_key(cls, key: int) -> None:
        cls._send_inputs([cls._input(key), cls._input(key, key_up=True)])

    @classmethod
    def _send_hotkey(cls, modifiers: int | tuple[int, ...], key: int) -> None:
        if isinstance(modifiers, int):
            modifiers = (modifiers,)
        cls._send_inputs(
            [cls._input(modifier) for modifier in modifiers]
            + [cls._input(key), cls._input(key, key_up=True)]
            + [cls._input(modifier, key_up=True) for modifier in reversed(modifiers)]
        )

    @classmethod
    def _send_unicode_text(cls, text: str) -> None:
        encoded_text = text.encode("utf-16-le")
        units = [
            int.from_bytes(encoded_text[index : index + 2], "little")
            for index in range(0, len(encoded_text), 2)
        ]
        inputs = [
            event
            for unit in units
            for event in (
                cls._input(unit, unicode=True),
                cls._input(unit, key_up=True, unicode=True),
            )
        ]
        if inputs:
            cls._send_inputs(inputs)
