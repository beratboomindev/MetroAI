"""Metro Assistant — Etkileşimli Sesli ve Yazılı Terminal Arayüzü."""
from __future__ import annotations

import json
import os
import re
import readline
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import __version__, audio, keys, log, memory, tools, tts
from .agent import Agent
from .llm import LLMChain
from .settings import ROOT, STATE_DIR, Provider, Settings, ensure_dirs, load, save, vosk_path

log_ = log.get()

EVENTS_FILE = STATE_DIR / "events.jsonl"
TUI_PID_FILE = STATE_DIR / "tui.pid"
STATUS_FILE = STATE_DIR / "status"
HISTORY_FILE = STATE_DIR / "chat_history"
SESSIONS_FILE = STATE_DIR / "chat_sessions.json"
ONBOARDED_FILE = STATE_DIR / "api_onboarded"
CONFIRM_FILE = STATE_DIR / "confirm_reply"

PROVIDER_META: dict[str, tuple[str, str]] = {
    "opencode": ("OpenCode Ücretsiz Modeller (Zen / CLI)", "https://opencode.ai/zen"),
    "groq": ("Groq (En hızlı LLM + Whisper STT)", "https://console.groq.com/keys"),
    "gemini": ("Google Gemini 2.5 (Geniş ücretsiz kota + ses)", "https://aistudio.google.com/apikey"),
    "openrouter": ("OpenRouter (Onlarca :free model)", "https://openrouter.ai/keys"),
    "cerebras": ("Cerebras (Ultra hızlı Llama)", "https://cloud.cerebras.ai"),
    "mistral": ("Mistral AI (Ücretsiz katman)", "https://console.mistral.ai/api-keys"),
}

# ANSI renk kodları (Metro Assistant profesyonel paleti)
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
CYAN = "\033[38;5;75m"
ORANGE = "\033[38;5;39m"
GREEN = "\033[38;5;114m"
YELLOW = "\033[38;5;221m"
RED = "\033[38;5;203m"
GRAY = "\033[38;5;244m"
WHITE = "\033[38;5;255m"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _vis_len(s: str) -> int:
    """ANSI renk kodları hariç görünür karakter uzunluğu."""
    return len(_ANSI_RE.sub("", s))


def _rl_prompt(s: str) -> str:
    """GNU readline'ın görünmez ANSI renk kodlarını genişlik olarak saymaması için
    tüm \\033[...m dizilerini \\001 ve \\002 içine alır (uzun yazı ve silme kaymasını %100 önler).
    """
    return _ANSI_RE.sub(lambda m: f"\001{m.group(0)}\002", s)


def _truncate_ansi(s: str, max_vis: int) -> str:
    """ANSI renk kodlarını bozmadan görünür metni max_vis karakterde keser."""
    if _vis_len(s) <= max_vis:
        return s
    if max_vis <= 1:
        return "…"
    out: list[str] = []
    vis = 0
    i = 0
    target = max_vis - 1
    while i < len(s) and vis < target:
        m = _ANSI_RE.match(s, i)
        if m:
            out.append(m.group(0))
            i = m.end()
        else:
            out.append(s[i])
            vis += 1
            i += 1
    out.append(f"{RESET}…")
    return "".join(out)


def is_api_onboarded(s: Settings | None = None) -> bool:
    """Kullanıcı en az bir kez API/provider seçimi yaptı mı veya anahtar girdi mi?"""
    if ONBOARDED_FILE.exists():
        return True
    s = s or load()
    if s.active_model:
        return True
    return any(bool(p.key()) for p in s.providers if not p.keyless)


def mark_api_onboarded() -> None:
    ensure_dirs()
    try:
        ONBOARDED_FILE.write_text(datetime.now().isoformat(), encoding="utf-8")
    except Exception:
        pass


def is_opencode_installed() -> bool:
    """OpenCode CLI sistemde kurulu mu?"""
    if shutil.which("opencode"):
        return True
    oc_bin = Path.home() / ".opencode" / "bin" / "opencode"
    if oc_bin.exists():
        os.environ["PATH"] = f"{oc_bin.parent}:{os.environ.get('PATH', '')}"
        return True
    return False


def ensure_opencode_installed(interactive: bool = True) -> bool:
    """OpenCode kurulu değilse kullanıcıya sorarak otomatik kurar."""
    if is_opencode_installed():
        return True
    print(f"\n  {YELLOW}⚠ OpenCode CLI bilgisayarında kurulu görünmüyor.{RESET}")
    print(f"  {GRAY}OpenCode sayesinde kredi kartı veya API anahtarı olmadan ücretsiz yapay zeka modellerini kullanabilirsin.{RESET}")
    if interactive:
        try:
            ans = input(_rl_prompt(f"  {CYAN}OpenCode şimdi otomatik kurulsun mu? (E/h): {RESET}")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if ans and not ans.startswith(("e", "y", "")):
            return False
    print(f"  {CYAN}⬇ OpenCode indiriliyor ve kuruluyor (https://opencode.ai/install)...{RESET}")
    rc = subprocess.run(["bash", "-lc", "curl -fsSL https://opencode.ai/install | bash"]).returncode
    oc_bin = Path.home() / ".opencode" / "bin"
    if oc_bin.exists():
        os.environ["PATH"] = f"{oc_bin}:{os.environ.get('PATH', '')}"
    if rc == 0 and is_opencode_installed():
        print(f"  {GREEN}✓ OpenCode başarıyla kuruldu!{RESET}\n")
        return True
    print(f"  {RED}✗ OpenCode kurulumu tamamlanamadı. İnternet bağlantını kontrol et.{RESET}\n")
    return False


def prompt_hotkey_setup(s: Settings) -> str:
    """Kullanıcıdan konuşmaya başlamak için isteğe bağlı bir anahtar tuş alır."""
    print(f"\n  {BOLD}⌨ Konuşmak İçin Anahtar Tuş (Kısayol) Ataması (İsteğe Bağlı):{RESET}")
    cur = keys.format_hotkey(s.hotkey) if s.hotkey else "Seçilmedi (yalnızca sesli uyandırma)"
    print(f"  {GRAY}Mevcut durum: {WHITE}{cur}{RESET}")
    print(f"  {GRAY}Bir tuş atarsan (örn. F8, F9, Pause, Insert), o tuşa her bastığında Metro hemen seni dinlemeye başlar.{RESET}")
    print(f"  {GRAY}Atamak istemiyorsan doğrudan Enter veya ESC tuşuna basabilirsin.{RESET}")

    sys.stdout.write(f"  {ORANGE}{BOLD}Şimdi atamak istediğin tuşa bas (Atlamak için Enter/ESC): {RESET}")
    sys.stdout.flush()

    captured = keys.capture_hotkey_interactive(timeout=12.0)
    if captured:
        s.hotkey = captured
        save(s)
        keys.get_watcher(s.hotkey).set_hotkey(s.hotkey)
        print(f"{GREEN}{BOLD}{keys.format_hotkey(captured)}{RESET}")
        print(f"  {GREEN}✓ Anahtar tuş {BOLD}{keys.format_hotkey(captured)}{RESET}{GREEN} olarak kaydedildi! Bu tuşa bastığında Metro konuşmaya başlayacak.{RESET}\n")
    else:
        print(f"{GRAY}Atlandı (tuş seçilmedi){RESET}")
        if s.hotkey:
            try:
                ans = input(_rl_prompt(f"  {GRAY}Mevcut kısayolu ({keys.format_hotkey(s.hotkey)}) kaldırmak ister misin? (e/H): {RESET}")).strip().lower()
                if ans.startswith(("e", "y")):
                    s.hotkey = ""
                    save(s)
                    keys.get_watcher("").set_hotkey("")
                    print(f"  {GRAY}• Anahtar tuş kaldırıldı.{RESET}")
            except (EOFError, KeyboardInterrupt):
                pass
        print()
    return s.hotkey


# ---------- Olay (Event) veri yolu ve İsteğe Bağlı Sohbet Geçmişi ----------
def _load_sessions() -> list[dict]:
    if not SESSIONS_FILE.exists():
        return []
    try:
        data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_sessions(sessions: list[dict]) -> None:
    ensure_dirs()
    tmp = SESSIONS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sessions[-25:], ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SESSIONS_FILE)


def start_new_chat_session() -> None:
    """Yeni bir sohbet oturumu başlatır (sonraki mesaj yeni başlık altında kaydedilir)."""
    sessions = _load_sessions()
    if sessions and not sessions[-1].get("closed"):
        sessions[-1]["closed"] = True
        _save_sessions(sessions)


def _record_session_turn(user_text: str, assistant_text: str, model: str | None = None) -> None:
    s = load()
    if not s.save_history:
        return
    u = (user_text or "").strip()
    a = (assistant_text or "").strip()
    if not u or not a:
        return

    now = time.time()
    sessions = _load_sessions()
    # Son oturum açıksa ve üzerinden 2 saatten az geçtiyse ve 16 turu aşmadıysa aynı oturuma ekle
    if (
        sessions
        and not sessions[-1].get("closed")
        and now - float(sessions[-1].get("updated_ts", 0)) < 7200
        and len(sessions[-1].get("turns", [])) < 16
    ):
        cur = sessions[-1]
    else:
        title = u[:48] + ("…" if len(u) > 48 else "")
        cur = {
            "id": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "updated_ts": now,
            "title": title,
            "turns": [],
        }
        sessions.append(cur)

    cur["updated_ts"] = now
    cur.setdefault("turns", []).append({
        "time": datetime.now().strftime("%H:%M"),
        "user": u,
        "assistant": a,
        "model": model or "",
    })
    _save_sessions(sessions)


def emit_event(event_type: str, **payload) -> None:
    """Daemon ve TUI arasında gerçek zamanlı olay paylaşımı."""
    ensure_dirs()
    record = {
        "ts": time.time(),
        "time": datetime.now().strftime("%H:%M:%S"),
        "type": event_type,
        **payload,
    }
    try:
        with EVENTS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if EVENTS_FILE.stat().st_size > 256 * 1024:
            lines = EVENTS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-150:]
            EVENTS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if event_type in ("assistant", "chat_turn"):
            _record_session_turn(
                payload.get("user_text", ""),
                payload.get("text", ""),
                payload.get("model"),
            )
    except Exception as e:
        log_.debug("event yazılamadı: %s", e)


def recent_turns(limit: int = 6) -> list[tuple[str, str]]:
    """Son konuşma çiftlerini (kullanıcı, metro) döndürür."""
    if not EVENTS_FILE.exists():
        return []
    turns: list[tuple[str, str]] = []
    last_user: str | None = None
    try:
        lines = EVENTS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-100:]
        for line in lines:
            ev = json.loads(line)
            t = ev.get("type")
            if t in ("user_voice", "user_text"):
                last_user = ev.get("text", "")
            elif t == "assistant" and last_user:
                turns.append((last_user, ev.get("text", "")))
                last_user = None
            elif t == "chat_turn":
                u, a = ev.get("user_text", ""), ev.get("text", "")
                if u and a:
                    turns.append((u, a))
    except Exception:
        pass
    return turns[-limit:]


# ---------- Pencere Yönetimi (Her Zaman Üstte + Otomatik Terminal Aç) ----------
def is_tui_running() -> bool:
    if not TUI_PID_FILE.exists():
        return False
    try:
        pid = int(TUI_PID_FILE.read_text().strip())
        os.kill(pid, 0)
        cmdline = Path(f"/proc/{pid}/cmdline").read_text(errors="ignore")
        if "metro" in cmdline or "python" in cmdline:
            return True
    except Exception:
        pass
    return False


def _desktop_env() -> dict[str, str]:
    env = os.environ.copy()
    uid = os.getuid()
    runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    env.setdefault("XDG_RUNTIME_DIR", runtime_dir)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus")
    if not env.get("WAYLAND_DISPLAY"):
        for sock in ("wayland-0", "wayland-1"):
            if (Path(runtime_dir) / sock).exists():
                env["WAYLAND_DISPLAY"] = sock
                break
    env.setdefault("DISPLAY", ":0")
    return env


def ensure_desktop_icon() -> None:
    """Uygulama logosunu (Logo 4) masaüstü kısayoluna ve KDE/Freedesktop ikon önbelleğine yerleştirir."""
    try:
        png_src = ROOT / "assets" / "metro-icon.png"
        svg_src = ROOT / "assets" / "metro-icon.svg"
        if not png_src.exists():
            return

        icons_base = Path.home() / ".local" / "share" / "icons" / "hicolor"
        for sz in ("128x128", "256x256", "512x512"):
            d = icons_base / sz / "apps"
            d.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(png_src, d / "metro-assistant.png")

        if svg_src.exists():
            sd = icons_base / "scalable" / "apps"
            sd.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(svg_src, sd / "metro-assistant.svg")

        apps_dir = Path.home() / ".local" / "share" / "applications"
        apps_dir.mkdir(parents=True, exist_ok=True)
        desktop_file = apps_dir / "metro-assistant.desktop"
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Metro Assistant\n"
            "Comment=Sesli ve Yazılı Yapay Zeka Asistanı\n"
            f"Exec={ROOT / 'metro'} chat -w\n"
            f"Icon={png_src}\n"
            "StartupWMClass=metro-assistant\n"
            "Terminal=false\n"
            "Categories=Utility;AudioVideo;\n"
            "StartupNotify=false\n"
        )
        old = desktop_file.read_text(encoding="utf-8", errors="ignore") if desktop_file.exists() else ""
        if old != content:
            desktop_file.write_text(content, encoding="utf-8")
            env = _desktop_env()
            for cmd in (["kbuildsycoca6", "--noincremental"], ["update-desktop-database", str(apps_dir)]):
                if shutil.which(cmd[0]):
                    subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log_.debug("masaüstü ikonu güncellenemedi: %s", e)


def ensure_always_on_top_rule(enabled: bool = True) -> None:
    """
    Metro Assistant penceresinin masaüstünde her zaman en üstte (Always on Top)
    kalması veya kapatıldığında anında normale dönmesi için KDE KWin kuralını ayarlar.
    """
    env = _desktop_env()
    try:
        if shutil.which("kwriteconfig6"):
            kwinrc = Path.home() / ".config" / "kwinrulesrc"
            existing_order = ""
            if kwinrc.exists():
                for line in kwinrc.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.startswith("Order="):
                        existing_order = line.split("=", 1)[1].strip()
                        break
            orders = [x for x in existing_order.split(",") if x and x != "metro-assistant"]
            orders.append("metro-assistant")
            new_order = ",".join(orders)

            subprocess.run(
                ["kwriteconfig6", "--file", "kwinrulesrc", "--group", "General", "--key", "Order", new_order],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["kwriteconfig6", "--file", "kwinrulesrc", "--group", "General", "--key", "count", str(len(orders))],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            rule_pairs = [
                ("Description", "Metro Assistant Pencere Kurali"),
                ("wmclass", "metro-assistant"),
                ("wmclassmatch", "1"),
                ("above", "true" if enabled else "false"),
                # aboverule=2 (Force) açık olan pencerenin de Her Zaman Üstte durumunu anında değiştirir
                ("aboverule", "2"),
            ]
            for k, v in rule_pairs:
                subprocess.run(
                    ["kwriteconfig6", "--file", "kwinrulesrc", "--group", "metro-assistant", "--key", k, v],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            for qdbus_bin in ("qdbus6", "qdbus"):
                if shutil.which(qdbus_bin):
                    subprocess.run(
                        [qdbus_bin, "org.kde.KWin", "/KWin", "reconfigure"],
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=3,
                    )
                    break
    except Exception as e:
        log_.debug("KWin her zaman üstte kuralı ayarlanamadı: %s", e)

    if shutil.which("wmctrl"):
        try:
            flag = "add,above" if enabled else "remove,above"
            subprocess.Popen(
                ["wmctrl", "-r", "Metro Assistant", "-b", flag],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


def ensure_terminal_open(settings: Settings | None = None) -> bool:
    """Açık bir Metro Assistant penceresi yoksa her zaman üstte duran yeni bir pencere açar."""
    s = load() if settings is None else load()
    ensure_desktop_icon()
    ensure_always_on_top_rule(s.always_on_top)

    if is_tui_running():
        return True

    if not s.auto_open_terminal:
        return False

    metro_bin = str(ROOT / "metro")
    title = "Metro Assistant"

    kitty_cmd = [
        "kitty",
        "--title",
        title,
        "--app-id",
        "metro-assistant",
        "--class",
        "metro-assistant",
        "-o",
        "initial_window_width=920",
        "-o",
        "initial_window_height=580",
        "-o",
        "remember_window_size=no",
        metro_bin,
        "chat",
    ]
    candidates: list[tuple[str, list[str]]] = [
        ("kitty", kitty_cmd),
        ("konsole", ["konsole", "--name", "metro-assistant", "-p", f"tabtitle={title}", "-e", metro_bin, "chat"]),
        ("alacritty", ["alacritty", "--title", title, "--class", "metro-assistant,metro-assistant", "-e", metro_bin, "chat"]),
        ("foot", ["foot", f"--title={title}", "--app-id=metro-assistant", metro_bin, "chat"]),
        ("ghostty", ["ghostty", f"--title={title}", "--class=metro-assistant", "-e", metro_bin, "chat"]),
        ("wezterm", ["wezterm", "start", "--class", "metro-assistant", "--", metro_bin, "chat"]),
        ("xterm", ["xterm", "-T", title, "-class", "metro-assistant", "-e", metro_bin, "chat"]),
    ]

    pref = (s.terminal or "auto").strip().lower()
    if pref and pref != "auto":
        candidates.sort(key=lambda item: 0 if item[0] == pref else 1)

    env = _desktop_env()
    for name, cmd in candidates:
        if not shutil.which(name):
            continue
        try:
            subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            log_.info("Metro Assistant terminali başlatıldı (%s)", name)
            return True
        except Exception as e:
            log_.warning("terminal başlatılamadı (%s): %s", name, e)
    return False


# ---------- Metro Assistant Konsolu ----------
class TUICtx(tools.Ctx):
    def __init__(self, tui: "MetroAssistantTUI"):
        self.tui = tui

    def say(self, text: str) -> None:
        if self.tui.speak_replies:
            tts.speak(text, self.tui.s.voice, self.tui.s.language, self.tui.s.max_reply_chars)

    def confirm(self, question: str, detail: str = "") -> bool:
        if detail:
            print(f"\n  {YELLOW}{BOLD}⚠ ONAY GEREKLİ — {question}{RESET}")
            for line in detail.splitlines()[:12]:
                print(f"    {GRAY}${RESET} {WHITE}{line}{RESET}")
        if self.tui.speak_replies:
            threading.Thread(
                target=tts.speak,
                args=(question, self.tui.s.voice, self.tui.s.language, self.tui.s.max_reply_chars),
                daemon=True,
            ).start()
        try:
            ans = input(_rl_prompt(f"\n  {ORANGE}{BOLD}👉 {question} [E]vet / [H]ayır (e/h): {RESET}")).strip().lower()
            return ans.startswith(("e", "y", "o", "t", "1"))
        except (EOFError, KeyboardInterrupt):
            return False

    def sleep_minutes(self, minutes: float) -> None:
        self.tui.print_system(f"Metro {minutes:g} dakika sessize alındı.")


class MetroAssistantTUI:
    def __init__(self, speak_replies: bool = False):
        ensure_dirs()
        self.s = load()
        self.speak_replies = speak_replies
        self.chain = LLMChain(self.s)
        self.ctx = TUICtx(self)
        self.agent = Agent(self.s, self.chain, self.ctx)
        for u, a in recent_turns(self.s.history_turns):
            self.agent.history.append((u, a))
        self.running = True
        self._print_lock = threading.Lock()
        self._in_prompt = False
        self._status_override: str | None = None
        self._awaiting_daemon_confirm = False

    # ---------- Görsel Yardımcılar (Asla Kaymayan / Taşmayan Responsive Kutu) ----------
    def _box_width(self) -> int:
        cols = shutil.get_terminal_size((80, 24)).columns
        # Gerçek terminal genişliğini ASLA aşma (satır kaymasını %100 önler)
        return max(38, min(86, cols - 2))

    def _set_window_title(self, status_text: str | None = None) -> None:
        st = (
            status_text
            or self._status_override
            or (STATUS_FILE.read_text(encoding="utf-8").strip() if STATUS_FILE.exists() else "hazır")
        )
        model_label = self.s.active_model or self.chain.last_used or "otomatik"
        title = f"◆ Metro Assistant — ● {st.upper()}  [{model_label}]"
        try:
            sys.stdout.write(f"\033]0;{title}\007")
            sys.stdout.flush()
        except Exception:
            pass

    def _active_model_display(self) -> str:
        if self.s.active_model:
            return f"{ self.s.active_model } (sabit)"
        cands = self.chain.candidates()
        if cands:
            p, m = cands[0]
            short_m = m.removeprefix("opencode/")
            return f"{p.id}/{short_m} (oto)"
        return "yok (/api)"

    def _build_header_lines(self, status_text: str | None = None) -> list[str]:
        w = self._box_width()
        inner = w - 4
        preset = tts.resolve_voice(self.s.voice)
        daemon_state = (
            status_text
            or self._status_override
            or (STATUS_FILE.read_text(encoding="utf-8").strip() if STATUS_FILE.exists() else "hazır")
        )

        def row(colored_text: str) -> str:
            fitted = _truncate_ansi(colored_text, inner)
            pad_len = max(0, inner - _vis_len(fitted))
            return f"{GRAY}│{RESET} {fitted}{' ' * pad_len} {GRAY}│{RESET}"

        left_col = f"{CYAN}{BOLD}◆ METRO ASSISTANT{RESET} {GRAY}v{__version__}{RESET}"
        if "dinliyor" in daemon_state.lower():
            right_col = f"{YELLOW}{BOLD}◉ DİNLİYOR (ESC=İptal){RESET}"
        else:
            right_col = f"{GREEN}{BOLD}● {daemon_state.upper()}{RESET}"

        gap = max(1, inner - _vis_len(left_col) - _vis_len(right_col))
        if _vis_len(left_col) + _vis_len(right_col) + 1 > inner:
            top_line = left_col
        else:
            top_line = f"{left_col}{' ' * gap}{right_col}"

        sep_line = f"{GRAY}{'─' * inner}{RESET}"

        hotkey_str = keys.format_hotkey(self.s.hotkey) if self.s.hotkey else "Yok"
        model_str = self._active_model_display()

        line1 = (
            f"{GRAY}Model:{RESET} {GREEN}{model_str}{RESET}  "
            f"{GRAY}•  Ses:{RESET} {WHITE}{preset.alias}{RESET}  "
            f"{GRAY}•  Tuş:{RESET} {CYAN}{hotkey_str}{RESET}"
        )
        line2 = (
            f"{GRAY}Komut:{RESET} {WHITE}/model{RESET} {GRAY}•{RESET} "
            f"{WHITE}/yeni{RESET} {GRAY}•{RESET} {WHITE}/gecmis{RESET} {GRAY}•{RESET} "
            f"{WHITE}/hafiza{RESET} {GRAY}•{RESET} {WHITE}/ayarlar{RESET} {GRAY}•{RESET} {WHITE}/help{RESET}"
        )

        return [
            f"{GRAY}╭─{'─' * inner}─╮{RESET}",
            row(top_line),
            row(sep_line),
            row(line1),
            row(line2),
            f"{GRAY}╰─{'─' * inner}─╯{RESET}",
        ]

    def render_header(self, clear_screen: bool = False) -> None:
        self._set_window_title()
        lines = self._build_header_lines()
        with self._print_lock:
            if clear_screen:
                sys.stdout.write("\033[r\033[2J\033[H")
            else:
                sys.stdout.write("\n")
            for line in lines:
                sys.stdout.write(line + "\n")
            sys.stdout.write("\n")
            sys.stdout.flush()

    def _async_print(self, block: str) -> None:
        with self._print_lock:
            if self._in_prompt:
                sys.stdout.write("\r\033[K")
            sys.stdout.write(block + "\n")
            if self._in_prompt:
                try:
                    readline.forced_update_display()
                except Exception:
                    sys.stdout.write(f"{CYAN}{BOLD}metro ❯{RESET} ")
            sys.stdout.flush()

    def print_user_voice(self, text: str, source: str = "stt") -> None:
        self._async_print(
            f"{CYAN}{BOLD}● Sen ({source}):{RESET} {WHITE}{text}{RESET}"
        )

    def print_assistant(self, text: str, model: str | None = None) -> None:
        tag = f" {GRAY}[{model}]{RESET}" if model else ""
        self._async_print(
            f"{ORANGE}{BOLD}◆ Metro{RESET}{tag}\n  {WHITE}{text}{RESET}\n"
        )

    def print_system(self, text: str) -> None:
        self._async_print(f"  {GRAY}• {text}{RESET}")

    # ---------- Arka Plan Olay İzleyici (Daemon <-> TUI) ----------
    def _watch_events(self) -> None:
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        EVENTS_FILE.touch(exist_ok=True)

        now = time.time()
        try:
            lines = EVENTS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-10:]
            for line in lines:
                ev = json.loads(line)
                if now - float(ev.get("ts", 0)) <= 8.0 and ev.get("origin") == "daemon":
                    self._render_daemon_event(ev)
        except Exception:
            pass

        with EVENTS_FILE.open("r", encoding="utf-8", errors="ignore") as f:
            f.seek(0, os.SEEK_END)
            while self.running:
                line = f.readline()
                if not line:
                    time.sleep(0.10)
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("origin") == "daemon":
                    self._render_daemon_event(ev)

    def _render_daemon_event(self, ev: dict) -> None:
        etype = ev.get("type")
        if etype == "wake":
            self._status_override = "dinliyor"
            self._set_window_title("dinliyor")
            self.print_system(f"{YELLOW}◉ Seni dinliyorum... (İptal için ESC){RESET}")
        elif etype == "cancelled":
            self._status_override = "hazır"
            self._set_window_title("hazır")
            self.print_system("Dinleme iptal edildi (ESC).")
        elif etype == "need_api":
            self._status_override = "hazır"
            self._set_window_title("hazır")
            self.print_system(
                "Hiç API çekmemişsin! Aşağıya /api yazarak hemen provider bağlayabilir ve modelleri çekebilirsin."
            )
        elif etype == "user_voice":
            text = ev.get("text", "")
            src = ev.get("source", "mikrofon")
            self.print_user_voice(text, src)
        elif etype == "thinking":
            self._status_override = "düşünüyor"
            self._set_window_title("düşünüyor")
            self.print_system("Düşünüyor...")
        elif etype == "confirm_request":
            self._awaiting_daemon_confirm = True
            self._status_override = "onay bekliyor"
            self._set_window_title("onay bekliyor")
            q = ev.get("question", "Şu komutu çalıştırayım mı?")
            detail = ev.get("detail", "")
            lines = [f"\n  {YELLOW}{BOLD}⚠ ONAY GEREKLİ — {q}{RESET}"]
            if detail:
                for dl in detail.splitlines()[:12]:
                    lines.append(f"    {GRAY}${RESET} {WHITE}{dl}{RESET}")
            lines.append(f"  {GRAY}(Sesle 'Evet / Hayır' diyebilir veya aşağıya e / h yazıp Enter'a basabilirsin){RESET}")
            self._async_print("\n".join(lines))
        elif etype == "confirm_resolved":
            self._awaiting_daemon_confirm = False
            self._status_override = "hazır"
            self._set_window_title("hazır")
            approved = bool(ev.get("approved"))
            if approved:
                self.print_system(f"{GREEN}✓ Onaylandı, komut çalıştırılıyor...{RESET}")
            else:
                self.print_system(f"{YELLOW}✗ Komut iptal edildi.{RESET}")
        elif etype == "assistant":
            self._status_override = "hazır"
            self._set_window_title("hazır")
            text = ev.get("text", "")
            model = ev.get("model")
            u = ev.get("user_text")
            if u and text:
                self.agent.history.append((u, text))
            self.print_assistant(text, model)

    # ---------- Model Seçim & Özelleştirme Merkezi (/model) ----------
    def run_model_manager(self, arg: str = "") -> None:
        self.s = load()
        self.chain = LLMChain(self.s)
        self.agent.s = self.s
        self.agent.chain = self.chain

        arg = arg.strip()
        low = arg.lower()

        # 1) Hızlı alt komutlar: /model auto, /model reset, /model ekle <p/m>, /model sil <p/m>
        if low in ("auto", "oto", "otomatik", "zincir"):
            self.s.active_model = ""
            save(self.s)
            self._set_window_title()
            print(f"\n  {GREEN}✓{RESET} Model seçimi {BOLD}Otomatik Zincir{RESET} moduna alındı (sıradaki en hızlı ücretsiz modeli kullanır).\n")
            return

        if low in ("reset", "sifirla", "sıfırla", "cooldown"):
            self.chain.reset_cooldowns()
            print(f"\n  {GREEN}✓{RESET} Tüm model bekleme süreleri (cooldown) sıfırlandı.\n")
            return

        if low.startswith(("add ", "ekle ")):
            spec = arg.split(maxsplit=1)[1].strip()
            if "/" not in spec:
                print(f"\n  {RED}Kullanım: /model ekle <sağlayıcı>/<model_adı> (Örn: /model ekle groq/llama-3.3-70b-versatile){RESET}\n")
                return
            pid, mid = spec.split("/", 1)
            target_p = next((p for p in self.s.providers if p.id == pid), None)
            if not target_p:
                print(f"\n  {RED}Bilinmeyen sağlayıcı: {pid}{RESET}\n")
                return
            model_val = spec if pid == "opencode" else mid
            if model_val not in target_p.models:
                target_p.models.insert(0, model_val)
            self.s.active_model = f"{pid}/{model_val}" if not model_val.startswith(f"{pid}/") else model_val
            save(self.s)
            self._set_window_title()
            print(f"\n  {GREEN}✓{RESET} Model eklendi ve aktif edildi: {BOLD}{self.s.active_model}{RESET}\n")
            return

        if low.startswith(("del ", "rm ", "sil ")):
            spec = arg.split(maxsplit=1)[1].strip()
            removed = False
            for p in self.s.providers:
                for m in list(p.models):
                    if spec in (m, f"{p.id}/{m}"):
                        p.models.remove(m)
                        removed = True
            if removed:
                save(self.s)
                print(f"\n  {GREEN}✓{RESET} '{spec}' model listesinden kaldırıldı.\n")
            else:
                print(f"\n  {YELLOW}'{spec}' listede bulunamadı.{RESET}\n")
            return

        # Mevcut tüm modelleri numaralandır
        rows = self.chain.status_rows()
        selectable: list[tuple[str, str]] = []
        for r in rows:
            if r["model"].startswith("("):
                continue
            selectable.append((r["provider"], r["model"]))

        # Doğrudan numara veya model adı verildiyse (/model 2 veya /model llama-3.3-70b-versatile)
        if arg:
            if arg.isdigit() and 1 <= int(arg) <= len(selectable):
                pid, mid = selectable[int(arg) - 1]
                full = mid if mid.startswith(f"{pid}/") else f"{pid}/{mid}"
                self.s.active_model = full
                save(self.s)
                self.chain.reset_cooldowns()
                self._set_window_title()
                print(f"\n  {GREEN}✓{RESET} Aktif model elle ayarlandı: {BOLD}{CYAN}{full}{RESET}\n")
                return

            # İsim eşleşmesi ara
            for pid, mid in selectable:
                full = mid if mid.startswith(f"{pid}/") else f"{pid}/{mid}"
                if arg.lower() in (full.lower(), mid.lower(), mid.removeprefix("opencode/").lower()):
                    self.s.active_model = full
                    save(self.s)
                    self.chain.reset_cooldowns()
                    self._set_window_title()
                    print(f"\n  {GREEN}✓{RESET} Aktif model elle ayarlandı: {BOLD}{CYAN}{full}{RESET}\n")
                    return

            # Listede olmasa bile provider/model formatındaysa kabul et
            if "/" in arg:
                self.s.active_model = arg
                save(self.s)
                self._set_window_title()
                print(f"\n  {GREEN}✓{RESET} Aktif model elle ayarlandı: {BOLD}{CYAN}{arg}{RESET}\n")
                return

        # Etkileşimli Model Seçim Menüsü
        print(f"\n  {BOLD}🧠 Model Yönetimi ve Elle Model Seçimi:{RESET}")
        cur_mode = f"{CYAN}{BOLD}{self.s.active_model}{RESET} (Elle Sabitlendi)" if self.s.active_model else f"{GREEN}Otomatik Yedekli Zincir{RESET}"
        print(f"  {GRAY}Şu anki mod: {cur_mode}{RESET}\n")

        idx = 1
        idx_map: dict[int, tuple[str, str]] = {}
        for row in rows:
            pid = row["provider"]
            mid = row["model"]
            if mid.startswith("("):
                continue
            full = mid if mid.startswith(f"{pid}/") else f"{pid}/{mid}"
            is_selected = (
                self.s.active_model in (full, mid)
                if self.s.active_model
                else (idx == 1 and (row["key"] or row.get("keyless")))
            )
            star = f"{GREEN}★{RESET}" if is_selected else " "
            if not row["key"] and not row.get("keyless"):
                st = f"{GRAY}anahtar yok (/api){RESET}"
            elif row["cooldown_s"]:
                st = f"{YELLOW}{row['cooldown_s'] // 60} dk beklemede{RESET}"
            else:
                st = f"{GREEN}hazır{RESET}"
            print(f"  {star} {CYAN}{idx:>2}){RESET} {BOLD}{pid:<12}{RESET} {WHITE}{mid:<36}{RESET} {st}")
            idx_map[idx] = (pid, mid)
            idx += 1

        print(f"\n    {CYAN}a){RESET} Otomatik Zincir moduna geç (biri dolunca diğerine atlar)")
        print(f"    {CYAN}e){RESET} Yeni bir model adını elle ekle ({GRAY}örn: groq/deepseek-r1-distill-llama-70b{RESET})")
        print(f"    {CYAN}p){RESET} Sağlayıcılardan güncel model listesini API'den çek (Pull)")
        print(f"    {CYAN}r){RESET} Model bekleme sayaçlarını (cooldown) sıfırla")
        print(f"    {CYAN}0){RESET} Vazgeç / Kapat\n")

        try:
            ch = input(_rl_prompt(f"  {ORANGE}{BOLD}Seçimin (1-{len(idx_map)} / a / e / p / r / 0) [0]: {RESET}")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not ch or ch == "0":
            print()
            return
        if ch == "a":
            self.run_model_manager("auto")
            return
        if ch == "r":
            self.run_model_manager("reset")
            return
        if ch == "p":
            self._pull_provider_models()
            return
        if ch == "e":
            try:
                spec = input(f"  {CYAN}Eklenecek model (<sağlayıcı>/<model_adı>): {RESET}").strip()
                if spec:
                    self.run_model_manager(f"ekle {spec}")
            except (EOFError, KeyboardInterrupt):
                print()
            return
        if ch.isdigit() and int(ch) in idx_map:
            self.run_model_manager(ch)
            return
        print(f"  {YELLOW}Geçersiz seçim.{RESET}\n")

    # ---------- Tüm Ayarlar & Özelleştirme Merkezi (/ayarlar) ----------
    def run_settings_menu(self) -> None:
        while True:
            self.s = load()
            hotkey_disp = keys.format_hotkey(self.s.hotkey) if self.s.hotkey else "Yok (Yalnızca Ses)"
            prompt_disp = (self.s.custom_prompt[:40] + "…") if len(self.s.custom_prompt) > 40 else (self.s.custom_prompt or "Varsayılan")
            aliases_disp = ", ".join(self.s.aliases.keys()) if self.s.aliases else "Yok"

            print(f"\n  {BOLD}⚙ Metro Assistant — Özelleştirme ve Ayarlar Merkezi:{RESET}")
            print(f"    {CYAN}1){RESET} Aktif LLM Modeli       : {GREEN}{self._active_model_display()}{RESET}")
            print(f"    {CYAN}2){RESET} Ses Profili (TTS)      : {WHITE}{self.s.voice}{RESET}")
            print(f"    {CYAN}3){RESET} Uyandırma Kelimeleri   : {WHITE}{', '.join(self.s.wake_words)}{RESET}")
            print(f"    {CYAN}4){RESET} Konuşma Kısayol Tuşu   : {CYAN}{hotkey_disp}{RESET}")
            print(f"    {CYAN}5){RESET} Pencere Her Zaman Üstte: {GREEN if self.s.always_on_top else GRAY}{'Açık' if self.s.always_on_top else 'Kapalı'}{RESET}")
            print(f"    {CYAN}6){RESET} Özel Kişilik / Talimat : {WHITE}{prompt_disp}{RESET}")
            print(f"    {CYAN}7){RESET} Özel Slash Komutları   : {WHITE}{aliases_disp}{RESET}")
            print(f"    {CYAN}8){RESET} Uyandırma Çan Sesi     : {GREEN if self.s.chime else GRAY}{'Açık' if self.s.chime else 'Kapalı'}{RESET}")
            print(f"    {CYAN}9){RESET} Sistem Komutu Onayı    : {GREEN if self.s.confirm_dangerous else YELLOW}{'Açık (Güvenli)' if self.s.confirm_dangerous else 'Kapalı'}{RESET}")
            print(f"   {CYAN}10){RESET} Sohbet Geçmişi Kaydı   : {GREEN if self.s.save_history else GRAY}{'Açık (/gecmis)' if self.s.save_history else 'Kapalı (Gizli Mod)'}{RESET}")
            print(f"    {CYAN}0){RESET} Kaydet ve Çık\n")

            try:
                c = input(_rl_prompt(f"  {ORANGE}{BOLD}Değiştirmek istediğin ayar (1-10 / 0) [0]: {RESET}")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not c or c == "0":
                print()
                break
            elif c == "1":
                self.run_model_manager()
            elif c == "2":
                self._handle_slash("/voice")
            elif c == "3":
                try:
                    w = input(f"  {CYAN}Uyandırma kelimeleri (virgülle ayır) [{', '.join(self.s.wake_words)}]: {RESET}").strip()
                    if w:
                        self.s.wake_words = [x.strip().lower() for x in w.split(",") if x.strip()]
                        save(self.s)
                        print(f"  {GREEN}✓ Uyandırma kelimeleri güncellendi (servisi yeniden başlatmak için: /sh systemctl --user restart metro.service){RESET}")
                except (EOFError, KeyboardInterrupt):
                    pass
            elif c == "4":
                prompt_hotkey_setup(self.s)
            elif c == "5":
                self._handle_slash("/top")
            elif c == "6":
                try:
                    p = input(f"  {CYAN}Metro nasıl davransın? (Boş = sıfırla): {RESET}").strip()
                    self.s.custom_prompt = p
                    save(self.s)
                    self.agent.s = self.s
                    print(f"  {GREEN}✓ Özel talimat güncellendi.{RESET}")
                except (EOFError, KeyboardInterrupt):
                    pass
            elif c == "7":
                self._manage_aliases("")
            elif c == "8":
                self.s.chime = not self.s.chime
                save(self.s)
                print(f"  {GREEN}✓ Çan sesi {'açıldı' if self.s.chime else 'kapatıldı'}.{RESET}")
            elif c == "9":
                self.s.confirm_dangerous = not self.s.confirm_dangerous
                save(self.s)
                print(f"  {GREEN}✓ Tehlikeli komut onayı {'açıldı' if self.s.confirm_dangerous else 'kapatıldı'}.{RESET}")
            elif c == "10":
                self.s.save_history = not self.s.save_history
                save(self.s)
                print(f"  {GREEN}✓ Sohbet geçmişi kaydı {'açıldı' if self.s.save_history else 'kapatıldı'}.{RESET}")

        self.render_header(clear_screen=False)

    # ---------- Kullanıcı Tanımlı Özel Komutlar (/komut) ----------
    def _manage_aliases(self, arg: str) -> None:
        self.s = load()
        arg = arg.strip()

        if not arg:
            print(f"\n  {BOLD}⚡ Özel Kısayol Komutların (/komut):{RESET}")
            if not self.s.aliases:
                print(f"  {GRAY}Henüz özel bir komut eklemedin.{RESET}")
            else:
                for k, v in self.s.aliases.items():
                    print(f"    {CYAN}/{k:<14}{RESET} → {WHITE}{v}{RESET}")
            print(f"\n  {GRAY}Yeni komut eklemek için:{RESET}")
            print(f"    {WHITE}/komut ekle /ozet Bana son konuşmayı 2 maddede özetle{RESET}")
            print(f"    {WHITE}/komut ekle /rock Spotify'dan Türkçe rock şarkısı çal{RESET}")
            print(f"    {WHITE}/komut ekle /guncelle /sh sudo pacman -Syu{RESET}")
            print(f"    {WHITE}/komut sil /ozet{RESET}\n")
            return

        low = arg.lower()
        if low.startswith(("sil ", "del ", "rm ")):
            name = arg.split(maxsplit=1)[1].strip().lstrip("/").lower()
            if name in self.s.aliases:
                del self.s.aliases[name]
                save(self.s)
                print(f"\n  {GREEN}✓{RESET} /{name} özel komutu silindi.\n")
            else:
                print(f"\n  {YELLOW}/{name} bulunamadı.{RESET}\n")
            return

        if low.startswith(("ekle ", "add ")):
            arg = arg.split(maxsplit=1)[1].strip()

        parts = arg.split(maxsplit=1)
        if len(parts) < 2:
            print(f"\n  {RED}Kullanım: /komut ekle </komut_adı> <yapılacak_işlem_veya_mesaj>{RESET}\n")
            return

        cmd_name = parts[0].lstrip("/").lower()
        cmd_val = parts[1].strip().lstrip("->").strip()
        self.s.aliases[cmd_name] = cmd_val
        save(self.s)
        print(f"\n  {GREEN}✓{RESET} Özel komut oluşturuldu: {BOLD}{CYAN}/{cmd_name}{RESET} → {WHITE}{cmd_val}{RESET}\n")

    # ---------- Kalıcı Hafıza Yönetimi (/hafiza) ----------
    def _manage_memory(self, arg: str = "") -> None:
        arg = arg.strip()
        low = arg.lower()

        if low in ("temizle", "clear", "sifirla", "sıfırla", "reset"):
            memory.clear_all()
            print(f"\n  {GREEN}✓{RESET} Kalıcı hafıza tamamen temizlendi.\n")
            return

        if low.startswith(("sil ", "del ", "rm ", "unut ")):
            q = arg.split(maxsplit=1)[1].strip()
            if memory.remove_fact(q):
                print(f"\n  {GREEN}✓{RESET} '{q}' hafızadan silindi.\n")
            else:
                print(f"\n  {YELLOW}'{q}' hafızada bulunamadı.{RESET}\n")
            return

        if low.startswith(("ekle ", "add ", "kaydet ")):
            fact = arg.split(maxsplit=1)[1].strip()
            saved = memory.add_fact(fact, "kullanici")
            if saved:
                print(f"\n  {GREEN}✓{RESET} Hafızaya eklendi: {WHITE}{saved}{RESET}\n")
            return

        if arg:
            saved = memory.add_fact(arg, "kullanici")
            if saved:
                print(f"\n  {GREEN}✓{RESET} Hafızaya eklendi: {WHITE}{saved}{RESET}\n")
            return

        prof, facts = memory.list_all()
        print(f"\n  {BOLD}🧠 Metro Kalıcı Hafıza (Ultra Düşük Token Modu):{RESET}")
        if not prof and not facts:
            print(f"  {GRAY}Henüz kayıtlı bir bilgi yok. Konuşurken 'Benim adım ...', 'Unutma ...' dediğinde otomatik öğrenir.{RESET}")
        else:
            if prof:
                print(f"  {CYAN}Profil:{RESET}")
                for k, v in prof.items():
                    print(f"    • {BOLD}{k.capitalize()}:{RESET} {WHITE}{v}{RESET}")
            if facts:
                print(f"  {CYAN}Hatırlanan Bilgiler ({len(facts)}):{RESET}")
                for idx, item in enumerate(facts, 1):
                    print(f"    {CYAN}{idx:>2}){RESET} {WHITE}{item.get('text', '')}{RESET}")
        print(f"\n  {GRAY}Komutlar:{RESET}")
        print(f"    {WHITE}/hafiza ekle <bilgi>{RESET}    Yeni bilgi ekle")
        print(f"    {WHITE}/hafiza sil <no|kelime>{RESET} Bilgiyi sil")
        print(f"    {WHITE}/hafiza temizle{RESET}         Tüm hafızayı sıfırla\n")

    # ---------- İsteğe Bağlı Sohbet Geçmişi (/gecmis) ----------
    def _manage_history(self, arg: str = "") -> None:
        self.s = load()
        arg = arg.strip()
        low = arg.lower()

        if low in ("ac", "aç", "on", "aktif"):
            self.s.save_history = True
            save(self.s)
            print(f"\n  {GREEN}✓{RESET} Sohbet geçmişi kaydı {BOLD}AÇILDI{RESET}.\n")
            return

        if low in ("kapat", "off", "pasif", "gizli"):
            self.s.save_history = False
            save(self.s)
            print(f"\n  {GREEN}✓{RESET} Sohbet geçmişi kaydı {BOLD}KAPATILDI{RESET} (yeni sohbetler kaydedilmeyecek).\n")
            return

        if low in ("temizle", "clear", "sifirla", "sıfırla"):
            SESSIONS_FILE.unlink(missing_ok=True)
            EVENTS_FILE.unlink(missing_ok=True)
            self.agent.history.clear()
            print(f"\n  {GREEN}✓{RESET} Tüm kayıtlı sohbet geçmişi temizlendi.\n")
            return

        sessions = list(reversed(_load_sessions()))

        if low.startswith(("sil ", "del ", "rm ")):
            q = arg.split(maxsplit=1)[1].strip()
            if q.isdigit() and 1 <= int(q) <= len(sessions):
                target_id = sessions[int(q) - 1].get("id")
                raw_all = [s for s in _load_sessions() if s.get("id") != target_id]
                _save_sessions(raw_all)
                print(f"\n  {GREEN}✓{RESET} #{q} numaralı sohbet geçmişten silindi.\n")
            else:
                print(f"\n  {YELLOW}Geçerli bir sohbet numarası gir (/gecmis sil 1).{RESET}\n")
            return

        if arg.isdigit() and 1 <= int(arg) <= len(sessions):
            chosen = sessions[int(arg) - 1]
            self.render_header(clear_screen=True)
            print(f"  {CYAN}{BOLD}📂 Sohbet Yüklendi:{RESET} {WHITE}{chosen.get('title', '')}{RESET} {GRAY}({chosen.get('date', '')}){RESET}\n")
            self.agent.history.clear()
            for t in chosen.get("turns", []):
                u = t.get("user", "")
                a = t.get("assistant", "")
                m = t.get("model") or None
                print(f"{CYAN}{BOLD}● Sen:{RESET} {WHITE}{u}{RESET}")
                tag = f" {GRAY}[{m}]{RESET}" if m else ""
                print(f"{ORANGE}{BOLD}◆ Metro{RESET}{tag}\n  {WHITE}{a}{RESET}\n")
                self.agent.history.append((u, a))
            return

        st_badge = f"{GREEN}AÇIK{RESET}" if self.s.save_history else f"{YELLOW}KAPALI (Gizli Mod){RESET}"
        print(f"\n  {BOLD}🕒 Sohbet Geçmişi (Durum: {st_badge}{BOLD}):{RESET}")
        if not sessions:
            print(f"  {GRAY}Henüz kaydedilmiş bir sohbet geçmişi yok.{RESET}")
        else:
            for idx, sess in enumerate(sessions[:12], 1):
                cnt = len(sess.get("turns", []))
                dt = sess.get("date", "")
                title = sess.get("title", "Sohbet")
                print(f"    {CYAN}{idx:>2}){RESET} {GRAY}[{dt}]{RESET} {WHITE}{title:<42}{RESET} {GRAY}({cnt} mesaj){RESET}")

        print(f"\n    {CYAN}y){RESET} Yeni temiz sohbet başlat (/yeni)")
        print(f"    {CYAN}k){RESET} Sohbet geçmişi kaydını {'Kapat' if self.s.save_history else 'Aç'}")
        print(f"    {CYAN}t){RESET} Tüm sohbet geçmişini temizle")
        print(f"    {CYAN}0){RESET} Kapat\n")

        try:
            pick = input(_rl_prompt(f"  {ORANGE}{BOLD}Görüntülemek/devam etmek istediğin sohbet (1-{len(sessions[:12])} / y / k / t / 0) [0]: {RESET}")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not pick or pick == "0":
            print()
            return
        if pick == "y":
            self._handle_slash("/yeni")
            return
        if pick == "k":
            self._manage_history("kapat" if self.s.save_history else "ac")
            return
        if pick == "t":
            self._manage_history("temizle")
            return
        if pick.isdigit() and 1 <= int(pick) <= len(sessions[:12]):
            self._manage_history(pick)
            return

    # ---------- Provider / API Bağlama ve Model Çekme Sihirbazı ----------
    def _pull_provider_models(self, pid: str | None = None) -> None:
        self.s = load()
        self.chain = LLMChain(self.s)
        targets = [p for p in self.s.providers if (pid is None or p.id == pid) and p.usable()]
        if not targets:
            print(f"\n  {YELLOW}Aktif sağlayıcı bulunamadı. Önce /api ile bir sağlayıcı bağla.{RESET}\n")
            return
        print(f"\n  {CYAN}🔄 Sağlayıcılardan güncel API modelleri çekiliyor...{RESET}")
        for p in targets:
            if p.id == "opencode" and not is_opencode_installed():
                ensure_opencode_installed(interactive=True)
            models = self.chain._discover(p, force=True)
            if models:
                p.models = models[:8] if not p.models else p.models
                preview = ", ".join(models[:4])
                more = f" (+{len(models) - 4} diğer)" if len(models) > 4 else ""
                print(f"  {GREEN}✓{RESET} {BOLD}{p.id:<12}{RESET} → {len(models)} model çekildi: {WHITE}{preview}{RESET}{GRAY}{more}{RESET}")
            else:
                print(f"  {YELLOW}•{RESET} {BOLD}{p.id:<12}{RESET} → model listesi alınamadı (varsayılanlar korunuyor)")
        save(self.s)
        mark_api_onboarded()
        self._set_window_title()
        print()

    def run_api_wizard(self, first_time: bool = False) -> None:
        self.s = load()
        self.chain = LLMChain(self.s)
        providers = [p for p in self.s.providers if p.id in PROVIDER_META]

        if first_time:
            print(f"  {YELLOW}{BOLD}⚡ Hiç API çekmemişsin! Önce bir provider bağlayıp modelleri çekelim.{RESET}")
            print(f"  {GRAY}(OpenCode ücretsiz modellerini tek tuşla çekebilir veya Groq/Gemini/OpenRouter anahtarı girebilirsin){RESET}\n")
            if not is_opencode_installed():
                ensure_opencode_installed(interactive=True)

        while True:
            print(f"  {BOLD}🔌 Sağlayıcı (Provider) & API Bağlama Merkezi:{RESET}")
            for idx, p in enumerate(providers, 1):
                title, _ = PROVIDER_META.get(p.id, (p.id, ""))
                if p.key():
                    status = f"{GREEN}● Anahtar Bağlı ({len(p.models)} model){RESET}"
                elif p.keyless and p.id == "opencode":
                    if is_opencode_installed():
                        status = f"{CYAN}● CLI Hazır ({len(p.models)} ücretsiz model){RESET}"
                    else:
                        status = f"{YELLOW}○ OpenCode Kurulu Değil (Otomatik Kur){RESET}"
                else:
                    status = f"{GRAY}○ Bağlı Değil{RESET}"
                print(f"    {CYAN}{idx}){RESET} {BOLD}{p.id:<12}{RESET} {status:<42} {GRAY}{title}{RESET}")

            print(f"    {CYAN}m){RESET} {WHITE}Aktif modeli elle seç veya değiştir (/model){RESET}")
            print(f"    {CYAN}p){RESET} {WHITE}Tüm bağlı sağlayıcılardan güncel modelleri API'den çek (Pull){RESET}")
            print(f"    {CYAN}k){RESET} {WHITE}Konuşmak için anahtar tuş (kısayol) ata (İsteğe bağlı){RESET}")
            print(f"    {CYAN}0){RESET} {GRAY}Tamamla ve sohbete başla{RESET}\n")

            try:
                choice = input(_rl_prompt(f"  {ORANGE}{BOLD}Seçimin (1-{len(providers)} / m / p / k / 0) [1]:{RESET} ")).strip().lower() or "1"
            except (EOFError, KeyboardInterrupt):
                print()
                mark_api_onboarded()
                break

            if choice in ("0", "q", "exit", "gec", "tamam"):
                mark_api_onboarded()
                print(f"\n  {GREEN}✓{RESET} Hazır! Artık 'Hey Metro' diyerek, kısayol tuşunla veya aşağıdan yazarak konuşabilirsin.\n")
                break

            if choice == "m":
                self.run_model_manager()
                continue

            if choice == "p":
                self._pull_provider_models()
                break

            if choice == "k":
                prompt_hotkey_setup(self.s)
                continue

            if not choice.isdigit() or not (1 <= int(choice) <= len(providers)):
                print(f"  {YELLOW}Lütfen listeden geçerli bir numara seç.{RESET}\n")
                continue

            p = providers[int(choice) - 1]
            title, url = PROVIDER_META.get(p.id, (p.id, ""))
            print(f"\n  {BOLD}→ {title}{RESET} ({CYAN}{url}{RESET})")

            if p.id == "opencode":
                if not is_opencode_installed():
                    ensure_opencode_installed(interactive=True)
                print(f"    {CYAN}1){RESET} Ücretsiz OpenCode modellerini API'den şimdi çek ve aktif et {GREEN}(Önerilen){RESET}")
                print(f"    {CYAN}2){RESET} `opencode auth login` çalıştır (Google / Nvidia / vb. hesap bağla)")
                print(f"    {CYAN}3){RESET} OpenCode Zen API anahtarı gir")
                sub = input(f"  {ORANGE}Seçim [1]: {RESET}").strip() or "1"
                if sub == "2":
                    subprocess.run(["opencode", "auth", "login"])
                elif sub == "3":
                    key = input(f"  {CYAN}OpenCode API Anahtarı:{RESET} ").strip()
                    if key:
                        p.api_key = key
                print(f"  {CYAN}🔄 OpenCode ücretsiz modelleri çekiliyor...{RESET}")
                models = self.chain._discover(p, force=True)
                if models:
                    p.models = models
                    print(f"  {GREEN}✓ {len(models)} ücretsiz model çekildi:{RESET}")
                    for i, m in enumerate(models, 1):
                        print(f"    {CYAN}{i}){RESET} {WHITE}{m}{RESET}")
                    try:
                        pick = input(f"\n  {ORANGE}Başlangıç modeli seç (1-{len(models)} / Enter=Otomatik): {RESET}").strip()
                        if pick.isdigit() and 1 <= int(pick) <= len(models):
                            self.s.active_model = models[int(pick) - 1]
                            print(f"  {GREEN}✓ Aktif model: {BOLD}{self.s.active_model}{RESET}")
                    except (EOFError, KeyboardInterrupt):
                        pass
                save(self.s)
                mark_api_onboarded()
                print(f"\n  {GREEN}✓ OpenCode modelleri bağlandı!{RESET}\n")
                if first_time:
                    prompt_hotkey_setup(self.s)
                self._set_window_title()
                break

            open_br = input(f"  {GRAY}Anahtar sayfasını tarayıcıda açayım mı? (e/H):{RESET} ").strip().lower()
            if open_br.startswith(("e", "y")):
                tools._spawn(["xdg-open", url])

            key = input(f"  {CYAN}{p.id} API Anahtarı (boş = iptal):{RESET} ").strip()
            if not key:
                print(f"  {GRAY}İptal edildi.{RESET}\n")
                continue

            p.api_key = key
            p.enabled = True
            save(self.s)

            print(f"  {CYAN}🔄 {p.id} sunucusundan modeller çekiliyor...{RESET}")
            models = self.chain._discover(p, force=True)
            if models:
                if not p.models:
                    p.models = models[:6]
                print(f"  {GREEN}✓ {len(models)} model API'den çekildi!{RESET} İlk modeller: {', '.join(models[:5])}")

            test_model = (p.models or models or ["?"])[0]
            print(f"  {CYAN}⚡ {p.id}/{test_model} canlı test ediliyor...{RESET}")
            try:
                reply = self.chain._call(p, test_model, [{"role": "user", "content": "Sadece OK yaz."}], 15, 0.0)
                print(f"  {GREEN}✓ Bağlantı başarılı! Yanıt: {reply[:40]!r}{RESET}\n")
                mark_api_onboarded()
                self.agent = Agent(self.s, self.chain, self.ctx)
                if first_time:
                    prompt_hotkey_setup(self.s)
                self._set_window_title()
                break
            except Exception as e:
                reason = getattr(e, "reason", str(e))
                print(f"  {RED}✗ Test başarısız ({reason}). Anahtarı kontrol edip tekrar deneyebilirsin.{RESET}\n")

    # ---------- Slash Komutları ----------
    def _handle_slash(self, raw: str) -> bool:
        parts = raw.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        # Kullanıcı tanımlı özel slash komutlarını kontrol et
        alias_key = cmd.lstrip("/")
        if alias_key in self.s.aliases:
            target_cmd = self.s.aliases[alias_key]
            if arg:
                target_cmd = f"{target_cmd} {arg}"
            print(f"  {GRAY}⚡ /{alias_key} → {target_cmd}{RESET}")
            if target_cmd.startswith("/"):
                return self._handle_slash(target_cmd)
            self._run_user_message(target_cmd)
            return True

        if cmd in ("/exit", "/quit", "/q"):
            self.running = False
            return True

        if cmd in ("/help", "/h", "/yardim", "/yardım"):
            print(f"\n  {BOLD}Metro Assistant — Komut ve Özelleştirme Rehberi:{RESET}")
            print(f"  {CYAN}/model{RESET} {GRAY}(veya /m){RESET}      Aktif modeli elle seç, değiştir, yeni model ekle veya sıfırla")
            print(f"  {CYAN}/model <no|ad>{RESET}      Belirtilen modele anında geç (örn: {WHITE}/model 2{RESET}, {WHITE}/model auto{RESET})")
            print(f"  {CYAN}/api{RESET} {GRAY}(veya /p){RESET}        Sağlayıcı (Provider) bağla ve API anahtarı gir")
            print(f"  {CYAN}/pull{RESET}               Bağlı sağlayıcılardan güncel modelleri API'den çek")
            print(f"  {CYAN}/voice{RESET} {GRAY}(veya /v){RESET}      Ses profilini numaralı menüden seç veya test et")
            print(f"  {CYAN}/hotkey{RESET} {GRAY}(veya /tus){RESET}   Konuşmak için anahtar tuş ata veya kaldır")
            print(f"  {CYAN}/wake <kelimeler>{RESET}   Uyandırma kelimelerini özelleştir (örn: {WHITE}/wake metro, asistan{RESET})")
            print(f"  {CYAN}/yeni{RESET} {GRAY}(veya /new){RESET}     Yeni temiz sohbet başlat (arka plan oturumunu da yeniler)")
            print(f"  {CYAN}/gecmis{RESET} {GRAY}(veya /his){RESET}   İsteğe bağlı sohbet geçmişini görüntüle, devam et veya aç/kapat")
            print(f"  {CYAN}/hafiza{RESET} {GRAY}(veya /mem){RESET}    Kalıcı hafızayı görüntüle, bilgi ekle veya sil (düşük token)")
            print(f"  {CYAN}/prompt <talimat>{RESET}   Metro'nun kişiliğini/konuşma tarzını özelleştir")
            print(f"  {CYAN}/komut{RESET} {GRAY}(veya /alias){RESET}  Kendi özel slash komutlarını oluştur (örn: {WHITE}/komut ekle /ozet ...{RESET})")
            print(f"  {CYAN}/ayarlar{RESET} {GRAY}(veya /set){RESET}  Tüm ayarları tek menüden görüntüle ve değiştir")
            print(f"  {CYAN}/top{RESET}                Pencereyi her zaman üstte tutma modunu aç/kapat")
            print(f"  {CYAN}/speak{RESET}              Yazılı sohbette sesli okumayı aç/kapat")
            print(f"  {CYAN}/listen{RESET}             Mikrofondan hemen sesli komut dinle (ESC ile iptal)")
            print(f"  {CYAN}/sh <komut>{RESET}         Terminal komutu çalıştır")
            print(f"  {CYAN}/clear{RESET}              Ekranı temizle ve üst kutuyu yenile")
            print(f"  {CYAN}/exit{RESET}               Metro Assistant penceresini kapat\n")
            return True

        if cmd in ("/model", "/models", "/m"):
            self.run_model_manager(arg)
            return True

        if cmd in ("/status", "/durum"):
            self.render_header(clear_screen=False)
            return True

        if cmd in ("/ayarlar", "/config", "/settings", "/set"):
            self.run_settings_menu()
            return True

        if cmd in ("/komut", "/alias", "/aliases", "/kisayol"):
            self._manage_aliases(arg)
            return True

        if cmd in ("/hafiza", "/hafıza", "/memory", "/mem", "/bellek"):
            self._manage_memory(arg)
            return True

        if cmd in ("/gecmis", "/geçmiş", "/history", "/his", "/sohbetler"):
            self._manage_history(arg)
            return True

        if cmd in ("/yeni", "/new", "/reset"):
            self.agent.history.clear()
            self.chain.reset_session()
            start_new_chat_session()
            self.render_header(clear_screen=True)
            print(f"  {GREEN}✓{RESET} Yeni temiz sohbet başlatıldı (kalıcı hafızan korunuyor).\n")
            return True

        if cmd in ("/prompt", "/persona", "/sistem", "/system"):
            if not arg:
                cur = self.s.custom_prompt or "(Varsayılan — özel talimat yok)"
                print(f"\n  {BOLD}Aktif Özel Talimat:{RESET} {WHITE}{cur}{RESET}")
                print(f"  {GRAY}Değiştirmek için: {WHITE}/prompt Kısa, esprili ve samimi cevap ver{RESET}")
                print(f"  {GRAY}Sıfırlamak için : {WHITE}/prompt sıfırla{RESET}\n")
                return True
            if arg.lower() in ("sifirla", "sıfırla", "reset", "off", "sil"):
                self.s.custom_prompt = ""
                save(self.s)
                self.agent.s = self.s
                print(f"\n  {GREEN}✓{RESET} Özel talimat sıfırlandı.\n")
                return True
            self.s.custom_prompt = arg
            save(self.s)
            self.agent.s = self.s
            print(f"\n  {GREEN}✓{RESET} Metro özel talimatı kaydedildi: {WHITE}{arg}{RESET}\n")
            return True

        if cmd in ("/wake", "/kelime", "/uyandir"):
            if not arg:
                print(f"\n  {BOLD}Aktif Uyandırma Kelimeleri:{RESET} {CYAN}{', '.join(self.s.wake_words)}{RESET}")
                print(f"  {GRAY}Değiştirmek için: {WHITE}/wake metro, hey metro, bilgisayar{RESET}\n")
                return True
            words = [w.strip().lower() for w in arg.split(",") if w.strip()]
            if words:
                self.s.wake_words = words
                save(self.s)
                subprocess.Popen(["systemctl", "--user", "restart", "metro.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"\n  {GREEN}✓{RESET} Uyandırma kelimeleri güncellendi ve arka plan servisine uygulandı: {BOLD}{', '.join(words)}{RESET}\n")
            return True

        if cmd in ("/api", "/provider", "/providers", "/connect", "/setup", "/p"):
            self.run_api_wizard(first_time=False)
            return True

        if cmd == "/pull":
            self._pull_provider_models(arg or None)
            return True

        if cmd in ("/hotkey", "/key", "/tus"):
            if arg in ("off", "none", "sil", "kaldır", "kaldir", "yok"):
                self.s.hotkey = ""
                save(self.s)
                keys.get_watcher("").set_hotkey("")
                print(f"\n  {GREEN}✓{RESET} Anahtar tuş kaldırıldı (yalnızca sesle uyandırma aktif).\n")
                return True
            if arg:
                norm = keys.normalize_hotkey(arg)
                if norm:
                    self.s.hotkey = norm
                    save(self.s)
                    keys.get_watcher(norm).set_hotkey(norm)
                    print(f"\n  {GREEN}✓{RESET} Anahtar tuş {BOLD}{keys.format_hotkey(norm)}{RESET} olarak ayarlandı.\n")
                    return True
            prompt_hotkey_setup(self.s)
            return True

        if cmd in ("/top", "/pin"):
            self.s.always_on_top = not self.s.always_on_top
            save(self.s)
            ensure_always_on_top_rule(self.s.always_on_top)
            st = "AÇIK (pencere her zaman üstte)" if self.s.always_on_top else "KAPALI (normal pencere)"
            print(f"\n  {GREEN}✓{RESET} Her zaman üstte modu: {BOLD}{st}{RESET}\n")
            return True

        if cmd == "/clear":
            self.agent.history.clear()
            self.chain.reset_session()
            start_new_chat_session()
            self.render_header(clear_screen=True)
            return True

        if cmd == "/speak":
            self.speak_replies = not self.speak_replies
            durum = "AÇIK (yazılı yanıtlar da seslendirilecek)" if self.speak_replies else "KAPALI (yalnızca sesli komutlar seslendirilir)"
            print(f"\n  {GREEN}✓{RESET} Sesli yanıt modu: {BOLD}{durum}{RESET}\n")
            return True

        if cmd in ("/voice", "/v", "/ses"):
            voice_list = tts.voices()
            if not arg:
                print(f"\n  {BOLD}🎙 Ses Profilleri (Aktif: {CYAN}{self.s.voice}{RESET}{BOLD}):{RESET}")
                for idx, (alias, gender, desc) in enumerate(voice_list, 1):
                    mark = f"{GREEN}●{RESET}" if alias == self.s.voice.lower() else " "
                    print(f"  {mark} {CYAN}{idx}){RESET} {BOLD}{alias:<13}{RESET} {GRAY}{gender:<6}{RESET} {desc}")
                try:
                    pick = input(_rl_prompt(f"\n  {ORANGE}{BOLD}Seçmek istediğin ses numarası veya adı (Enter=Kapat): {RESET}")).strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return True
                if not pick:
                    print()
                    return True
                arg = pick

            if arg.isdigit() and 1 <= int(arg) <= len(voice_list):
                arg = voice_list[int(arg) - 1][0]

            preset = tts.resolve_voice(arg)
            self.s.voice = preset.alias
            save(self.s)
            print(f"\n  {GREEN}✓{RESET} Ses {BOLD}{preset.alias}{RESET} ({preset.desc}) olarak ayarlandı. Test ediliyor...")
            tts.speak("Merhaba, ben Metro. Yeni sesimle hazırım.", self.s.voice, self.s.language)
            print()
            return True

        if cmd in ("/listen", "/mic"):
            self._listen_once()
            return True

        if cmd == "/sh":
            if not arg:
                print(f"  {RED}Kullanım: /sh <komut>{RESET}\n")
                return True
            rc, out = tools._run(["bash", "-lc", arg], timeout=60)
            color = GREEN if rc == 0 else RED
            print(f"\n  {color}$ {arg}{RESET}")
            if out:
                for line in out.splitlines()[:40]:
                    print(f"  {GRAY}│{RESET} {line}")
            print()
            return True

        print(f"  {YELLOW}Bilinmeyen komut: {cmd}. Tüm komutlar için /help veya /ayarlar yaz.{RESET}\n")
        return True

    def _listen_once(self) -> None:
        from .stt import Stt
        from .wake import WakeWord

        try:
            self._status_override = "dinliyor"
            self._set_window_title("dinliyor")
            print(f"\n  {CYAN}🎙 Mikrofon açılıyor, lütfen konuş... (İptal için ESC){RESET}")
            wake = WakeWord(vosk_path(self.s), self.s.wake_words, self.s.sample_rate)
            stt = Stt(self.s, local=wake)
            mic = audio.Mic(self.s.sample_rate, self.s.input_device)
            floor = mic.calibrate(0.5)
            if self.s.chime:
                audio.chime(self.s.sample_rate)
            samples = mic.record_command(floor, self.s.silence_sec, self.s.max_record_sec)
            cancelled = mic.last_cancelled
            mic.close()
            self._status_override = "hazır"
            self._set_window_title("hazır")
            if cancelled:
                if self.s.chime:
                    audio.cancel_chime(self.s.sample_rate)
                print(f"  {YELLOW}Dinleme iptal edildi (ESC).{RESET}\n")
                return
            if samples is None:
                print(f"  {YELLOW}Ses algılanmadı.{RESET}\n")
                return
            text, src = stt.transcribe(audio.to_wav_bytes(samples, self.s.sample_rate))
            if not text:
                print(f"  {YELLOW}Konuşma anlaşılamadı.{RESET}\n")
                return
            self.print_user_voice(text, src)
            tts.prewarm()
            reply = self.agent.handle(text)
            emit_event(
                "chat_turn",
                origin="tui",
                user_text=text,
                text=reply,
                model=self.chain.last_used,
            )
            tts.speak(
                reply,
                self.s.voice,
                self.s.language,
                self.s.max_reply_chars,
                on_start=lambda: self.print_assistant(reply, self.chain.last_used),
            )
        except Exception as e:
            self._status_override = "hazır"
            self._set_window_title("hazır")
            print(f"  {RED}Mikrofon hatası: {e}{RESET}\n")

    def _run_user_message(self, user_in: str) -> None:
        self._status_override = "düşünüyor"
        self._set_window_title("düşünüyor")
        sys.stdout.write(f"  {GRAY}✻ Düşünüyor...{RESET}\r")
        sys.stdout.flush()

        if self.speak_replies:
            tts.prewarm()

        self.s = load()
        self.chain.s = self.s
        self.agent.s = self.s

        reply = self.agent.handle(user_in)
        self._status_override = "hazır"
        self._set_window_title("hazır")
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

        emit_event(
            "chat_turn",
            origin="tui",
            user_text=user_in,
            text=reply,
            model=self.chain.last_used,
        )

        if self.speak_replies:
            tts.speak(
                reply,
                self.s.voice,
                self.s.language,
                self.s.max_reply_chars,
                on_start=lambda: self.print_assistant(reply, self.chain.last_used),
            )
        else:
            self.print_assistant(reply, self.chain.last_used)

    # ---------- Ana Döngü ----------
    def run(self) -> int:
        try:
            TUI_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass

        self.s = load()
        ensure_desktop_icon()
        ensure_always_on_top_rule(self.s.always_on_top)

        try:
            if HISTORY_FILE.exists():
                readline.read_history_file(str(HISTORY_FILE))
        except Exception:
            pass

        self.render_header(clear_screen=True)

        # İlk açılışta henüz hiçbir API çekilmediyse / provider bağlanmadıysa otomatik sor
        if not is_api_onboarded(self.s):
            self.run_api_wizard(first_time=True)

        watcher = threading.Thread(target=self._watch_events, daemon=True)
        watcher.start()

        try:
            while self.running:
                try:
                    self._in_prompt = True
                    prompt_str = (
                        f"  {ORANGE}{BOLD}👉 Onaylıyor musun? [E]vet / [H]ayır (e/h): {RESET}"
                        if self._awaiting_daemon_confirm
                        else f"{CYAN}{BOLD}metro ❯{RESET} "
                    )
                    user_in = input(_rl_prompt(prompt_str)).strip()
                    self._in_prompt = False
                except EOFError:
                    print()
                    break
                except KeyboardInterrupt:
                    self._in_prompt = False
                    keys.trigger_cancel()
                    print(f"\n  {GRAY}(Çıkmak için /exit veya Ctrl+D){RESET}")
                    continue

                if not user_in:
                    continue

                low_in = user_in.lower().strip()
                daemon_st = STATUS_FILE.read_text(encoding="utf-8").strip() if STATUS_FILE.exists() else ""
                if self._awaiting_daemon_confirm or daemon_st == "onay bekliyor":
                    if low_in in ("e", "evet", "y", "yes", "onay", "tamam", "olur", "calistir", "çalıştır", "1"):
                        self._awaiting_daemon_confirm = False
                        CONFIRM_FILE.write_text("yes", encoding="utf-8")
                        keys.trigger_cancel()
                        continue
                    if low_in in ("h", "hayir", "hayır", "n", "no", "iptal", "vazgec", "vazgeç", "0"):
                        self._awaiting_daemon_confirm = False
                        CONFIRM_FILE.write_text("no", encoding="utf-8")
                        keys.trigger_cancel()
                        continue

                if user_in.startswith("/"):
                    try:
                        self._handle_slash(user_in)
                    except Exception as e:
                        log_.exception("slash komutu hatası")
                        print(f"\n  {RED}Komut hatası: {e}{RESET}\n")
                    continue

                low_in = user_in.lower()
                if low_in in (
                    "api bağla",
                    "api bagla",
                    "provider bağla",
                    "provider bagla",
                    "api çek",
                    "api cek",
                    "model çek",
                    "model cek",
                ):
                    self.run_api_wizard(first_time=False)
                    continue

                if low_in in ("model değiştir", "model degistir", "modeller", "model seç", "model sec"):
                    self.run_model_manager()
                    continue

                self._run_user_message(user_in)
        finally:
            self.running = False
            try:
                sys.stdout.write("\033[r")
                sys.stdout.flush()
            except Exception:
                pass
            if self.s.save_history:
                try:
                    readline.set_history_length(200)
                    readline.write_history_file(str(HISTORY_FILE))
                except Exception:
                    pass
            try:
                if TUI_PID_FILE.exists() and TUI_PID_FILE.read_text().strip() == str(os.getpid()):
                    TUI_PID_FILE.unlink(missing_ok=True)
            except Exception:
                pass
        return 0


MetroCodeTUI = MetroAssistantTUI
