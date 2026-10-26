"""Metro Assistant — Etkileşimli Sesli ve Yazılı Terminal Arayüzü."""
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
ONBOARDED_FILE = STATE_DIR / "api_onboarded"

PROVIDER_META: dict[str, tuple[str, str]] = {
    "opencode": ("OpenCode Ücretsiz Modeller (Zen / CLI)", "https://opencode.ai/zen"),
    "groq": ("Groq (En hızlı LLM + Whisper STT)", "https://console.groq.com/keys"),
    "gemini": ("Google Gemini 2.5 (Geniş ücretsiz kota + ses)", "https://aistudio.google.com/apikey"),
    "openrouter": ("OpenRouter (Onlarca :free model)", "https://openrouter.ai/keys"),
    "cerebras": ("Cerebras (Ultra hızlı Llama)", "https://cloud.cerebras.ai"),
    "mistral": ("Mistral AI (Ücretsiz katman)", "https://console.mistral.ai/api-keys"),
}


def is_api_onboarded(s: Settings | None = None) -> bool:
    """Kullanıcı en az bir kez API/provider seçimi yaptı mı veya anahtar girdi mi?"""
    if ONBOARDED_FILE.exists():
        return True
    s = s or load()
    return any(bool(p.key()) for p in s.providers if not p.keyless)


def mark_api_onboarded() -> None:
    ensure_dirs()
    try:
        ONBOARDED_FILE.write_text(datetime.now().isoformat(), encoding="utf-8")
    except Exception:
        pass

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
    """Açık bir Metro Assistant penceresi yoksa yeni bir terminal penceresi açar."""
    if is_tui_running():
        return True

    s = settings or load()
    if not s.auto_open_terminal:
        return False

    metro_bin = str(ROOT / "metro")
    title = "Metro Assistant"

    candidates: list[tuple[str, list[str]]] = [
        ("kitty", ["kitty", "--title", title, "--app-id", "metro-assistant", metro_bin, "chat"]),
        ("konsole", ["konsole", "-p", f"tabtitle={title}", "-e", metro_bin, "chat"]),
        ("alacritty", ["alacritty", "--title", title, "--class", "metro-assistant", "-e", metro_bin, "chat"]),
        ("foot", ["foot", f"--title={title}", "--app-id=metro-assistant", metro_bin, "chat"]),
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

    def confirm(self, question: str) -> bool:
        try:
            ans = input(f"\n  {YELLOW}⚠ {question} (e/h): {RESET}").strip().lower()
            return ans.startswith(("e", "y", "o"))
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

    # ---------- Görsel Yardımcılar ----------
    def _term_width(self) -> int:
        return max(60, min(90, shutil.get_terminal_size((80, 24)).columns - 2))

    def render_header(self) -> None:
        w = self._term_width()
        inner = w - 4
        preset = tts.resolve_voice(self.s.voice)
        ready = [p.id for p in self.s.providers if p.usable()]
        providers_str = ", ".join(ready) if ready else "yok"
        daemon_state = STATUS_FILE.read_text(encoding="utf-8").strip() if STATUS_FILE.exists() else "hazır"

        def pad(visible_len: int, text: str) -> str:
            spaces = max(0, inner - visible_len)
            return f"{GRAY}│{RESET} {text}{' ' * spaces} {GRAY}│{RESET}"

        left_vis = f"◆ METRO ASSISTANT  v{__version__}"
        right_vis = f"● {daemon_state.upper()}"
        gap = max(2, inner - len(left_vis) - len(right_vis))
        top_line = (
            f"{CYAN}{BOLD}◆ METRO ASSISTANT{RESET}  {GRAY}v{__version__}{RESET}"
            f"{' ' * gap}{GREEN}{BOLD}● {daemon_state.upper()}{RESET}"
        )

        sep_line = f"{GRAY}{'─' * inner}{RESET}"

        mode_str = "Açık" if self.speak_replies else "Yalnızca Sesli Komutta"
        info1_vis = f"Ses Profili : {preset.alias} ({preset.gender})   •   Sesli Yanıt : {mode_str}"
        info1_col = (
            f"{GRAY}Ses Profili :{RESET} {WHITE}{preset.alias}{RESET} {GRAY}({preset.gender})   •   "
            f"Sesli Yanıt :{RESET} {CYAN}{mode_str}{RESET}"
        )

        info2_vis = f"Sağlayıcılar: {providers_str}"
        info2_col = f"{GRAY}Sağlayıcılar:{RESET} {GREEN}{providers_str}{RESET}"

        hint_vis = "Komutlar    : /api (sağlayıcı bağla) • /voice (ses) • /listen • /help"
        hint_col = (
            f"{GRAY}Komutlar    :{RESET} {WHITE}/api{RESET} {GRAY}(sağlayıcı bağla) •{RESET} "
            f"{WHITE}/voice{RESET} {GRAY}(ses) •{RESET} {WHITE}/listen{RESET} {GRAY}•{RESET} {WHITE}/help{RESET}"
        )

        print(f"\n{GRAY}╭─{'─' * inner}─╮{RESET}")
        print(pad(len(left_vis) + gap + len(right_vis), top_line))
        print(pad(inner, sep_line))
        print(pad(len(info1_vis), info1_col))
        print(pad(len(info2_vis), info2_col))
        print(pad(len(hint_vis), hint_col))
        print(f"{GRAY}╰─{'─' * inner}─╯{RESET}\n")

    def _async_print(self, block: str) -> None:
        with self._print_lock:
            if self._in_prompt:
                sys.stdout.write("\r\033[K")
            sys.stdout.write(block + "\n")
            if self._in_prompt:
                sys.stdout.write(f"{CYAN}{BOLD}metro ❯{RESET} ")
                try:
                    sys.stdout.write(readline.get_line_buffer())
                except Exception:
                    pass
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
        elif etype == "need_api":
            self.print_system(
                "Hiç API çekmemişsin! Aşağıya /api yazarak hemen provider bağlayabilir ve modelleri çekebilirsin."
            )
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
        print()

    def run_api_wizard(self, first_time: bool = False) -> None:
        self.s = load()
        self.chain = LLMChain(self.s)
        providers = [p for p in self.s.providers if p.id in PROVIDER_META]

        if first_time:
            print(f"  {YELLOW}{BOLD}⚡ Hiç API çekmemişsin! Önce bir provider bağlayıp modelleri çekelim.{RESET}")
            print(f"  {GRAY}(OpenCode ücretsiz modellerini tek tuşla çekebilir veya Groq/Gemini/OpenRouter anahtarı girebilirsin){RESET}\n")

        while True:
            print(f"  {BOLD}🔌 Sağlayıcı (Provider) & API Bağlama Merkezi:{RESET}")
            for idx, p in enumerate(providers, 1):
                title, _ = PROVIDER_META.get(p.id, (p.id, ""))
                if p.key():
                    status = f"{GREEN}● Anahtar Bağlı ({len(p.models)} model){RESET}"
                elif p.keyless and p.id == "opencode":
                    status = f"{CYAN}● CLI Hazır ({len(p.models)} ücretsiz model){RESET}"
                else:
                    status = f"{GRAY}○ Bağlı Değil{RESET}"
                print(f"    {CYAN}{idx}){RESET} {BOLD}{p.id:<12}{RESET} {status:<42} {GRAY}{title}{RESET}")

            print(f"    {CYAN}p){RESET} {WHITE}Tüm bağlı sağlayıcılardan güncel modelleri API'den çek (Pull){RESET}")
            print(f"    {CYAN}0){RESET} {GRAY}Tamamla ve sohbete başla{RESET}\n")

            try:
                choice = input(f"  {ORANGE}{BOLD}Seçimin (1-{len(providers)} / p / 0) [1]:{RESET} ").strip().lower() or "1"
            except (EOFError, KeyboardInterrupt):
                print()
                mark_api_onboarded()
                break

            if choice in ("0", "q", "exit", "gec", "tamam"):
                mark_api_onboarded()
                print(f"\n  {GREEN}✓{RESET} Hazır! Artık 'Hey Metro' diyerek veya aşağıdan yazarak konuşabilirsin.\n")
                break

            if choice == "p":
                self._pull_provider_models()
                break

            if not choice.isdigit() or not (1 <= int(choice) <= len(providers)):
                print(f"  {YELLOW}Lütfen listeden geçerli bir numara seç.{RESET}\n")
                continue

            p = providers[int(choice) - 1]
            title, url = PROVIDER_META.get(p.id, (p.id, ""))
            print(f"\n  {BOLD}→ {title}{RESET} ({CYAN}{url}{RESET})")

            if p.id == "opencode":
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
                    for m in models:
                        print(f"    • {WHITE}{m}{RESET}")
                save(self.s)
                mark_api_onboarded()
                print(f"\n  {GREEN}✓ OpenCode modelleri bağlandı ve birinci önceliğe alındı!{RESET}\n")
                break

            # Diğer sağlayıcılar (Groq, Gemini, OpenRouter, Cerebras, Mistral)
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
                break
            except Exception as e:
                reason = getattr(e, "reason", str(e))
                print(f"  {RED}✗ Test başarısız ({reason}). Anahtarı kontrol edip tekrar deneyebilirsin.{RESET}\n")

    # ---------- Slash Komutları ----------
    def _handle_slash(self, raw: str) -> bool:
        parts = raw.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit", "/q"):
            self.running = False
            return True

        if cmd == "/help":
            print(f"\n  {BOLD}Metro Assistant Komutları:{RESET}")
            print(f"  {CYAN}/api{RESET}                Sağlayıcı (Provider) bağla ve API modellerini çek")
            print(f"  {CYAN}/pull{RESET}               Bağlı sağlayıcılardan güncel model listesini çek")
            print(f"  {CYAN}/voice{RESET}              Mevcut ses profillerini listele")
            print(f"  {CYAN}/voice <ad>{RESET}         Sesi değiştir ve test et (örn. {WHITE}/voice metro{RESET}, {WHITE}/voice brian{RESET})")
            print(f"  {CYAN}/speak{RESET}              Yazılı sohbette sesli okumayı aç/kapat")
            print(f"  {CYAN}/listen{RESET}             Mikrofondan hemen sesli komut dinle")
            print(f"  {CYAN}/model{RESET}              LLM model zinciri ve kota durumunu göster")
            print(f"  {CYAN}/sh <komut>{RESET}         Terminal komutu çalıştır")
            print(f"  {CYAN}/clear{RESET}              Ekranı ve sohbet geçmişini temizle")
            print(f"  {CYAN}/exit{RESET}               Metro Assistant penceresini kapat\n")
            return True

        if cmd in ("/api", "/provider", "/providers", "/connect", "/setup"):
            self.run_api_wizard(first_time=False)
            return True

        if cmd == "/pull":
            self._pull_provider_models(arg or None)
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
                print(f"\n  Değiştirmek için: {WHITE}/voice metro{RESET} veya {WHITE}/voice brian{RESET}\n")
                return True
            preset = tts.resolve_voice(arg)
            self.s.voice = preset.alias
            save(self.s)
            print(f"\n  {GREEN}✓{RESET} Ses {BOLD}{preset.alias}{RESET} ({preset.desc}) olarak ayarlandı. Test ediliyor...")
            tts.speak("Merhaba, ben Metro. Yeni sesimle hazırım.", self.s.voice, self.s.language)
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

        # İlk açılışta henüz hiçbir API çekilmediyse / provider bağlanmadıysa otomatik sor
        if not is_api_onboarded(self.s):
            self.run_api_wizard(first_time=True)

        watcher = threading.Thread(target=self._watch_events, daemon=True)
        watcher.start()

        try:
            while self.running:
                try:
                    self._in_prompt = True
                    user_in = input(f"{CYAN}{BOLD}metro ❯{RESET} ").strip()
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

                if user_in.lower() in (
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


MetroCodeTUI = MetroAssistantTUI
