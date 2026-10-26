"""Genel klavye kısayolu (Push-to-Talk / Uyandırma tuşu) ve ESC ile dinleme iptali."""
from __future__ import annotations

import os
import re
import select
import struct
import sys
import termios
import threading
import time
from pathlib import Path
from typing import Callable

from . import log
from .settings import CONFIG_PATH, STATE_DIR, load

log_ = log.get()

CANCEL_FILE = STATE_DIR / "cancel_listen"
TRIGGER_FILE = STATE_DIR / "trigger_wake"
CAPTURING_FILE = STATE_DIR / "capturing_hotkey"

# Linux input-event-codes.h (x86_64: timeval(16B) + type(2B) + code(2B) + value(4B) = 24B)
_EVENT_STRUCT = struct.Struct("llHHI")
_EV_KEY = 0x01

CODE_TO_KEY: dict[int, str] = {
    1: "ESC",
    2: "1", 3: "2", 4: "3", 5: "4", 6: "5", 7: "6", 8: "7", 9: "8", 10: "9", 11: "0",
    12: "MINUS", 13: "EQUAL",
    14: "BACKSPACE",
    15: "TAB",
    16: "Q", 17: "W", 18: "E", 19: "R", 20: "T", 21: "Y", 22: "U", 23: "I", 24: "O", 25: "P",
    26: "LEFTBRACE", 27: "RIGHTBRACE",
    28: "ENTER",
    29: "CTRL",
    30: "A", 31: "S", 32: "D", 33: "F", 34: "G", 35: "H", 36: "J", 37: "K", 38: "L",
    39: "SEMICOLON", 40: "APOSTROPHE",
    41: "GRAVE",
    42: "SHIFT",
    43: "BACKSLASH",
    44: "Z", 45: "X", 46: "C", 47: "V", 48: "B", 49: "N", 50: "M",
    51: "COMMA", 52: "DOT", 53: "SLASH",
    54: "SHIFT",
    55: "KPASTERISK",
    56: "ALT",
    57: "SPACE",
    58: "CAPSLOCK",
    59: "F1", 60: "F2", 61: "F3", 62: "F4", 63: "F5", 64: "F6",
    65: "F7", 66: "F8", 67: "F9", 68: "F10",
    69: "NUMLOCK", 70: "SCROLLLOCK",
    71: "KP7", 72: "KP8", 73: "KP9", 74: "KPMINUS",
    75: "KP4", 76: "KP5", 77: "KP6", 78: "KPPLUS",
    79: "KP1", 80: "KP2", 81: "KP3",
    82: "KP0", 83: "KPDOT",
    86: "102ND",
    87: "F11", 88: "F12",
    96: "KPENTER",
    97: "RIGHTCTRL",
    98: "KPSLASH",
    99: "SYSRQ",
    100: "RIGHTALT",
    102: "HOME", 103: "UP", 104: "PAGEUP",
    105: "LEFT", 106: "RIGHT",
    107: "END", 108: "DOWN", 109: "PAGEDOWN",
    110: "INSERT", 111: "DELETE",
    113: "MUTE", 114: "VOLUMEDOWN", 115: "VOLUMEUP",
    119: "PAUSE",
    125: "META", 126: "META",
    127: "MENU",
    183: "F13", 184: "F14", 185: "F15", 186: "F16", 187: "F17", 188: "F18",
    189: "F19", 190: "F20", 191: "F21", 192: "F22", 193: "F23", 194: "F24",
    # Fare yan tuşları (Push-to-talk için çok popüler)
    274: "MOUSE3",
    275: "MOUSE4",
    276: "MOUSE5",
}

_MODIFIERS = {"CTRL", "ALT", "SHIFT", "META"}

# Terminal raw escape dizileri (yedek yakalayıcı)
_TERM_SEQ_TO_KEY: dict[bytes, str] = {
    b"\x1bOP": "F1", b"\x1bOQ": "F2", b"\x1bOR": "F3", b"\x1bOS": "F4",
    b"\x1b[15~": "F5", b"\x1b[17~": "F6", b"\x1b[18~": "F7", b"\x1b[19~": "F8",
    b"\x1b[20~": "F9", b"\x1b[21~": "F10", b"\x1b[23~": "F11", b"\x1b[24~": "F12",
    b"\x1b[2~": "INSERT", b"\x1b[3~": "DELETE", b"\x1b[5~": "PAGEUP", b"\x1b[6~": "PAGEDOWN",
    b"\x1b[H": "HOME", b"\x1b[F": "END",
}


def normalize_hotkey(spec: str) -> str:
    """Kullanıcının girdiği kısayol metnini standart biçime çevirir (örn. 'f8' -> 'F8', 'super+m' -> 'META+M')."""
    if not spec:
        return ""
    raw = spec.strip().upper().replace(" ", "")
    if raw in ("NONE", "YOK", "BOŞ", "BOS", "0", "-", "IPTAL", "ATLA", "OFF", "SIL", "KALDIR"):
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
    mods = sorted({p for p in norm if p in _MODIFIERS})
    keys = [p for p in norm if p not in _MODIFIERS]
    return "+".join(mods + keys)


def format_hotkey(spec: str) -> str:
    """Kısayolu kullanıcı dostu biçimde gösterir."""
    norm = normalize_hotkey(spec)
    return norm if norm else "Yok"


def trigger_cancel() -> None:
    """Aktif dinlemeyi iptal etmek için sinyal yazar."""
    try:
        CANCEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        CANCEL_FILE.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass
    if _GLOBAL_WATCHER is not None:
        _GLOBAL_WATCHER.esc_event.set()


def trigger_wake() -> None:
    """Metro'yu hemen uyandırmak için sinyal yazar."""
    try:
        TRIGGER_FILE.parent.mkdir(parents=True, exist_ok=True)
        TRIGGER_FILE.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass
    if _GLOBAL_WATCHER is not None:
        _GLOBAL_WATCHER.wake_event.set()


def _input_device_paths() -> list[Path]:
    """Sistemdeki tüm klavye (ve yan tuşlu fare) event aygıtlarını döndürür."""
    out: set[Path] = set()

    # 1) /dev/input/by-path/*-event-kbd ve *-event-mouse
    by_path = Path("/dev/input/by-path")
    if by_path.exists():
        for pat in ("*-event-kbd", "*-event-mouse"):
            for p in by_path.glob(pat):
                try:
                    out.add(p.resolve())
                except Exception:
                    pass

    # 2) /proc/bus/input/devices içinden kbd handler'ı olan tüm eventX aygıtları
    proc_devs = Path("/proc/bus/input/devices")
    if proc_devs.exists():
        try:
            text = proc_devs.read_text(encoding="utf-8", errors="ignore")
            for block in text.split("\n\n"):
                if "Handlers=" in block and ("kbd" in block or "sysrq" in block):
                    for m in re.finditer(r"\bevent(\d+)\b", block):
                        ev_path = Path(f"/dev/input/event{m.group(1)}")
                        if ev_path.exists():
                            out.add(ev_path)
        except Exception:
            pass

    return sorted(out)


def has_global_key_access() -> bool:
    for p in _input_device_paths():
        if os.access(p, os.R_OK):
            return True
    return False


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
    - config.toml değiştiğinde yeni kısayol tuşunu otomatik olarak yükler.
    """

    def __init__(self, hotkey: str = "", on_hotkey: Callable[[], None] | None = None):
        self.hotkey = normalize_hotkey(hotkey)
        self.on_hotkey = on_hotkey
        self.esc_event = threading.Event()
        self.wake_event = threading.Event()
        self._stop = False
        self._last_hotkey_ts = 0.0
        self._config_mtime = 0.0
        self._last_config_check = 0.0
        self._sync_config_hotkey(force=True)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _sync_config_hotkey(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_config_check < 0.5:
            return
        self._last_config_check = now
        try:
            if CONFIG_PATH.exists():
                mtime = CONFIG_PATH.stat().st_mtime
                if force or mtime != self._config_mtime:
                    self._config_mtime = mtime
                    cfg_hotkey = normalize_hotkey(load().hotkey)
                    if cfg_hotkey != self.hotkey:
                        self.hotkey = cfg_hotkey
                        log_.info("Kısayol tuşu güncellendi: %s", self.hotkey or "(yok)")
        except Exception:
            pass

    def set_hotkey(self, hotkey: str) -> None:
        self.hotkey = normalize_hotkey(hotkey)
        try:
            if CONFIG_PATH.exists():
                self._config_mtime = CONFIG_PATH.stat().st_mtime
        except Exception:
            pass

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
        self._sync_config_hotkey()
        if self.wake_event.is_set():
            self.wake_event.clear()
            try:
                TRIGGER_FILE.unlink(missing_ok=True)
            except Exception:
                pass
            return True
        if TRIGGER_FILE.exists():
            try:
                TRIGGER_FILE.unlink(missing_ok=True)
            except Exception:
                pass
            return True
        return False

    def _open_fds(self) -> list[int]:
        fds: list[int] = []
        for p in _input_device_paths():
            try:
                fd = os.open(str(p), os.O_RDONLY | os.O_NONBLOCK)
                fds.append(fd)
            except Exception:
                continue
        return fds

    def _loop(self) -> None:
        fds = self._open_fds()
        if not fds:
            log_.warning("Klavye aygıtları (/dev/input/event*) açılamadı — kısayol tuşu için input grubu gerekli.")
            return

        active_mods: set[str] = set()
        size = _EVENT_STRUCT.size
        last_reopen = time.monotonic()

        try:
            while not self._stop:
                self._sync_config_hotkey()

                # Yeni takılan USB klavye/fare varsa 15 saniyede bir yenile
                if time.monotonic() - last_reopen > 15.0:
                    last_reopen = time.monotonic()
                    new_paths = _input_device_paths()
                    if len(new_paths) != len(fds):
                        for fd in fds:
                            try:
                                os.close(fd)
                            except Exception:
                                pass
                        fds = self._open_fds()
                        if not fds:
                            time.sleep(1.0)
                            continue

                r, _, _ = select.select(fds, [], [], 0.20)
                for fd in r:
                    try:
                        data = os.read(fd, size * 32)
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
                            # Eğer kullanıcı tek başına META/CTRL/ALT gibi bir tuşu hotkey yaptıysa
                            if value == 1 and self.hotkey == key_name:
                                self._fire_hotkey()
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
                            if combo == self.hotkey or key_name == self.hotkey:
                                self._fire_hotkey()
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except Exception:
                    pass

    def _fire_hotkey(self) -> None:
        if CAPTURING_FILE.exists():
            return
        now = time.monotonic()
        if now - self._last_hotkey_ts <= 0.75:
            return
        self._last_hotkey_ts = now
        log_.info("Kısayol tuşu algılandı (%s) -> Metro uyandırılıyor", self.hotkey)
        self.wake_event.set()
        try:
            TRIGGER_FILE.parent.mkdir(parents=True, exist_ok=True)
            TRIGGER_FILE.write_text(str(time.time()), encoding="utf-8")
        except Exception:
            pass
        if self.on_hotkey:
            try:
                self.on_hotkey()
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


def capture_hotkey_interactive(timeout: float = 12.0, prompt_text: str = "") -> str:
    """Kullanıcının bastığı tuşu (veya kombinasyonu) yakalar.
    Enter veya ESC basılırsa boş (atlandı) döner.
    """
    if prompt_text:
        sys.stdout.write(prompt_text)
        sys.stdout.flush()

    fds: list[int] = []
    for p in _input_device_paths():
        try:
            fd = os.open(str(p), os.O_RDONLY | os.O_NONBLOCK)
            fds.append(fd)
        except Exception:
            continue

    if not fds and not sys.stdin.isatty():
        try:
            val = input().strip()
            return normalize_hotkey(val)
        except (EOFError, KeyboardInterrupt):
            return ""

    # Komutu çalıştırmak için basılan önceki Enter tuşunun bırakılmasını (key-up) bekle ve temizle
    time.sleep(0.30)
    for fd in fds:
        try:
            while os.read(fd, 4096):
                pass
        except OSError:
            pass

    old_term = None
    stdin_fd: int | None = None
    if sys.stdin and sys.stdin.isatty():
        try:
            stdin_fd = sys.stdin.fileno()
            old_term = termios.tcgetattr(stdin_fd)
            new_term = termios.tcgetattr(stdin_fd)
            new_term[3] = new_term[3] & ~(termios.ECHO | termios.ICANON)
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, new_term)
            while select.select([stdin_fd], [], [], 0.0)[0]:
                os.read(stdin_fd, 64)
        except Exception:
            old_term = None
            stdin_fd = None

    active_mods: set[str] = set()
    size = _EVENT_STRUCT.size
    chosen = ""
    deadline = time.monotonic() + timeout

    try:
        CAPTURING_FILE.parent.mkdir(parents=True, exist_ok=True)
        CAPTURING_FILE.write_text("1", encoding="utf-8")
    except Exception:
        pass

    try:
        while time.monotonic() < deadline:
            watch_list = list(fds)
            if stdin_fd is not None and not fds:
                watch_list.append(stdin_fd)

            r, _, _ = select.select(watch_list, [], [], 0.15)
            for fd in r:
                if fd == stdin_fd:
                    try:
                        raw = os.read(stdin_fd, 32)
                    except OSError:
                        continue
                    if raw in (b"\r", b"\n", b"\x1b"):
                        return ""
                    if raw in _TERM_SEQ_TO_KEY:
                        return _TERM_SEQ_TO_KEY[raw]
                    if len(raw) == 1 and 32 <= raw[0] <= 126:
                        ch = chr(raw[0]).upper()
                        return "SPACE" if ch == " " else ch
                    continue

                try:
                    data = os.read(fd, size * 32)
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
                        if key_name in ("ENTER", "KPENTER", "ESC"):
                            return ""
                        chosen = "+".join(sorted(active_mods) + [key_name])
                        return chosen
    except KeyboardInterrupt:
        return ""
    finally:
        try:
            CAPTURING_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        if old_term is not None and stdin_fd is not None:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_term)
            except Exception:
                pass
        if stdin_fd is not None:
            try:
                while select.select([stdin_fd], [], [], 0.0)[0]:
                    os.read(stdin_fd, 64)
            except Exception:
                pass
        for fd in fds:
            try:
                os.close(fd)
            except Exception:
                pass

    return chosen
