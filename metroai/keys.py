"""Genel klavye kısayolu (Push-to-Talk / Uyandırma tuşu) ve ESC ile dinleme iptali."""
from __future__ import annotations

import os
import select
import struct
import sys
import termios
import threading
import time
from pathlib import Path
from typing import Callable

from . import log
from .settings import STATE_DIR

log_ = log.get()

CANCEL_FILE = STATE_DIR / "cancel_listen"
TRIGGER_FILE = STATE_DIR / "trigger_wake"

# Linux input-event-codes.h (x86_64: timeval(16B) + type(2B) + code(2B) + value(4B) = 24B)
_EVENT_STRUCT = struct.Struct("llHHI")
_EV_KEY = 0x01

CODE_TO_KEY: dict[int, str] = {
    1: "ESC",
    14: "BACKSPACE",
    15: "TAB",
    28: "ENTER",
    29: "CTRL",
    41: "GRAVE",
    42: "SHIFT",
    54: "SHIFT",
    56: "ALT",
    57: "SPACE",
    58: "CAPSLOCK",
    59: "F1",
    60: "F2",
    61: "F3",
    62: "F4",
    63: "F5",
    64: "F6",
    65: "F7",
    66: "F8",
    67: "F9",
    68: "F10",
    70: "SCROLLLOCK",
    87: "F11",
    88: "F12",
    97: "RIGHTCTRL",
    99: "SYSRQ",
    100: "RIGHTALT",
    102: "HOME",
    104: "PAGEUP",
    107: "END",
    109: "PAGEDOWN",
    110: "INSERT",
    111: "DELETE",
    119: "PAUSE",
    125: "META",
    126: "META",
    # Harfler (kombine kısayollar için: ALT+M, META+M vb.)
    16: "Q", 17: "W", 18: "E", 19: "R", 20: "T", 21: "Y", 22: "U", 23: "I", 24: "O", 25: "P",
    30: "A", 31: "S", 32: "D", 33: "F", 34: "G", 35: "H", 36: "J", 37: "K", 38: "L",
    44: "Z", 45: "X", 46: "C", 47: "V", 48: "B", 49: "N", 50: "M",
}

_MODIFIERS = {"CTRL", "ALT", "SHIFT", "META"}


def normalize_hotkey(spec: str) -> str:
    """Kullanıcının girdiği kısayol metnini standart biçime çevirir (örn. 'f8' -> 'F8', 'super+m' -> 'META+M')."""
    if not spec:
        return ""
    raw = spec.strip().upper().replace(" ", "")
    if raw in ("NONE", "YOK", "BOŞ", "BOS", "0", "-", "IPTAL", "ATLA"):
        return ""
    parts = [p for p in raw.replace("-", "+").split("+") if p]
    norm: list[str] = []
    for p in parts:
        if p in ("SUPER", "WIN", "WINDOWS", "LOGO", "CMD"):
            p = "META"
        elif p in ("CONTROL", "LCTRL"):
            p = "CTRL"
        elif p in ("RCTRL",):
            p = "RIGHTCTRL"
        elif p in ("RALT", "ALTGR"):
            p = "RIGHTALT"
        elif p in ("ESCAPE",):
            p = "ESC"
        norm.append(p)
    mods = sorted([p for p in norm if p in _MODIFIERS])
    keys = [p for p in norm if p not in _MODIFIERS]
    return "+".join(mods + keys)


def _keyboard_paths() -> list[Path]:
    out: set[Path] = set()
    by_path = Path("/dev/input/by-path")
    if by_path.exists():
        for p in by_path.glob("*-event-kbd"):
            try:
                out.add(p.resolve())
            except Exception:
                pass
    return sorted(out)


def _stdin_esc_pressed() -> bool:
    """Terminal odaklıyken ESC tuşuna basılıp basılmadığını kontrol eder."""
    if not sys.stdin or not sys.stdin.isatty():
        return False
    try:
        r, _, _ = select.select([sys.stdin], [], [], 0.0)
        if r:
            ch = os.read(sys.stdin.fileno(), 32)
            if ch == b"\x1b":
                return True
    except Exception:
        pass
    return False


class KeyWatcher:
    """Arka planda klavye olaylarını dinler:
    - ESC basıldığında aktif ses dinlemesini anında iptal eder.
    - Atanan konuşma tuşuna (hotkey) basıldığında Metro'yu uyandırır.
    """

    def __init__(self, hotkey: str = "", on_hotkey: Callable[[], None] | None = None):
        self.hotkey = normalize_hotkey(hotkey)
        self.on_hotkey = on_hotkey
        self.esc_event = threading.Event()
        self.wake_event = threading.Event()
        self._stop = False
        self._last_hotkey_ts = 0.0
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def set_hotkey(self, hotkey: str) -> None:
        self.hotkey = normalize_hotkey(hotkey)

    def clear_esc(self) -> None:
        self.esc_event.clear()
        try:
            CANCEL_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def is_esc_pressed(self) -> bool:
        if self.esc_event.is_set():
            return True
        if CANCEL_FILE.exists():
            try:
                CANCEL_FILE.unlink(missing_ok=True)
            except Exception:
                pass
            return True
        if _stdin_esc_pressed():
            return True
        return False

    def consume_wake(self) -> bool:
        if self.wake_event.is_set():
            self.wake_event.clear()
            return True
        if TRIGGER_FILE.exists():
            try:
                TRIGGER_FILE.unlink(missing_ok=True)
            except Exception:
                pass
            return True
        return False

    def _loop(self) -> None:
        fds: list[int] = []
        for p in _keyboard_paths():
            try:
                fd = os.open(str(p), os.O_RDONLY | os.O_NONBLOCK)
                fds.append(fd)
            except Exception:
                continue

        if not fds:
            log_.debug("Doğrudan klavye aygıtı açılamadı (yalnızca sinyal dosyası / terminal ESC aktif)")
            return

        active_mods: set[str] = set()
        size = _EVENT_STRUCT.size

        try:
            while not self._stop:
                r, _, _ = select.select(fds, [], [], 0.25)
                for fd in r:
                    try:
                        data = os.read(fd, size * 16)
                    except OSError:
                        continue
                    for offset in range(0, len(data) - size + 1, size):
                        _, _, ev_type, code, value = _EVENT_STRUCT.unpack_from(data, offset)
                        if ev_type != _EV_KEY:
                            continue
                        key_name = CODE_TO_KEY.get(code)
                        if not key_name:
                            continue

                        if key_name in _MODIFIERS:
                            if value in (1, 2):
                                active_mods.add(key_name)
                            elif value == 0:
                                active_mods.discard(key_name)
                            continue

                        # Yalnızca tuşa ilk basıldığında (value == 1) tetikle
                        if value != 1:
                            continue

                        if key_name == "ESC":
                            self.esc_event.set()
                            try:
                                CANCEL_FILE.parent.mkdir(parents=True, exist_ok=True)
                                CANCEL_FILE.write_text("1", encoding="utf-8")
                            except Exception:
                                pass
                            continue

                        if self.hotkey:
                            combo = "+".join(sorted(active_mods) + [key_name])
                            if combo == self.hotkey or (not active_mods and key_name == self.hotkey):
                                now = time.monotonic()
                                if now - self._last_hotkey_ts > 0.8:
                                    self._last_hotkey_ts = now
                                    self.wake_event.set()
                                    if self.on_hotkey:
                                        try:
                                            self.on_hotkey()
                                        except Exception:
                                            pass
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except Exception:
                    pass


_GLOBAL_WATCHER: KeyWatcher | None = None


def get_watcher(hotkey: str = "") -> KeyWatcher:
    global _GLOBAL_WATCHER
    if _GLOBAL_WATCHER is None:
        _GLOBAL_WATCHER = KeyWatcher(hotkey=hotkey)
    elif hotkey:
        _GLOBAL_WATCHER.set_hotkey(hotkey)
    return _GLOBAL_WATCHER


def capture_hotkey_interactive(prompt_text: str = "") -> str:
    """Kullanıcıdan ya doğrudan klavyeden bir tuşa basmasını ya da yazmasını ister.
    Enter veya ESC basılırsa boş (atlandı) döner.
    """
    fds: list[int] = []
    for p in _keyboard_paths():
        try:
            fd = os.open(str(p), os.O_RDONLY | os.O_NONBLOCK)
            fds.append(fd)
        except Exception:
            continue

    if not fds or not sys.stdin.isatty():
        try:
            val = input(prompt_text or "Konuşma tuşu (örn. F8, F9, ALT+M — boş bırak = atla): ").strip()
            return normalize_hotkey(val)
        except (EOFError, KeyboardInterrupt):
            return ""

    print(prompt_text or "  👉 Konuşmak için atamak istediğin tuşa ŞİMDİ BAS (atlamak için Enter veya ESC): ", end="", flush=True)

    # Önceki tuş bırakma (key-up) olaylarını temizle
    time.sleep(0.25)
    for fd in fds:
        try:
            while os.read(fd, 1024):
                pass
        except OSError:
            pass

    old_term = None
    try:
        old_term = termios.tcgetattr(sys.stdin.fileno())
        new_term = termios.tcgetattr(sys.stdin.fileno())
        new_term[3] = new_term[3] & ~(termios.ECHO | termios.ICANON)
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, new_term)
    except Exception:
        old_term = None

    active_mods: set[str] = set()
    size = _EVENT_STRUCT.size
    chosen = ""
    deadline = time.monotonic() + 12.0

    try:
        while time.monotonic() < deadline:
            r, _, _ = select.select(fds, [], [], 0.2)
            for fd in r:
                try:
                    data = os.read(fd, size * 16)
                except OSError:
                    continue
                for offset in range(0, len(data) - size + 1, size):
                    _, _, ev_type, code, value = _EVENT_STRUCT.unpack_from(data, offset)
                    if ev_type != _EV_KEY:
                        continue
                    key_name = CODE_TO_KEY.get(code)
                    if not key_name:
                        continue
                    if key_name in _MODIFIERS:
                        if value in (1, 2):
                            active_mods.add(key_name)
                        elif value == 0:
                            active_mods.discard(key_name)
                        continue
                    if value == 1:
                        if key_name in ("ENTER", "ESC"):
                            print("Atlandı (yalnızca sesle uyanma aktif)")
                            return ""
                        chosen = "+".join(sorted(active_mods) + [key_name])
                        print(f"{chosen}")
                        return chosen
    except KeyboardInterrupt:
        print()
        return ""
    finally:
        if old_term is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_term)
            except Exception:
                pass
        # Stdin tamponunda biriken karakterleri temizle
        try:
            while select.select([sys.stdin], [], [], 0.0)[0]:
                os.read(sys.stdin.fileno(), 64)
        except Exception:
            pass
        for fd in fds:
            try:
                os.close(fd)
            except Exception:
                pass

    print("Zaman aşımı (atlandı)")
    return chosen
