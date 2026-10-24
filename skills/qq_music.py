import ctypes
import json
import sys
import time
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
                "for a target song and then select/play the first result. Only call "
                "when the user explicitly asks to control or search QQ Music."
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
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    }
]


_SEARCH_ACTIONS = {"search", "search_and_play", "play", "play_song"}
_SEARCH_AND_PLAY_ACTIONS = {"search_and_play", "play", "play_song"}


class _QQMusicController(Protocol):
    def execute(self, action: str, query: str | None = None) -> str: ...


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
    if action in _SEARCH_ACTIONS:
        if set(arguments) != {"action", "query"}:
            raise ValueError("搜索操作必须只提供 action 和 query。")
        query = arguments["query"]
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
        return action, query
    raise ValueError("QQ 音乐操作仅支持播放/暂停、上一首/下一首、停止、音量调节、静音切换、搜索或搜索并播放。")


def invoke_qq_music_skill(
    arguments: object,
    *,
    controller: _QQMusicController | None = None,
) -> str:
    action, query = validate_qq_music_arguments(arguments)
    music_controller = controller or _WindowsQQMusicController()
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
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    VK_CONTROL = 0x11
    VK_ALT = 0x12
    VK_F = 0x46
    VK_RETURN = 0x0D
    VK_MEDIA_PLAY_PAUSE = 0xB3
    SW_RESTORE = 9
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    QQ_MUSIC_EXECUTABLES = {
        "qqmusic.exe",
        "qqmusicmain.exe",
        "qqmusicplayer.exe",
    }

    def execute(self, action: str, query: str | None = None) -> str:
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

        if action in _SEARCH_AND_PLAY_ACTIONS:
            if not query:
                raise ValueError("QQ 音乐搜索并播放必须提供歌曲、歌手或专辑名称。")
            self._focus_qq_music_window()
            self._send_hotkey(self.VK_CONTROL, self.VK_F)
            time.sleep(0.15)
            self._send_unicode_text(query)
            self._send_key(self.VK_RETURN)
            time.sleep(0.25)
            self._send_key(self.VK_RETURN)
            return f"已在 QQ 音乐中搜索并播放：{query}"

        hwnd = self._find_qq_music_window()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.ShowWindow(hwnd, self.SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        if user32.GetForegroundWindow() != hwnd:
            raise RuntimeError("无法将 QQ 音乐窗口切换到前台，请先手动激活该窗口。")
        self._send_hotkey(self.VK_CONTROL, self.VK_F)
        time.sleep(0.15)
        self._send_unicode_text(query or "")
        self._send_key(self.VK_RETURN)
        return f"已在 QQ 音乐中搜索：{query}"

    def _focus_qq_music_window(self) -> None:
        hwnd = self._find_qq_music_window()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.ShowWindow(hwnd, self.SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        if user32.GetForegroundWindow() != hwnd:
            raise RuntimeError("无法将 QQ 音乐窗口切换到前台，请先手动激活该窗口。")

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

        matches: list[wintypes.HWND] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def collect_window(hwnd: int, _parameter: int) -> bool:
            if not is_window_visible(hwnd):
                return True
            process_id = wintypes.DWORD()
            get_window_process_id(hwnd, ctypes.byref(process_id))
            if process_id.value in process_ids:
                matches.append(hwnd)
                return False
            return True

        enum_windows(collect_window, 0)
        if not matches:
            raise RuntimeError(
                "检测到 QQ 音乐主程序正在运行，但未找到可操作的窗口；"
                "请先打开 QQ 音乐主窗口。"
            )
        return matches[0]

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
