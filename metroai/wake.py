"""Vosk tabanlı çoklu-konuşmacı uyandırma kelimesi algılama (AGC + Gramer + Fonetik)."""
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
    for a, b in (("ı", "i"), ("İ", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c"), ("â", "a")):
        s = s.replace(a, b)
    return "".join(ch for ch in s if ch.isalnum())


def _phonetic(s: str) -> str:
    """Farklı ses tonları ve aksanlar için fonetik sadeleştirme."""
    s = _fold(s)
    # d/t, b/p, g/k dönüşümleri ve sonda o/u/a/e yuvarlaması
    s = s.replace("medr", "metr").replace("netr", "metr").replace("betr", "metr").replace("petr", "metr")
    s = s.replace("mikr", "metr").replace("motr", "metr").replace("retr", "metr").replace("mert", "metr")
    if s.startswith("metr") and len(s) in (5, 6):
        return "metro"
    if s in ("meto", "medo", "meta", "metu", "metin", "metan"):
        return "metro"
    return s


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


def _agc_frame(frame: np.ndarray, target_rms: float = 2200.0) -> bytes:
    """Uzak veya kısık sesli konuşmacıları Vosk için otomatik yükseltir (AGC)."""
    if frame.size == 0:
        return b""
    f = frame.astype(np.float32)
    rms = float(np.sqrt(np.mean(f * f)))
    if 120.0 < rms < target_rms:
        gain = min(4.2, target_rms / max(rms, 1.0))
        f = np.clip(f * gain, -32768.0, 32767.0)
    return f.astype(np.int16).tobytes()


class WakeWord:
    def __init__(self, model_path: Path, keywords: list[str], samplerate: int = 16000):
        if vosk is None:
            raise RuntimeError("vosk kurulu değil: ./venv/bin/pip install vosk")
        if not Path(model_path).exists():
            raise RuntimeError(
                f"Vosk modeli bulunamadı: {model_path}\n"
                "İndirmek için: ./install.sh"
            )
        self.model = vosk.Model(str(model_path))
        self.samplerate = samplerate
        self.keywords = [_fold(k) for k in keywords if _fold(k)] or ["metro"]
        self._grammar_json = self._build_grammar(self.keywords)
        self.rec_grammar = self._new_grammar_rec()
        self.rec_free = self._new_free_rec()
        self.last_trigger = 0.0
        self.last_reset = time.monotonic()
        log_.info(
            "Uyandırma modeli yüklendi (%s) — kelimeler: %s",
            Path(model_path).name,
            ", ".join(keywords),
        )

    @staticmethod
    def _build_grammar(keywords: list[str]) -> str:
        phrases: list[str] = ["[unk]"]
        prefixes = ("", "hey ", "alo ", "selam ", "merhaba ", "hi ", "ey ")
        variants = set(keywords) | {"metro", "metre"}
        for kw in variants:
            for pre in prefixes:
                phrases.append(f"{pre}{kw}".strip())
        return json.dumps(sorted(set(phrases)), ensure_ascii=False)

    def _new_grammar_rec(self):
        try:
            r = vosk.KaldiRecognizer(self.model, self.samplerate, self._grammar_json)
            r.SetWords(False)
            return r
        except Exception:
            return None

    def _new_free_rec(self):
        r = vosk.KaldiRecognizer(self.model, self.samplerate)
        r.SetWords(False)
        return r

    def _match(self, text: str) -> bool:
        raw_tokens = [
            _fold(t)
            for t in text.replace("?", " ").replace(".", " ").replace(",", " ").split()
            if t.strip()
        ]
        tokens = [t for t in raw_tokens if t and t != "unk"]
        if not tokens:
            return False

        # Bitişik iki kelimeyi de birleştir ("met ro", "hey me tro" gibi bölünmeleri yakalar)
        joined = [tokens[i] + tokens[i + 1] for i in range(len(tokens) - 1)]
        all_candidates = tokens + joined

        for token in all_candidates:
            ptoken = _phonetic(token)
            for kw in self.keywords:
                if token.startswith(kw) or ptoken.startswith(kw):
                    return True
                if len(kw) >= 4 and (_lev(token, kw) <= 1 or _lev(ptoken, kw) <= 1):
                    return True
                if kw in token and len(token) <= len(kw) + 3:
                    return True
        return False

    def feed(self, frame: np.ndarray) -> bool:
        """Bir PCM çerçevesi besler; uyandırma kelimesi duyulursa True döner."""
        now = time.monotonic()
        # Konuşmacı i-vector adaptasyonunun tek kişiye kilitlenmemesi için 8 saniyede bir sıfırla
        if now - self.last_reset > 8.0:
            self.reset()

        pcm = _agc_frame(frame)

        # 1) Gramer tabanlı hızlı tanıyıcı (farklı ses tonlarına en duyarlı katman)
        if self.rec_grammar is not None:
            if self.rec_grammar.AcceptWaveform(pcm):
                g_text = json.loads(self.rec_grammar.Result()).get("text", "")
                if g_text and self._match(g_text):
                    return self._trigger()
                # Cümle bitti ama eşleşmedi -> i-vector'ü sıfırla ki yeni konuşmacıyı hemen tanısın
                self.rec_grammar = self._new_grammar_rec()
            else:
                g_part = json.loads(self.rec_grammar.PartialResult()).get("partial", "")
                if g_part and self._match(g_part):
                    return self._trigger()

        # 2) Serbest sözlük + fonetik eşleştirici
        if self.rec_free.AcceptWaveform(pcm):
            text = json.loads(self.rec_free.Result()).get("text", "")
            if text and self._match(text):
                return self._trigger()
            self.rec_free = self._new_free_rec()
            self.last_reset = now
            return False

        partial = json.loads(self.rec_free.PartialResult()).get("partial", "")
        if partial and self._match(partial):
            return self._trigger()
        return False

    def reset(self) -> None:
        """Tanıyıcıları ve konuşmacı adaptasyonunu sıfırlar."""
        self.rec_grammar = self._new_grammar_rec()
        self.rec_free = self._new_free_rec()
        self.last_reset = time.monotonic()

    def _trigger(self) -> bool:
        self.reset()
        now = time.monotonic()
        if now - self.last_trigger < 1.6:
            return False
        self.last_trigger = now
        return True

    def transcribe(self, wav_bytes: bytes) -> str:
        """Yerel (çevrimdışı) yedek STT."""
        rec = vosk.KaldiRecognizer(self.model, self.samplerate)
        rec.SetWords(False)
        rec.AcceptWaveform(wav_bytes)
        final = json.loads(rec.FinalResult())
        return (final.get("text") or "").strip()
