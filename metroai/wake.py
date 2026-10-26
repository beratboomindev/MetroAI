"""Vosk tabanlı uyandırma kelimesi algılama (tamamen yerel, hafif)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from . import log

log_ = log.get()

try:
    import vosk

    vosk.SetLogLevel(-1)
except Exception:  # pragma: no cover
    vosk = None  # type: ignore


def _fold(s: str) -> str:
    s = s.lower()
    for a, b in (("ı", "i"), ("İ", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c")):
        s = s.replace(a, b)
    return "".join(ch for ch in s if ch.isalnum())


def _lev(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2:
        return 9
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


class WakeWord:
    def __init__(self, model_path: Path, keywords: list[str], samplerate: int = 16000):
        if vosk is None:
            raise RuntimeError("vosk kurulu değil: ./venv/bin/pip install vosk")
        if not Path(model_path).exists():
            raise RuntimeError(
                f"Vosk modeli bulunamadı: {model_path}\n"
                "İndirmek için: ./metro setup"
            )
        self.model = vosk.Model(str(model_path))
        self.rec = vosk.KaldiRecognizer(self.model, samplerate)
        self.rec.SetWords(False)
        self.keywords = [_fold(k) for k in keywords]
        self.last_trigger = 0.0
        self.last_reset = time.monotonic()
        log_.info("Uyandırma modeli yüklendi (%s) — kelimeler: %s", Path(model_path).name, ", ".join(keywords))

    def _match(self, text: str) -> bool:
        for raw in text.replace("?", "").replace(".", "").replace(",", " ").split():
            token = _fold(raw)
            if not token:
                continue
            for kw in self.keywords:
                if token.startswith(kw):
                    return True
                if len(kw) >= 4 and _lev(token, kw) <= 1:
                    return True
        return False

    def feed(self, frame: np.ndarray) -> bool:
        """Bir PCM çerçevesi besler; uyandırma kelimesi duyulursa True döner."""
        now = time.monotonic()
        if now - self.last_reset > 20:
            self.reset()

        pcm = frame.astype(np.int16).tobytes()
        if self.rec.AcceptWaveform(pcm):
            text = json.loads(self.rec.Result()).get("text", "")
            self.last_reset = time.monotonic()
            if text and self._match(text):
                return self._trigger()
            return False

        partial = json.loads(self.rec.PartialResult()).get("partial", "")
        if partial and self._match(partial):
            return self._trigger()
        return False

    def reset(self) -> None:
        """Tanıyıcıyı sıfırlar (kendi sesimizi duymamak için)."""
        self.rec = vosk.KaldiRecognizer(self.model, 16000)
        self.rec.SetWords(False)
        self.last_reset = time.monotonic()

    def _trigger(self) -> bool:
        # Eşleşen cümleyi her durumda at, yoksa artığı bir sonraki cümleye taşınır.
        self.reset()
        now = time.monotonic()
        if now - self.last_trigger < 2.0:
            return False
        self.last_trigger = now
        return True

    def transcribe(self, wav_bytes: bytes) -> str:
        """Yerel (çevrimdışı) yedek STT."""
        rec = vosk.KaldiRecognizer(self.model, 16000)
        rec.SetWords(False)
        rec.AcceptWaveform(wav_bytes)
        final = json.loads(rec.FinalResult())
        return (final.get("text") or "").strip()
