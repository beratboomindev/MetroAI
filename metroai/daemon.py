"""Metro arka plan servisi: dinle → uyan → anla → yanıtla."""
from __future__ import annotations

import json
import signal
import threading
import time
from datetime import datetime

from . import agent as agent_mod
from . import audio, keys, log, tools, tts, tui
from .llm import LLMChain
from .settings import CACHE_DIR, STATE_DIR, Settings, ensure_dirs, load, vosk_path
from .stt import Stt
from .wake import WakeWord

log_ = log.get()
STATUS_FILE = STATE_DIR / "status"
CONFIRM_FILE = STATE_DIR / "confirm_reply"

YES = ("evet", "onay", "tamam", "olur", "yap", "kabul", "tabii", "aynen", "dogru", "he", "calistir", "çalıştır")
NO = ("hayir", "hayır", "vazgec", "vazgeç", "iptal", "yok", "olmaz", "yapma", "dur")


class Runtime(tools.Ctx):
    def __init__(self, settings: Settings):
        self.s = settings
        self.tts_lock = threading.Lock()
        self.stop = False
        self.paused_until = 0.0
        self._was_paused = False
        self.state = "baslatiliyor"
        self.mic = audio.Mic(settings.sample_rate, settings.input_device)
        self.wake = WakeWord(vosk_path(settings), settings.wake_words, settings.sample_rate)
        self.stt = Stt(settings, local=self.wake)
        self.chain = LLMChain(settings)
        self.agent = agent_mod.Agent(settings, self.chain, self)
        self.keys = keys.get_watcher(settings.hotkey)
        self.noise_floor = 80.0

    # ---------- tools.Ctx ----------
    def say(self, text: str, on_start=None) -> None:
        if not text:
            if on_start:
                on_start()
            return
        with self.tts_lock:
            self._set_state("konusuyor")
            tts.speak(text, self.s.voice, self.s.language, self.s.max_reply_chars, on_start=on_start)
            self.mic.flush()
            self.wake.reset()
        self._set_state("uykuda" if time.time() < self.paused_until else "dinliyor")

    def _consume_screen_confirm(self) -> bool | None:
        if not CONFIRM_FILE.exists():
            return None
        try:
            val = CONFIRM_FILE.read_text(encoding="utf-8").strip().lower()
            CONFIRM_FILE.unlink(missing_ok=True)
            if val == "yes":
                return True
            if val == "no":
                return False
        except Exception:
            pass
        return None

    def confirm(self, question: str, detail: str = "") -> bool:
        try:
            CONFIRM_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        self._set_state("onay bekliyor")
        tui.ensure_terminal_open(self.s)
        tui.emit_event("confirm_request", origin="daemon", question=question, detail=detail)

        # Yalnızca kısa onay sorusunu sesli oku (komutun kendisini sesli okuma)
        self.say(question)
        self._set_state("onay bekliyor")

        screen_ans = self._consume_screen_confirm()
        if screen_ans is not None:
            tui.emit_event("confirm_resolved", origin="daemon", approved=screen_ans, via="ekran")
            return screen_ans

        samples = self.mic.record_command(
            noise_floor=self.noise_floor, silence_sec=0.75, max_sec=10, start_timeout=8
        )

        screen_ans = self._consume_screen_confirm()
        if screen_ans is not None:
            tui.emit_event("confirm_resolved", origin="daemon", approved=screen_ans, via="ekran")
            return screen_ans

        if samples is None:
            tui.emit_event("confirm_resolved", origin="daemon", approved=False, via="iptal")
            if self.mic.last_cancelled:
                audio.cancel_chime(self.s.sample_rate)
            else:
                self.say("Onay alamadım, iptal ediyorum.")
            return False

        text, _ = self.stt.transcribe(audio.to_wav_bytes(samples, self.s.sample_rate))
        low = text.lower()
        log_.info("onay yanıtı: %r", low)
        if any(w in low for w in NO):
            tui.emit_event("confirm_resolved", origin="daemon", approved=False, via="ses")
            self.say("Tamam, iptal.")
            return False
        if any(w in low for w in YES):
            tui.emit_event("confirm_resolved", origin="daemon", approved=True, via="ses")
            return True
        tui.emit_event("confirm_resolved", origin="daemon", approved=False, via="ses")
        self.say("Net anlayamadım, iptal ediyorum.")
        return False

    def sleep_minutes(self, minutes: float) -> None:
        self.paused_until = time.time() + max(0.5, minutes) * 60
        log_.info("%.1f dakika dinlenmeye geçti", minutes)
        self._set_state("uykuda")

    # ---------- durum ----------
    def _set_state(self, state: str) -> None:
        self.state = state
        try:
            STATUS_FILE.write_text(state, encoding="utf-8")
        except Exception:
            pass

    # ---------- ana döngü ----------
    def run(self) -> None:
        ensure_dirs()
        self._banner()
        self.mic.open()
        self.noise_floor = self.mic.calibrate(0.8)
        log_.info("ortam gürültü seviyesi: %.0f", self.noise_floor)
        self._set_state("hazır")
        if self.s.chime:
            audio.chime(self.s.sample_rate)
        self._notify("Metro hazır. 'Hey Metro' diyerek çağırabilirsin.")

        while not self.stop:
            if self.keys.consume_wake():
                self.paused_until = 0.0
                log_.info("uyandırma tetiklendi (kısayol tuşu: %s)", self.keys.hotkey)
                self._on_wake()
                continue

            if time.time() < self.paused_until:
                time.sleep(0.20)
                self._was_paused = True
                continue
            if self._was_paused:
                self._was_paused = False
                self._set_state("hazır")
                self.mic.flush()
                self.wake.reset()

            frame = self.mic.read()
            lvl = audio.rms(frame)
            if lvl < self.noise_floor:
                self.noise_floor = max(35.0, 0.92 * self.noise_floor + 0.08 * lvl)
            elif lvl < self.noise_floor * 1.4:
                self.noise_floor = min(220.0, 0.995 * self.noise_floor + 0.005 * lvl)

            if not (self.keys.consume_wake() or self.wake.feed(frame)):
                continue

            log_.info("uyandırma tetiklendi (ses veya kısayol tuşu)")
            self._on_wake()

        self.mic.close()
        self._set_state("kapali")
        log_.info("Metro kapatıldı.")

    def _on_wake(self) -> None:
        was_media_playing = False
        try:
            # Sesi veya ayarları terminalden değiştirilmiş olabilir, güncel tut
            try:
                latest = load()
                self.s = latest
                self.chain.s = latest
                self.agent.s = latest
                self.keys.set_hotkey(latest.hotkey)
            except Exception:
                pass

            # Hoparlörden gelen müziğin mikrofona yankı yapmasını önlemek için çalmayı geçici duraklat
            was_media_playing = tools.is_media_playing()
            if was_media_playing:
                tools.pause_media()

            # Hey Metro denildiğinde Metro Assistant terminal penceresini aç (açık değilse)
            self._set_state("dinliyor")
            tui.emit_event("wake", origin="daemon")
            tui.ensure_terminal_open(self.s)

            # İlk kez "Hey Metro" denildiğinde henüz API çekilmediyse/provider bağlanmadıysa uyar
            if not tui.is_api_onboarded(self.s):
                tui.emit_event("need_api", origin="daemon")
                self.say(
                    "Henüz hiçbir API çekmemişsin. Önce bir sağlayıcı bağlayalım. "
                    "Açılan Metro Assistant penceresinden hemen seçebilirsin."
                )
                if was_media_playing:
                    tools.resume_media()
                return

            if self.s.chime:
                audio.chime(self.s.sample_rate)

            if self.s.ack_phrase:
                self.say(self.s.ack_phrase)

            self.mic.flush()
            samples = self.mic.record_command(
                noise_floor=self.noise_floor,
                silence_sec=self.s.silence_sec,
                max_sec=self.s.max_record_sec,
                noise_multiplier=self.s.noise_multiplier,
            )
            if samples is None:
                if self.mic.last_cancelled:
                    audio.cancel_chime(self.s.sample_rate)
                    tui.emit_event("cancelled", origin="daemon")
                    log_.info("dinleme ESC ile iptal edildi")
                else:
                    log_.info("komut gelmedi")
                if was_media_playing:
                    tools.resume_media()
                self._set_state("hazır")
                return

            # LLM düşünürken TTS WebSocket bağlantısını arka planda önceden ısıt
            tts.prewarm()

            self._set_state("anliyor")
            text, source = self.stt.transcribe(audio.to_wav_bytes(samples, self.s.sample_rate))
            log_.info("duyulan (%s): %r", source, text)
            if not text:
                self.say("Seni duyamadım.")
                if was_media_playing:
                    tools.resume_media()
                return

            tui.emit_event("user_voice", origin="daemon", source=source, text=text)
            self._set_state("dusunuyor")
            tui.emit_event("thinking", origin="daemon")
            turns = tui.recent_turns(self.s.history_turns)
            if turns:
                self.agent.history.clear()
                for u, a in turns:
                    self.agent.history.append((u, a))
            reply = self.agent.handle(text, is_voice=True)
            log_.info("yanıt: %s", reply)

            low_text = text.lower()
            user_stopped_media = any(w in low_text for w in ("dur", "durdur", "kes", "kapat", "sus", "pause")) or any(
                w in reply.lower() for w in ("durdurdum", "kapattım", "durduruldu")
            )

            self.say(
                reply,
                on_start=lambda: tui.emit_event(
                    "assistant",
                    origin="daemon",
                    user_text=text,
                    text=reply,
                    model=self.chain.last_used,
                ),
            )
            if was_media_playing and not user_stopped_media:
                tools.resume_media()
        except Exception:
            log_.exception("uyandırma sırasında hata")
            self.say("Bir sorun oldu.")
            if was_media_playing:
                tools.resume_media()
        finally:
            self.mic.flush()
            self.wake.reset()
            if time.time() >= self.paused_until:
                self._set_state("hazır")

    # ---------- yardımcılar ----------
    def _banner(self) -> None:
        log_.info("=" * 52)
        log_.info("  %s hazır — CachyOS sesli asistan", self.s.name)
        log_.info("  Uyandırma: %s", " / ".join(f"hey {w}" for w in self.s.wake_words))
        log_.info("  Ses: %s  |  Dil: %s", self.s.voice, self.s.language)
        ready = [p.id for p in self.s.providers if p.usable()]
        log_.info("  Sağlayıcılar: %s", ", ".join(ready) or "YOK (./metro setup çalıştır)")
        log_.info("=" * 52)

    def _notify(self, text: str) -> None:
        try:
            import subprocess

            subprocess.run(["notify-send", "Metro", text], timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def serve(settings: Settings) -> None:
    rt = Runtime(settings)

    def _stop(signum, frame):
        rt.stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    rt.run()
