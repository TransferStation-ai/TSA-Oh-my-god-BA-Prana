import ctypes
from pathlib import Path
import sys
import time
from ctypes import wintypes


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
VK_ESCAPE = 0x1B
SW_RESTORE = 9
DWMWA_EXTENDED_FRAME_BOUNDS = 9

KEY_CODES = {
    "enter": 0x0D,
    "esc": 0x1B,
    "tab": 0x09,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "ctrl": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
    "capslock": 0x14,
    "numlock": 0x90,
    "printscreen": 0x2C,
    "minus": 0xBD,
    "equal": 0xBB,
    "comma": 0xBC,
    "period": 0xBE,
    "slash": 0xBF,
    "backslash": 0xDC,
    "semicolon": 0xBA,
    "apostrophe": 0xDE,
    "bracketleft": 0xDB,
    "bracketright": 0xDD,
    "grave": 0xC0,
}
KEY_CODES.update({chr(code): code - 32 for code in range(ord("a"), ord("z") + 1)})
KEY_CODES.update({str(digit): 0x30 + digit for digit in range(10)})
KEY_CODES.update({f"f{index}": 0x6F + index for index in range(1, 13)})

MODIFIER_KEYS = {"ctrl", "alt", "shift", "win"}

_BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


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


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("桌面键鼠控制仅支持 Windows。")


def ensure_dpi_awareness() -> None:
    _require_windows()
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


def virtual_screen_rect() -> tuple[int, int, int, int]:
    _require_windows()
    ensure_dpi_awareness()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_metric = user32.GetSystemMetrics
    get_metric.argtypes = [ctypes.c_int]
    get_metric.restype = ctypes.c_int
    left = get_metric(SM_XVIRTUALSCREEN)
    top = get_metric(SM_YVIRTUALSCREEN)
    width = get_metric(SM_CXVIRTUALSCREEN)
    height = get_metric(SM_CYVIRTUALSCREEN)
    if width <= 0 or height <= 0:
        raise RuntimeError("无法读取虚拟屏幕尺寸。")
    return left, top, width, height


def get_cursor_position() -> tuple[int, int]:
    _require_windows()
    point = wintypes.POINT()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise RuntimeError(
            f"无法读取鼠标位置（Windows 错误 {ctypes.get_last_error()}）。"
        )
    return point.x, point.y


def set_cursor_position(x: int, y: int) -> None:
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    if not user32.SetCursorPos(x, y):
        raise RuntimeError(
            f"无法移动鼠标（Windows 错误 {ctypes.get_last_error()}）。"
        )


def key_input(key: int, *, key_up: bool = False, unicode: bool = False) -> _INPUT:
    flags = (KEYEVENTF_KEYUP if key_up else 0) | (
        KEYEVENTF_UNICODE if unicode else 0
    )
    return _INPUT(
        type=INPUT_KEYBOARD,
        ki=_KEYBDINPUT(
            wVk=0 if unicode else key,
            wScan=key if unicode else 0,
            dwFlags=flags,
        ),
    )


def send_inputs(inputs: list[_INPUT]) -> None:
    _require_windows()
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
        raise RuntimeError(
            "键鼠输入失败"
            f"（Windows 错误 {ctypes.get_last_error()}，"
            f"已发送 {sent}/{len(inputs)} 个事件）。"
        )


def send_key(key: int) -> None:
    send_inputs([key_input(key), key_input(key, key_up=True)])


def send_hotkey(modifiers: list[int], key: int) -> None:
    send_inputs(
        [key_input(modifier) for modifier in modifiers]
        + [key_input(key), key_input(key, key_up=True)]
        + [key_input(modifier, key_up=True) for modifier in reversed(modifiers)]
    )


def send_unicode_text(text: str) -> None:
    encoded_text = text.encode("utf-16-le")
    units = [
        int.from_bytes(encoded_text[index : index + 2], "little")
        for index in range(0, len(encoded_text), 2)
    ]
    inputs = [
        event
        for unit in units
        for event in (
            key_input(unit, unicode=True),
            key_input(unit, key_up=True, unicode=True),
        )
    ]
    if inputs:
        send_inputs(inputs)


def mouse_button(button: str, *, up: bool = False) -> None:
    if button not in _BUTTON_FLAGS:
        raise ValueError(f"不支持的鼠标按键：{button}")
    down_flag, up_flag = _BUTTON_FLAGS[button]
    send_inputs(
        [
            _INPUT(
                type=INPUT_MOUSE,
                mi=_MOUSEINPUT(dwFlags=up_flag if up else down_flag),
            )
        ]
    )


def mouse_wheel(amount: int) -> None:
    if amount == 0:
        raise ValueError("滚轮数量不能为 0。")
    send_inputs(
        [
            _INPUT(
                type=INPUT_MOUSE,
                mi=_MOUSEINPUT(
                    mouseData=(amount * WHEEL_DELTA) & 0xFFFFFFFF,
                    dwFlags=MOUSEEVENTF_WHEEL,
                ),
            )
        ]
    )


def is_key_down(key: int) -> bool:
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    return bool(user32.GetAsyncKeyState(key) & 0x8000)


def foreground_window() -> int:
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    return int(user32.GetForegroundWindow())


def window_title(hwnd: int) -> str:
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_window_text = user32.GetWindowTextW
    get_window_text.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    get_window_text.restype = ctypes.c_int
    buffer = ctypes.create_unicode_buffer(512)
    get_window_text(wintypes.HWND(hwnd), buffer, len(buffer))
    return buffer.value.strip()


def window_class_name(hwnd: int) -> str:
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_class_name = user32.GetClassNameW
    get_class_name.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    get_class_name.restype = ctypes.c_int
    buffer = ctypes.create_unicode_buffer(256)
    get_class_name(wintypes.HWND(hwnd), buffer, len(buffer))
    return buffer.value


def window_process_id(hwnd: int) -> int:
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    process_id = wintypes.DWORD()
    get_window_thread_process_id = user32.GetWindowThreadProcessId
    get_window_thread_process_id.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_window_thread_process_id.restype = wintypes.DWORD
    get_window_thread_process_id(
        wintypes.HWND(hwnd), ctypes.byref(process_id)
    )
    return int(process_id.value)


def window_process_name(hwnd: int) -> str:
    process_id = window_process_id(hwnd)
    if not process_id:
        return ""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    handle = open_process(0x1000, False, process_id)
    if not handle:
        return ""
    try:
        query = kernel32.QueryFullProcessImageNameW
        query.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        query.restype = wintypes.BOOL
        size = wintypes.DWORD(260)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not query(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return Path(buffer.value).name
    finally:
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        close_handle(handle)


def is_window_visible(hwnd: int) -> bool:
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    return bool(user32.IsWindowVisible(wintypes.HWND(hwnd)))


def window_bounds(hwnd: int) -> tuple[int, int, int, int]:
    _require_windows()
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
        DWMWA_EXTENDED_FRAME_BOUNDS,
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
                "无法读取窗口位置"
                f"（Windows 错误 {ctypes.get_last_error()}）。"
            )
    return (
        int(rect.left),
        int(rect.top),
        max(0, int(rect.right - rect.left)),
        max(0, int(rect.bottom - rect.top)),
    )


def window_client_bounds(hwnd: int) -> tuple[int, int, int, int]:
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    rect = wintypes.RECT()
    user32.GetClientRect.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.RECT),
    ]
    user32.GetClientRect.restype = wintypes.BOOL
    if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise RuntimeError("无法读取窗口客户区。")
    point = wintypes.POINT(0, 0)
    user32.ClientToScreen.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.POINT),
    ]
    user32.ClientToScreen.restype = wintypes.BOOL
    if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(point)):
        raise RuntimeError("无法读取窗口客户区位置。")
    return (
        int(point.x),
        int(point.y),
        max(0, int(rect.right - rect.left)),
        max(0, int(rect.bottom - rect.top)),
    )


def is_window_minimized(hwnd: int) -> bool:
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    return bool(user32.IsIconic(wintypes.HWND(hwnd)))


def activate_window(hwnd: int) -> None:
    _require_windows()
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

    left, top, _width, _height = window_bounds(hwnd)
    if user32.IsIconic(wintypes.HWND(hwnd)) or left <= -30000 or top <= -30000:
        user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
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
        raise RuntimeError("无法激活目标窗口，请老师手动点击该窗口后重试。")
