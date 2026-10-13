"""Challenge gate for skill presses (SSK mode / Polina mode).

Two optional "challenge" modes stop a macro until the player proves it is a
human typing on the keyboard:

* ``ssk``    — a random N-character password (letters + digits) is shown in an
               always-on-top overlay and must be typed in full.
* ``polina`` — a *single* random character (letters + digits + punctuation,
               i.e. the unshifted main keys of a 60% keyboard) is shown and must
               be pressed.

The challenge is required **before every skill key** listed in ``keys`` (by
default ``q w e r d f`` — the ability keys of every Dota hero, including
Invoker's orbs/invoke/cast), so it works on any hero, not just Invoker. A brand
new challenge is generated for every press.

Key capture is global (works while the game is focused):

* Windows -> pynput with ``suppress=True`` (typed characters do NOT leak into
  the game);
* other OSes -> pynput without suppression (best effort);
* if no overlay/display is available -> falls back to typing in the console.
"""
from __future__ import annotations

import platform
import queue
import random
import string
import threading
import time

# --- character sets -----------------------------------------------------------
# Single-key (no Shift) characters, so the user never has to hold Shift. On
# Windows we decode by *virtual key code*, so the keyboard layout (RU/EN) does
# not matter — only the physical key position.
SSK_CHARSET = string.ascii_lowercase + string.digits
POLINA_PUNCT = ";=,-./`[\\]'"
POLINA_CHARSET = string.ascii_lowercase + string.digits + POLINA_PUNCT

# Windows virtual-key code -> character, US layout positions (layout
# independent, because we read the physical key).
_VK_CHAR: dict[int, str] = {}
for _i in range(26):
    _VK_CHAR[0x41 + _i] = chr(0x61 + _i)          # A..Z -> a..z
for _i in range(10):
    _VK_CHAR[0x30 + _i] = chr(0x30 + _i)          # 0..9
    _VK_CHAR[0x60 + _i] = chr(0x30 + _i)          # NumPad 0..9
_VK_CHAR.update({
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".",
    0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
})


def generate_password(length: int = 8, charset: str = SSK_CHARSET) -> str:
    return "".join(random.choice(charset) for _ in range(length))


def _key_to_char(key, charset: str) -> str | None:
    """Map a pynput key event to a lowercase char from ``charset`` (or None).

    On Windows this uses the *virtual key code*, so it is independent of the
    active keyboard layout. On other platforms the ``char`` attribute (which is
    layout dependent) is used.
    """
    if platform.system() == "Windows":
        vk = getattr(key, "vk", None)
        if vk is not None:
            ch = _VK_CHAR.get(vk)
            return ch if ch in charset else None
    ch = getattr(key, "char", None)
    if ch and len(ch) == 1 and ch.isascii():
        low = ch.lower()
        if low in charset:
            return low
    return None


class _Overlay:
    """Always-on-top Tkinter window that shows the current challenge."""

    def __init__(self) -> None:
        import tkinter as tk

        self.tk = tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        for attr in ("-alpha",):
            try:
                self.root.attributes(attr, 0.9)
            except Exception:
                pass
        bg = "#0b0b0b"
        self.orb = tk.Label(self.root, font=("Segoe UI", 18, "bold"),
                            fg="#ffffff", bg=bg)
        self.orb.pack(fill="x")
        self.target = tk.Label(self.root, font=("Consolas", 30, "bold"),
                               fg="#00ff88", bg=bg)
        self.target.pack(fill="x", pady=(2, 2))
        self.typed = tk.Label(self.root, font=("Consolas", 22),
                              fg="#ffd24a", bg=bg)
        self.typed.pack(fill="x")
        self.hint = tk.Label(self.root, font=("Segoe UI", 10),
                             fg="#888888", bg=bg)
        self.hint.pack(fill="x", pady=(0, 4))

    def show(self, label: str, target: str, typed: str, err: bool,
             hint: str) -> None:
        self.orb.config(text=label)
        self.target.config(text=target)
        self.typed.config(text=typed or "·" * len(target),
                          fg="#ff5555" if err else "#ffd24a")
        self.hint.config(text=hint)
        self.root.update_idletasks()
        w = self.root.winfo_reqwidth()
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"+{(sw - w) // 2}+60")
        self.root.deiconify()
        self.root.lift()
        self.root.update()

    def hide(self) -> None:
        self.root.withdraw()
        self.root.update()

    def destroy(self) -> None:
        try:
            self.root.destroy()
        except Exception:
            pass


class ChallengeGate:
    """Human-typing challenge required before each skill key press.

    ``mode="ssk"``    -> the full random password must be typed.
    ``mode="polina"`` -> a single random character must be pressed.

    The challenge triggers before *every* key that is in ``keys`` (default
    ``q w e r d f``), so it applies to all heroes.
    """

    def __init__(self, mode: str = "ssk", keys=("q", "w", "e", "r", "d", "f"),
                 length: int = 8, charset: str | None = None,
                 use_overlay: bool = True, timeout: float | None = None) -> None:
        self.mode = mode.lower()
        self.keys = {k.lower() for k in keys}
        self.length = length
        if charset is None:
            charset = POLINA_CHARSET if self.mode == "polina" else SSK_CHARSET
        self.charset = charset
        self.timeout = timeout
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._active = threading.Event()
        self.last_password: str | None = None
        self._listener = None
        try:
            from pynput import keyboard
            self._keyboard = keyboard
        except ImportError:
            self._keyboard = None
        self._overlay: _Overlay | None = None
        if use_overlay:
            try:
                self._overlay = _Overlay()
            except Exception:
                self._overlay = None  # no display -> console fallback

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self._keyboard is None:
            return
        suppress = platform.system() == "Windows"
        try:
            self._listener = self._keyboard.Listener(
                on_press=self._on_press, suppress=suppress)
        except Exception:
            self._listener = self._keyboard.Listener(on_press=self._on_press)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
        if self._overlay is not None:
            self._overlay.destroy()

    def applies(self, key: str) -> bool:
        return key.lower() in self.keys

    def prompt_for(self, key: str):
        """Return ``True``/``False`` if a challenge was requested, else ``None``.

        Called right before the key is sent. A challenge is requested for every
        skill key (both modes).
        """
        if key.lower() in self.keys:
            return self.challenge(f"СКИЛЛ {key.upper()}")
        return None

    # -- capture -------------------------------------------------------------
    def _on_press(self, key) -> None:
        if not self._active.is_set():
            return
        ch = _key_to_char(key, self.charset)
        if ch is not None:
            self._queue.put(ch)

    # -- challenge -----------------------------------------------------------
    def _generate(self) -> str:
        if self.mode == "polina":
            return random.choice(self.charset)
        return generate_password(self.length, self.charset)

    def _hint(self) -> str:
        if self.mode == "polina":
            return "Нажмите показанный символ"
        return "Введите пароль с клавиатуры"

    def challenge(self, label: str) -> bool:
        target = self._generate()
        self.last_password = target
        if self._keyboard is None and self._overlay is None:
            return self._console(label, target)

        while not self._queue.empty():
            self._queue.get_nowait()
        typed = ""
        self._active.set()
        self._show(label, target, typed, False)
        start = time.monotonic()
        ok = False
        try:
            while True:
                if self._overlay is not None:
                    self._overlay.root.update()
                try:
                    ch = self._queue.get(timeout=0.05)
                except queue.Empty:
                    if self.timeout and time.monotonic() - start > self.timeout:
                        break
                    continue
                typed += ch
                if target.startswith(typed):
                    self._show(label, target, typed, False)
                    if typed == target:
                        ok = True
                        break
                else:
                    typed = ""
                    self._show(label, target, typed, True)
        finally:
            self._active.clear()
            if self._overlay is not None:
                self._overlay.hide()
        return ok

    def _show(self, label, target, typed, err) -> None:
        if self._overlay is not None:
            self._overlay.show(label, target, typed, err, self._hint())

    def _console(self, label: str, target: str) -> bool:
        print(f"\n[ЧЕЛЛЕНДЖ] {label}: введите {target}")
        try:
            answer = input("> ").strip().lower()
        except EOFError:
            return False
        return answer == target
