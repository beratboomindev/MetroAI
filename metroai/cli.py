"""Metro komut satırı: run / setup / check / ask / say / listen / service / doctor."""
from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from . import __version__, audio, log, tools, tts
from .agent import Agent
from .settings import (
    CACHE_DIR,
    CONFIG_PATH,
    DEFAULT_PROVIDERS,
    LOG_DIR,
    MODELS_DIR,
    ROOT,
    STATE_DIR,
    Provider,
    Settings,
    ensure_dirs,
    load,
    save,
    vosk_path,
)
from .llm import LLMChain

VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-tr-0.3.zip"
SERVICE = "metro.service"
UNIT_PATH = Path.home() / ".config/systemd/user" / SERVICE


# ---------------------------------------------------------------- yardımcılar
def _ask(prompt: str, default: str = "") -> str:
    try:
        val = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    except EOFError:
        return default
    return val or default


def _ask_secret(prompt: str) -> str:
    try:
        return getpass.getpass(prompt + ": ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}")


def _bad(msg: str) -> None:
    print(f"  \033[31m✗\033[0m {msg}")


def _info(msg: str) -> None:
    print(f"  \033[36m•\033[0m {msg}")


# ---------------------------------------------------------------- setup
def cmd_setup(args) -> int:
    ensure_dirs()
    s = load() if CONFIG_PATH.exists() else Settings(providers=[Provider(**p) for p in DEFAULT_PROVIDERS])

    print("\n\033[1mMetro kurulumu\033[0m — hepsi ücretsiz, kredi kartı yok.\n")
    print("Aşağıdaki servislerden en az BİRİNİN ücretsiz anahtarını alman yeterli.")
    print("Bir modelin kotası dolunca Metro otomatik olarak diğerine geçer.\n")
    print("  Groq       → https://console.groq.com/keys        (en hızlı, whisper dahil)")
    print("  Gemini     → https://aistudio.google.com/apikey   (geniş ücretsiz kota)")
    print("  OpenRouter → https://openrouter.ai/keys           (onlarca :free model)")
    print("  Cerebras   → https://cloud.cerebras.ai            (ücretsiz katman)")
    print("  Mistral    → https://console.mistral.ai/api-keys  (ücretsiz katman)\n")

    for p in s.providers:
        if p.keyless:
            _info(f"{p.id}: anahtarsız çalışıyor, atlandı")
            continue
        existing = p.api_key or ""
        if args.groq and p.id == "groq":
            p.api_key = args.groq
            continue
        if args.gemini and p.id == "gemini":
            p.api_key = args.gemini
            continue
        if args.openrouter and p.id == "openrouter":
            p.api_key = args.openrouter
            continue
        label = f"{p.id} API anahtarı"
        if existing:
            label += " (mevcut, boş bırak = koru)"
        key = _ask_secret(label)
        if key:
            p.api_key = key

    if not args.no_test:
        if _ask("\nAnahtarları şimdi test edeyim mi? (E/h)", "E").lower().startswith(("e", "y")):
            save(s)
            _test_providers(s)

    print("\nSes ve uyandırma ayarları:")
    if args.keys_only:
        save(s)
        print(f"\n\033[32mAnahtarlar kaydedildi:\033[0m {CONFIG_PATH}")
        return 0

    voice_choice = _ask(
        "Ses [1=metro (tok erkek - önerilen), 2=brian (modern erkek), 3=seraphina (doğal kadın), 4=emel (klasik kadın), 5=değiştirme]",
        "1",
    )
    if voice_choice == "1":
        s.voice = "metro"
    elif voice_choice == "2":
        s.voice = "brian"
    elif voice_choice == "3":
        s.voice = "seraphina"
    elif voice_choice == "4":
        s.voice = "emel"

    words = _ask("Uyandırma kelimesi", ", ".join(s.wake_words))
    s.wake_words = [w.strip().lower() for w in words.split(",") if w.strip()] or ["metro"]

    s.ack_phrase = _ask("Uyandıktan sonra kısa onay cümlesi (boş = sadece çan sesi)", s.ack_phrase)

    if not vosk_path(s).exists():
        print("\nTürkçe uyandırma modeli indiriliyor (~50 MB)...")
        ok = _download_vosk(MODELS_DIR)
        if ok:
            _ok("Model hazır: models/vosk-model-small-tr-0.3")
        else:
            _bad("Model indirilemedi. Daha sonra `./metro setup` ile tekrar dene.")
    else:
        _ok(f"Model mevcut: {vosk_path(s).name}")

    save(s)
    print(f"\n\033[32mAyarlar kaydedildi:\033[0m {CONFIG_PATH}")
    print("\nBaşlatmak için:")
    print("  ./metro service install    # arka planda, açılışta otomatik")
    print("  ./metro                    # terminalde ön planda test")
    return 0


def _download_vosk(dest: Path) -> bool:
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / "vosk-model-small-tr-0.3"
    if target.exists():
        return True
    zip_path = dest / "tr-model.zip"
    try:
        with urllib.request.urlopen(VOSK_URL, timeout=60) as r, zip_path.open("wb") as f:
            total = int(r.headers.get("Content-Length", 0))
            done = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    print(f"\r  %{pct}", end="", flush=True)
        print()
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(dest)
        zip_path.unlink(missing_ok=True)
        return target.exists()
    except Exception as e:
        print(f"\n  indirme hatası: {e}")
        zip_path.unlink(missing_ok=True)
        return False


# ---------------------------------------------------------------- check
def _test_providers(s: Settings) -> None:
    chain = LLMChain(s)
    print()
    any_ok = False
    for p in s.providers:
        if not p.usable():
            _info(f"{p.id}: anahtar yok, atlandı")
            continue
        models = p.models[:1] if p.models else []
        if not models:
            model = (chain._discover(p) or ["?"])[0]
        else:
            model = models[0]
        try:
            reply = chain._call(p, model, [{"role": "user", "content": "Sadece OK yaz."}], 10, 0.0)
            _ok(f"{p.id} → {model} çalışıyor ({reply[:40]!r})")
            any_ok = True
        except Exception as e:
            reason = getattr(e, "reason", str(e))
            _bad(f"{p.id} → {model}: {reason}")
    print()
    if any_ok:
        print("  En az bir sağlayıcı hazır. Metro kullanıma uygun.")
    else:
        print("  Hiçbir sağlayıcı çalışmadı. Anahtarları kontrol et.")


def cmd_check(args) -> int:
    s = load()
    print(f"\nMetro {__version__} — yapılandırma: {CONFIG_PATH}\n")
    print("Sağlayıcılar:")
    _test_providers(s)
    return 0


# ---------------------------------------------------------------- CLI modları
class CLICtx(tools.Ctx):
    def __init__(self, speak: bool = False, s: Settings | None = None):
        self.speak = speak
        self.s = s

    def say(self, text: str) -> None:
        print(text)
        if self.speak and self.s:
            tts.speak(text, self.s.voice, self.s.language)

    def confirm(self, question: str) -> bool:
        return _ask(f"{question} (e/h)", "h").lower().startswith(("e", "y"))

    def sleep_minutes(self, minutes: float) -> None:
        print(f"[{minutes:g} dk uyku]")


def cmd_ask(args) -> int:
    s = load()
    chain = LLMChain(s)
    agent = Agent(s, chain, CLICtx(speak=args.speak, s=s))
    text = " ".join(args.text)
    reply = agent.handle(text)
    print(f"\n{reply}\n")
    return 0


def cmd_say(args) -> int:
    s = load()
    tts.speak(" ".join(args.text), s.voice, s.language, s.max_reply_chars)
    return 0


def cmd_listen(args) -> int:
    s = load()
    from .stt import Stt
    from .wake import WakeWord

    wake = WakeWord(vosk_path(s), s.wake_words, s.sample_rate)
    stt = Stt(s, local=wake)
    mic = audio.Mic(s.sample_rate, s.input_device)
    print("Ortam ölçülüyor...")
    floor = mic.calibrate(0.8)
    print(f"Gürültü seviyesi: {floor:.0f} — konuş (bitirmek için sus).")
    samples = mic.record_command(floor, s.silence_sec, args.max_sec)
    mic.close()
    if samples is None:
        print("Ses algılanmadı.")
        return 1
    dur = len(samples) / s.sample_rate
    print(f"{dur:.1f} sn kaydedildi, çevriliyor...")
    text, src = stt.transcribe(audio.to_wav_bytes(samples, s.sample_rate))
    print(f"\n[{src}] {text or '(boş)'}")
    return 0 if text else 1


def cmd_devices(args) -> int:
    import sounddevice as sd

    print("Giriş cihazları:")
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            default = " (varsayılan)" if idx == sd.default.device[0] else ""
            print(f"  {idx}: {dev['name']}{default}")
    print("\nconfig.toml → [audio] input_device = <numara veya isim parçası>")
    return 0


def cmd_voices(args) -> int:
    s = load()
    if getattr(args, "set_voice", None):
        preset = tts.resolve_voice(args.set_voice)
        s.voice = preset.alias
        save(s)
        _ok(f"Varsayılan ses '{preset.alias}' ({preset.desc}) olarak kaydedildi.")
        tts.speak("Merhaba, ben Metro. Yeni sesimle hazırım.", s.voice, s.language)
        return 0

    print(f"\nMetro ses profilleri (aktif: \033[36m{s.voice}\033[0m):\n")
    try:
        for alias, gender, desc in tts.voices(args.lang):
            mark = "\033[32m●\033[0m" if alias == s.voice.lower() else " "
            print(f"  {mark} \033[1m{alias:<15}\033[0m {gender:<7} {desc}")
        print("\nSesi değiştirmek ve test etmek için:")
        print("  metro voices --set metro")
        print("  metro voices --set brian")
        print("  metro voices --set seraphina")
    except Exception as e:
        print(f"  liste alınamadı: {e}")
    return 0


def cmd_chat(args) -> int:
    from . import tui

    if getattr(args, "window", False):
        ok = tui.ensure_terminal_open(load())
        return 0 if ok else 1
    app = tui.MetroAssistantTUI(speak_replies=getattr(args, "speak", False))
    return app.run()


# ---------------------------------------------------------------- servis
def _venv_python() -> str:
    p = ROOT / "venv" / "bin" / "python"
    return str(p if p.exists() else sys.executable)


def _unit_text() -> str:
    return f"""[Unit]
Description=Metro sesli asistan
After=pipewire.service pipewire-pulse.service graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
WorkingDirectory={ROOT}
ExecStart={_venv_python()} -m metroai run
Restart=always
RestartSec=4
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


def _systemctl(*args: str) -> int:
    return subprocess.run(["systemctl", "--user", *args]).returncode


def cmd_service(args) -> int:
    action = args.action
    if action == "install":
        UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        UNIT_PATH.write_text(_unit_text(), encoding="utf-8")
        _systemctl("daemon-reload")
        _systemctl("enable", SERVICE)
        _systemctl("restart", SERVICE)
        print(f"\nServis kuruldu ve başlatıldı: {UNIT_PATH}")
        print("Durum:  ./metro status")
        print("Loglar: ./metro service logs")
        return 0
    if action == "uninstall":
        _systemctl("disable", "--now", SERVICE)
        UNIT_PATH.unlink(missing_ok=True)
        _systemctl("daemon-reload")
        print("Servis kaldırıldı.")
        return 0
    if action == "logs":
        cmd = ["journalctl", "--user", "-u", SERVICE, "-n", "80"]
        if args.follow:
            cmd.append("-f")
        return subprocess.run(cmd).returncode
    rc = _systemctl(action, SERVICE)
    print(f"systemctl --user {action} {SERVICE} → {rc}")
    return rc


# ---------------------------------------------------------------- durum / doktor
def cmd_status(args) -> int:
    s = load()
    state_file = STATE_DIR / "status"
    print(f"\nMetro {__version__}")
    if state_file.exists():
        print(f"  Durum dosyası : {state_file.read_text().strip()}")
    active = subprocess.run(
        ["systemctl", "--user", "is-active", SERVICE], capture_output=True, text=True
    ).stdout.strip()
    print(f"  Servis        : {active}")
    ready = [p.id for p in s.providers if p.usable()]
    print(f"  Sağlayıcılar  : {', '.join(ready) or 'yok'}")
    chain = LLMChain(s)
    rows = chain.status_rows()
    if rows:
        print("\n  Model zinciri:")
        for row in rows:
            if not row["key"] and not row.get("keyless"):
                mark = "anahtar yok"
            elif row["cooldown_s"]:
                mark = f"{row['cooldown_s'] // 60} dk beklemede"
            else:
                mark = "hazır"
            print(f"    {row['provider']:<12} {row['model']:<34} {mark}")
    return 0


def cmd_doctor(args) -> int:
    s = load()
    fails = 0
    print(f"\n\033[1mMetro doktor\033[0m — {__version__}\n")

    print("Ortam:")
    _ok(f"Python {sys.version.split()[0]}")
    for mod in ("requests", "sounddevice", "numpy", "vosk", "edge_tts"):
        try:
            __import__(mod)
            _ok(f"python paketi: {mod}")
        except Exception:
            _bad(f"python paketi eksik: {mod}")
            fails += 1

    print("\nYapılandırma:")
    if CONFIG_PATH.exists():
        _ok(f"{CONFIG_PATH}")
    else:
        _bad("config.toml yok — ./metro setup")
        fails += 1
    ready = [p.id for p in s.providers if p.usable()]
    if ready:
        _ok(f"hazır sağlayıcı: {', '.join(ready)}")
    else:
        _bad("hiçbir sağlayıcıda anahtar yok")
        fails += 1

    print("\nSes:")
    model = vosk_path(s)
    if model.exists():
        _ok(f"uyandırma modeli: {model.name}")
    else:
        _bad("uyandırma modeli yok — ./metro setup")
        fails += 1
    try:
        mic = audio.Mic(s.sample_rate, s.input_device)
        floor = mic.calibrate(0.6)
        _ok(f"mikrofon çalışıyor (gürültü {floor:.0f})")
        mic.close()
    except Exception as e:
        _bad(f"mikrofon: {e}")
        fails += 1
    try:
        path = tts.synthesize("Metro test", s.voice, s.language)
        if path:
            _ok(f"seslendirme: {path.name}")
        else:
            _bad("seslendirme üretilemedi")
            fails += 1
    except Exception as e:
        _bad(f"seslendirme: {e}")
        fails += 1

    print("\nServis:")
    if UNIT_PATH.exists():
        _ok(f"{UNIT_PATH}")
    else:
        _info("systemd servisi kurulu değil (./metro service install)")

    print()
    if fails:
        print(f"\033[31m{fails} sorun bulundu.\033[0m")
        return 1
    print("\033[32mHer şey yolunda.\033[0m")
    return 0


# ---------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="metro", description="Metro — her zaman dinleyen ücretsiz sesli asistan")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="arka plan servisi olarak çalıştır")
    r.set_defaults(func=lambda a: _run(a))

    st = sub.add_parser("setup", help="anahtarları ve ses ayarlarını yap")
    st.add_argument("--groq")
    st.add_argument("--gemini")
    st.add_argument("--openrouter")
    st.add_argument("--keys-only", action="store_true")
    st.add_argument("--no-test", action="store_true")
    st.set_defaults(func=cmd_setup)

    c = sub.add_parser("check", help="sağlayıcıları test et")
    c.set_defaults(func=cmd_check)

    a = sub.add_parser("ask", help="metin olarak soru sor")
    a.add_argument("text", nargs="+")
    a.add_argument("-s", "--speak", action="store_true")
    a.set_defaults(func=cmd_ask)

    sy = sub.add_parser("say", help="metni seslendir")
    sy.add_argument("text", nargs="+")
    sy.set_defaults(func=cmd_say)

    li = sub.add_parser("listen", help="mikrofonu test et ve yazıya çevir")
    li.add_argument("--max-sec", type=float, default=12)
    li.set_defaults(func=cmd_listen)

    d = sub.add_parser("devices", help="mikrofonları listele")
    d.set_defaults(func=cmd_devices)

    v = sub.add_parser("voices", help="TTS seslerini listele veya değiştir")
    v.add_argument("--lang", default="tr")
    v.add_argument("--set", dest="set_voice", metavar="SES", help="aktif sesi değiştir ve test et (örn. metro, brian, seraphina)")
    v.set_defaults(func=cmd_voices)

    ch = sub.add_parser("chat", aliases=["assistant", "tui"], help="Metro Assistant etkileşimli sohbet konsolunu aç")
    ch.add_argument("-s", "--speak", action="store_true", help="yazılı yanıtları da seslendir")
    ch.add_argument("-w", "--window", action="store_true", help="yeni bir terminal penceresinde aç")
    ch.set_defaults(func=cmd_chat)

    sv = sub.add_parser("service", help="systemd servisini yönet")
    sv.add_argument("action", choices=["install", "uninstall", "start", "stop", "restart", "status", "enable", "disable", "logs"])
    sv.add_argument("-f", "--follow", action="store_true")
    sv.set_defaults(func=cmd_service)

    s2 = sub.add_parser("status", help="durum ve model zinciri")
    s2.set_defaults(func=cmd_status)

    doc = sub.add_parser("doctor", help="kurulumu kontrol et")
    doc.set_defaults(func=cmd_doctor)

    return p


def _run(args) -> int:
    from .daemon import serve

    s = load()
    if not CONFIG_PATH.exists():
        print("Önce kurulum yap: ./metro setup")
        return 1
    if not any(p.usable() for p in s.providers):
        print("Hiçbir sağlayıcı anahtarı yok. Önce: ./metro setup")
        return 1
    serve(s)
    return 0


def main(argv: list[str] | None = None) -> int:
    ensure_dirs()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        args.func = lambda a: _run(a)
        args.verbose = getattr(args, "verbose", False)

    logfile = LOG_DIR / "metro.log"
    if logfile.exists() and logfile.stat().st_size > 5 * 1024 * 1024:
        logfile.replace(LOG_DIR / "metro.log.1")
    log.setup(verbose=getattr(args, "verbose", False), logfile=logfile)

    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\nGörüşürüz.")
        return 130
    except Exception as e:
        log.get().exception("beklenmeyen hata")
        print(f"Hata: {e}")
        return 1
