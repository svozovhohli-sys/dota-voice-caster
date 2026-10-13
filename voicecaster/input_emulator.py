"""Cross-platform keyboard output.

Sends real key taps to the focused window (the game). Backends:
  * Windows  -> SendInput with scancodes (works with DirectInput games)
  * Linux/X11 -> xdotool
  * Linux/Wayland -> ydotool (needs ydotoold running)
  * macOS    -> pynput (optional)

The emulator exposes a tiny interface: ``tap(key)``, ``key_down(key)``,
``key_up(key)``.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from typing import Dict


class InputError(RuntimeError):
    pass


# --- key name normalisation -------------------------------------------------

_ALIASES = {
    "control": "ctrl",
    "escape": "esc",
    "return": "enter",
    "win": "super",
    "windows": "super",
    "meta": "super",
}


def _norm(key: str) -> str:
    k = key.strip().lower()
    return _ALIASES.get(k, k)


# --- Windows backend (ctypes SendInput, scancode based) ----------------------

_VK_SCAN: Dict[str, int] = {
    "esc": 0x01, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
    "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B, "-": 0x0C,
    "=": 0x0D, "backspace": 0x0E, "tab": 0x0F,
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14, "y": 0x15,
    "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19, "[": 0x1A, "]": 0x1B,
    "enter": 0x1C, "ctrl": 0x1D,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23,
    "j": 0x24, "k": 0x25, "l": 0x26, ";": 0x27, "'": 0x28, "`": 0x29,
    "shift": 0x2A, "\\": 0x2B,
    "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30, "n": 0x31,
    "m": 0x32, ",": 0x33, ".": 0x34, "/": 0x35, "alt": 0x38, "space": 0x39,
    "f1": 0x3B, "f2": 0x3C, "f3": 0x3D, "f4": 0x3E, "f5": 0x3F, "f6": 0x40,
    "f7": 0x41, "f8": 0x42, "f9": 0x43, "f10": 0x44, "f11": 0x57, "f12": 0x58,
}

_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_SCANCODE = 0x0008
_KEYEVENTF_EXTENDEDKEY = 0x0001
_EXTENDED = {"super", "up", "down", "left", "right", "insert", "delete",
             "home", "end", "pageup", "pagedown", "numlock"}

_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004


class _WindowsBackend:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
            ]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
            ]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            ]

        class _INPUTUNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT),
                        ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

        self._KEYBDINPUT = KEYBDINPUT
        self._MOUSEINPUT = MOUSEINPUT
        self._INPUT = INPUT
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)

    def _emit(self, scancode: int, keyup: bool) -> None:
        flags = _KEYEVENTF_SCANCODE
        if keyup:
            flags |= _KEYEVENTF_KEYUP
        inp = self._INPUT()
        inp.type = 1  # INPUT_KEYBOARD
        inp.u.ki = self._KEYBDINPUT(0, scancode, flags, 0, None)
        self._user32.SendInput(1, self._ctypes.byref(inp),
                               self._ctypes.sizeof(inp))

    def key_down(self, key: str) -> None:
        sc = _VK_SCAN.get(key)
        if sc is None:
            raise InputError(f"неизвестная клавиша: {key!r}")
        self._emit(sc, keyup=False)

    def key_up(self, key: str) -> None:
        sc = _VK_SCAN.get(key)
        if sc is None:
            raise InputError(f"неизвестная клавиша: {key!r}")
        self._emit(sc, keyup=True)

    def _mouse(self, flags: int, dx: int = 0, dy: int = 0) -> None:
        inp = self._INPUT()
        inp.type = 0  # INPUT_MOUSE
        inp.u.mi = self._MOUSEINPUT(dx, dy, 0, flags, 0, None)
        self._user32.SendInput(1, self._ctypes.byref(inp),
                               self._ctypes.sizeof(inp))

    def move_cursor(self, dx: int, dy: int) -> None:
        # Relative movement (no ABSOLUTE flag).
        self._mouse(_MOUSEEVENTF_MOVE, int(dx), int(dy))

    def click(self) -> None:
        self._mouse(_MOUSEEVENTF_LEFTDOWN)
        self._mouse(_MOUSEEVENTF_LEFTUP)


# --- Linux backend (xdotool / ydotool) --------------------------------------

class _LinuxBackend:
    def __init__(self) -> None:
        if shutil.which("xdotool") and _x11_available():
            self._tool = "xdotool"
        elif shutil.which("ydotool"):
            self._tool = "ydotool"
        else:
            raise InputError(
                "Нужен xdotool (X11) или ydotool (Wayland).\n"
                "  X11:     sudo apt install xdotool\n"
                "  Wayland: sudo apt install ydotool  # и запустить ydotoold"
            )

    def _run(self, args) -> None:
        subprocess.run(args, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def tap(self, key: str) -> None:
        if self._tool == "xdotool":
            self._run(["xdotool", "key", "--clearmodifiers", key])
        else:
            self._run(["ydotool", "key", _ydotool_code(key)])

    def key_down(self, key: str) -> None:
        if self._tool == "xdotool":
            self._run(["xdotool", "keydown", "--clearmodifiers", key])
        else:
            self._run(["ydotool", "key", f"{_ydotool_code(key)}:1"])

    def key_up(self, key: str) -> None:
        if self._tool == "xdotool":
            self._run(["xdotool", "keyup", "--clearmodifiers", key])
        else:
            self._run(["ydotool", "key", f"{_ydotool_code(key)}:0"])

    def move_cursor(self, dx: int, dy: int) -> None:
        if self._tool == "xdotool":
            self._run(["xdotool", "mousemove_relative",
                       str(int(dx)), str(int(dy))])
        else:
            self._run(["ydotool", "mousemove", "--relative",
                       "--x", str(int(dx)), "--y", str(int(dy))])

    def click(self) -> None:
        if self._tool == "xdotool":
            self._run(["xdotool", "click", "1"])
        else:
            self._run(["ydotool", "click", "0xC0"])  # BTN_LEFT


_YDOTOOL_CODES: Dict[str, int] = {
    "esc": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8,
    "8": 9, "9": 10, "0": 11, "minus": 12, "equal": 13, "backspace": 14,
    "tab": 15, "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21,
    "u": 22, "i": 23, "o": 24, "p": 25, "enter": 28, "ctrl": 29,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37,
    "l": 38, "shift": 42, "z": 44, "x": 45, "c": 46, "v": 47, "b": 48,
    "n": 49, "m": 50, "alt": 56, "space": 57,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64, "f7": 65,
    "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
}


def _ydotool_code(key: str) -> str:
    code = _YDOTOOL_CODES.get(key)
    if code is None:
        raise InputError(f"неизвестная клавиша: {key!r}")
    return f"{code}:1"


def _x11_available() -> bool:
    import os
    return bool(os.environ.get("DISPLAY")) and \
        os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland"


# --- Public interface --------------------------------------------------------

class KeyboardEmulator:
    """Selects the right backend for the current OS and sends key taps."""

    def __init__(self) -> None:
        system = platform.system()
        if system == "Windows":
            self._impl = _WindowsBackend()
            self.backend_name = "windows/SendInput"
        elif system == "Linux":
            self._impl = _LinuxBackend()
            self.backend_name = f"linux/{self._impl._tool}"
        elif system == "Darwin":
            try:
                self._impl = _MacBackend()
            except ImportError as exc:  # pragma: no cover
                raise InputError("Для macOS установите pynput") from exc
            self.backend_name = "macos/pynput"
        else:
            raise InputError(f"Неподдерживаемая ОС: {system}")

    def tap(self, key: str) -> None:
        tap = getattr(self._impl, "tap", None)
        if tap is not None:
            tap(key)
        else:
            self._impl.key_down(key)
            self._impl.key_up(key)

    def key_down(self, key: str) -> None:
        self._impl.key_down(key)

    def key_up(self, key: str) -> None:
        self._impl.key_up(key)

    def move_cursor(self, dx: int, dy: int) -> None:
        self._impl.move_cursor(int(dx), int(dy))

    def click(self) -> None:
        self._impl.click()


class _MacBackend:  # pragma: no cover - only reached on macOS
    def __init__(self) -> None:
        from pynput.keyboard import Controller
        from pynput.mouse import Controller as MouseController
        self._kb = Controller()
        self._mouse = MouseController()

    def tap(self, key: str) -> None:
        self._kb.press(key)
        self._kb.release(key)

    def key_down(self, key: str) -> None:
        self._kb.press(key)

    def key_up(self, key: str) -> None:
        self._kb.release(key)

    def move_cursor(self, dx: int, dy: int) -> None:
        self._mouse.move(int(dx), int(dy))

    def click(self) -> None:
        from pynput.mouse import Button
        self._mouse.click(Button.left)
