"""Metro Code — Claude Code & J.A.R.V.I.S. tarzı etkileşimli terminal arayüzü."""
from __future__ import annotations

import json
import os
import readline
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import __version__, audio, log, tools, tts
from .agent import Agent
from .llm import LLMChain
from .settings import ROOT, STATE_DIR, Settings, ensure_dirs, load, save, vosk_path

log_ = log.get()

EVENTS_FILE = STATE_DIR / "events.jsonl"
TUI_PID_FILE = STATE_DIR / "tui.pid"
STATUS_FILE = STATE_DIR / "status"
HISTORY_FILE = STATE_DIR / "chat_history"

# ANSI renk kodları (Claude Code & Jarvis paleti)
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
CYAN = "\033[38;5;81m"
ORANGE = "\033[38;5;209m"
GREEN = "\033[38;5;114m"
YELLOW = "\033[38;5;221m"
RED = "\033[38;5;203m"
GRAY = "\033[38;5;245m"
WHITE = "\033[38;5;255m"


# ---------- Olay (Event) veri yolu ----------
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
        # Dosya çok büyürse son 200 satırı tut
        if EVENTS_FILE.stat().st_size > 256 * 1024:
            lines = EVENTS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-150:]
            EVENTS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
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


# ---------- Pencere Yönetimi (Hey Metro -> Terminal Aç) ----------
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


def ensure_terminal_open(settings: Settings | None = None) -> bool:
    """Açık bir Metro Code penceresi yoksa yeni bir terminal penceresi açar."""
    if is_tui_running():
        return True

    s = settings or load()
    if not s.auto_open_terminal:
        return False

    metro_bin = str(ROOT / "metro")
    title = "Metro Code — J.A.R.V.I.S."

    candidates: list[tuple[str, list[str]]] = [
        ("kitty", ["kitty", "--title", title, "--app-id", "metro-code", metro_bin, "chat"]),
        ("konsole", ["konsole", "-p", f"tabtitle={title}", "-e", metro_bin, "chat"]),
        ("alacritty", ["alacritty", "--title", title, "--class", "metro-code", "-e", metro_bin, "chat"]),
        ("foot", ["foot", f"--title={title}", "--app-id=metro-code", metro_bin, "chat"]),
        ("ghostty", ["ghostty", f"--title={title}", "-e", metro_bin, "chat"]),
        ("wezterm", ["wezterm", "start", "--", metro_bin, "chat"]),
        ("xterm", ["xterm", "-T", title, "-e", metro_bin, "chat"]),
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
            log_.info("Metro Code terminali başlatıldı (%s)", name)
            return True
        except Exception as e:
            log_.warning("terminal başlatılamadı (%s): %s", name, e)
    return False


# ---------- Claude Code / Jarvis TUI Konsolu ----------
class TUICtx(tools.Ctx):
    def __init__(self, tui: "MetroCodeTUI"):
        self.tui = tui

    def say(self, text: str) -> None:
        if self.tui.speak_replies:
            tts.speak(text, self.tui.s.voice, self.tui.s.language, self.tui.s.max_reply_chars)

    def confirm(self, question: str) -> bool:
        try:
            ans = input(f"\n  {YELLOW}⚠ {question} (e/h): {RESET}").strip().lower()
            return ans.startswith(("e", "y", "o"))
        except (EOFError, KeyboardInterrupt):
            return False

    def sleep_minutes(self, minutes: float) -> None:
        self.tui.print_system(f"Metro {minutes:g} dakika sessize alındı.")


class MetroCodeTUI:
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

    # ---------- Görsel Yardımcılar ----------
    def _term_width(self) -> int:
        return max(56, min(92, shutil.get_terminal_size((80, 24)).columns - 2))

    def render_header(self) -> None:
        w = self._term_width()
        inner = w - 4
        preset = tts.resolve_voice(self.s.voice)
        ready = [p.id for p in self.s.providers if p.usable()]
        providers_str = ", ".join(ready) if ready else "yok"
        daemon_state = STATUS_FILE.read_text(encoding="utf-8").strip() if STATUS_FILE.exists() else "hazır"

        def pad(visible_len: int, text: str) -> str:
            spaces = max(0, inner - visible_len)
            return f"{CYAN}│{RESET} {text}{' ' * spaces} {CYAN}│{RESET}"

        title_vis = f"✻ Metro Code v{__version__}  •  J.A.R.V.I.S. Sesli & Yazılı Konsol"
        title_col = f"{ORANGE}{BOLD}✻ Metro Code{RESET} {GRAY}v{__version__}  •  J.A.R.V.I.S. Sesli & Yazılı Konsol{RESET}"

        voice_vis = f"Ses: {preset.alias} ({preset.gender})  •  Sağlayıcılar: {providers_str}"
        voice_col = (
            f"{GRAY}Ses:{RESET} {WHITE}{preset.alias}{RESET} {GRAY}({preset.gender})  •  "
            f"Sağlayıcılar:{RESET} {GREEN}{providers_str}{RESET}"
        )

        mode_str = "AÇIK" if self.speak_replies else "SADECE SESLİ KOMUTTA"
        status_vis = f"Durum: ● {daemon_state.upper()}  •  Sesli Okuma: {mode_str}"
        status_col = (
            f"{GRAY}Durum:{RESET} {GREEN}● {daemon_state.upper()}{RESET}  {GRAY}•  "
            f"Sesli Okuma:{RESET} {CYAN}{mode_str}{RESET}"
        )

        hint_vis = "İpucu: 'Hey Metro' diyerek konuş veya aşağıya yaz (/help komutlar)"
        hint_col = f"{DIM}{hint_vis}{RESET}"

        print(f"\n{CYAN}╭─{'─' * inner}─╮{RESET}")
        print(pad(len(title_vis), title_col))
        print(pad(len(voice_vis), voice_col))
        print(pad(len(status_vis), status_col))
        print(pad(len(hint_vis), hint_col))
        print(f"{CYAN}╰─{'─' * inner}─╯{RESET}\n")

    def _async_print(self, block: str) -> None:
        with self._print_lock:
            if self._in_prompt:
                sys.stdout.write("\r\033[K")
            sys.stdout.write(block + "\n")
            if self._in_prompt:
                sys.stdout.write(f"{ORANGE}{BOLD}❯{RESET} ")
                try:
                    sys.stdout.write(readline.get_line_buffer())
                except Exception:
                    pass
            sys.stdout.flush()

    def print_user_voice(self, text: str, source: str = "stt") -> None:
        self._async_print(
            f"{CYAN}{BOLD}🎙 Sen ({source}):{RESET} {WHITE}{text}{RESET}"
        )

    def print_assistant(self, text: str, model: str | None = None) -> None:
        tag = f" {GRAY}[{model}]{RESET}" if model else ""
        self._async_print(
            f"{ORANGE}{BOLD}⏺ Metro{RESET}{tag}\n  {WHITE}{text}{RESET}\n"
        )

    def print_system(self, text: str) -> None:
        self._async_print(f"  {GRAY}✻ {text}{RESET}")

    # ---------- Arka Plan Olay İzleyici (Daemon <-> TUI) ----------
    def _watch_events(self) -> None:
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        EVENTS_FILE.touch(exist_ok=True)

        # Pencere yeni açıldıysa son 8 saniyedeki uyandırma/ses olaylarını göster
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
                    time.sleep(0.12)
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
            self.print_system("Hey Metro algılandı — seni dinliyorum...")
        elif etype == "user_voice":
            text = ev.get("text", "")
            src = ev.get("source", "mikrofon")
            self.print_user_voice(text, src)
        elif etype == "thinking":
            self.print_system("Düşünüyor...")
        elif etype == "assistant":
            text = ev.get("text", "")
            model = ev.get("model")
            u = ev.get("user_text")
            if u and text:
                self.agent.history.append((u, text))
            self.print_assistant(text, model)

    # ---------- Slash Komutları ----------
    def _handle_slash(self, raw: str) -> bool:
        parts = raw.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit", "/q"):
            self.running = False
            return True

        if cmd == "/help":
            print(f"\n  {BOLD}Metro Code Komutları:{RESET}")
            print(f"  {CYAN}/voice{RESET}              Mevcut ses profillerini listele")
            print(f"  {CYAN}/voice <ad>{RESET}         Sesi değiştir ve test et (örn. {WHITE}/voice jarvis{RESET}, {WHITE}/voice brian{RESET})")
            print(f"  {CYAN}/speak{RESET}              Yazılı sohbette sesli okumayı aç/kapat")
            print(f"  {CYAN}/listen{RESET}             Mikrofondan hemen sesli komut dinle")
            print(f"  {CYAN}/model{RESET}              LLM model zinciri ve kota durumunu göster")
            print(f"  {CYAN}/sh <komut>{RESET}         Terminal komutu çalıştır")
            print(f"  {CYAN}/clear{RESET}              Ekranı ve sohbet geçmişini temizle")
            print(f"  {CYAN}/exit{RESET}               Metro Code penceresini kapat\n")
            return True

        if cmd == "/clear":
            self.agent.history.clear()
            os.system("clear")
            self.render_header()
            return True

        if cmd == "/speak":
            self.speak_replies = not self.speak_replies
            durum = "AÇIK (yazılı yanıtlar da seslendirilecek)" if self.speak_replies else "KAPALI (yalnızca sesli komutlar seslendirilir)"
            print(f"\n  {GREEN}✓{RESET} Sesli yanıt modu: {BOLD}{durum}{RESET}\n")
            return True

        if cmd == "/voice":
            if not arg:
                print(f"\n  {BOLD}Kullanılabilir Ses Profilleri (Aktif: {CYAN}{self.s.voice}{RESET}{BOLD}):{RESET}")
                for alias, gender, desc in tts.voices():
                    mark = f"{GREEN}●{RESET}" if alias == self.s.voice.lower() else " "
                    print(f"  {mark} {CYAN}{alias:<15}{RESET} {GRAY}{gender:<6}{RESET} {desc}")
                print(f"\n  Değiştirmek için: {WHITE}/voice jarvis{RESET} veya {WHITE}/voice brian{RESET}\n")
                return True
            preset = tts.resolve_voice(arg)
            self.s.voice = preset.alias
            save(self.s)
            print(f"\n  {GREEN}✓{RESET} Ses {BOLD}{preset.alias}{RESET} ({preset.desc}) olarak ayarlandı. Test ediliyor...")
            tts.speak("Merhaba patron, ben Metro. Yeni sesimle emrindeyim.", self.s.voice, self.s.language)
            print()
            return True

        if cmd in ("/model", "/models", "/status"):
            rows = self.chain.status_rows()
            print(f"\n  {BOLD}Model Zinciri:{RESET}")
            for row in rows:
                if not row["key"] and not row.get("keyless"):
                    st = f"{GRAY}anahtar yok{RESET}"
                elif row["cooldown_s"]:
                    st = f"{YELLOW}{row['cooldown_s'] // 60} dk beklemede{RESET}"
                else:
                    st = f"{GREEN}hazır{RESET}"
                print(f"    {CYAN}{row['provider']:<12}{RESET} {row['model']:<34} {st}")
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

        print(f"  {YELLOW}Bilinmeyen komut: {cmd}. Yardım için /help yaz.{RESET}\n")
        return True

    def _listen_once(self) -> None:
        from .stt import Stt
        from .wake import WakeWord

        try:
            print(f"\n  {CYAN}🎙 Mikrofon açılıyor, lütfen konuş...{RESET}")
            wake = WakeWord(vosk_path(self.s), self.s.wake_words, self.s.sample_rate)
            stt = Stt(self.s, local=wake)
            mic = audio.Mic(self.s.sample_rate, self.s.input_device)
            floor = mic.calibrate(0.5)
            if self.s.chime:
                audio.chime(self.s.sample_rate)
            samples = mic.record_command(floor, self.s.silence_sec, self.s.max_record_sec)
            mic.close()
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
            tts.speak(
                reply,
                self.s.voice,
                self.s.language,
                self.s.max_reply_chars,
                on_start=lambda: self.print_assistant(reply, self.chain.last_used),
            )
        except Exception as e:
            print(f"  {RED}Mikrofon hatası: {e}{RESET}\n")

    # ---------- Ana Döngü ----------
    def run(self) -> int:
        try:
            TUI_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass

        try:
            if HISTORY_FILE.exists():
                readline.read_history_file(str(HISTORY_FILE))
        except Exception:
            pass

        os.system("clear")
        self.render_header()

        watcher = threading.Thread(target=self._watch_events, daemon=True)
        watcher.start()

        try:
            while self.running:
                try:
                    self._in_prompt = True
                    user_in = input(f"{ORANGE}{BOLD}❯{RESET} ").strip()
                    self._in_prompt = False
                except EOFError:
                    print()
                    break
                except KeyboardInterrupt:
                    self._in_prompt = False
                    print(f"\n  {GRAY}(Çıkmak için /exit veya Ctrl+D){RESET}")
                    continue

                if not user_in:
                    continue

                if user_in.startswith("/"):
                    self._handle_slash(user_in)
                    continue

                # LLM / Ajan çağrısı
                sys.stdout.write(f"  {GRAY}✻ Düşünüyor...{RESET}\r")
                sys.stdout.flush()

                if self.speak_replies:
                    tts.prewarm()

                # Güncel ayarları/sesi senkronize tut
                self.s = load()
                self.agent.s = self.s

                reply = self.agent.handle(user_in)
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
        finally:
            self.running = False
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
