"""Seslendirme: Ön-ısıtmalı (Pre-warmed WebSocket) + Canlı Streaming Multilingual Neural TTS."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import socket
import subprocess
import threading
import time
import urllib.parse
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import aiohttp
import requests

from . import audio, log
from .settings import CACHE_DIR, load

log_ = log.get()
TTS_DIR = CACHE_DIR / "tts"


@dataclass(frozen=True)
class VoicePreset:
    alias: str
    voice_id: str
    rate: str
    pitch: str
    gender: str
    desc: str


VOICE_PRESETS: dict[str, VoicePreset] = {
    "jarvis": VoicePreset(
        "jarvis",
        "de-DE-FlorianMultilingualNeural",
        "+6%",
        "-4Hz",
        "Erkek",
        "Tok, karizmatik Jarvis sesi (Multilingual V2 — Önerilen)",
    ),
    "florian": VoicePreset(
        "florian",
        "de-DE-FlorianMultilingualNeural",
        "+5%",
        "-2Hz",
        "Erkek",
        "Doğal, akıcı erkek sesi (Multilingual V2)",
    ),
    "brian": VoicePreset(
        "brian",
        "en-US-BrianMultilingualNeural",
        "+5%",
        "-2Hz",
        "Erkek",
        "Modern, dinamik erkek asistan sesi (Multilingual V2)",
    ),
    "remy": VoicePreset(
        "remy",
        "fr-FR-RemyMultilingualNeural",
        "+4%",
        "-2Hz",
        "Erkek",
        "Sıcak, doğal erkek sesi (Multilingual V2)",
    ),
    "ahmet": VoicePreset(
        "ahmet",
        "tr-TR-AhmetNeural",
        "+6%",
        "-3Hz",
        "Erkek",
        "Klasik Türkçe erkek sesi (sorun olursa Jarvis'e düşer)",
    ),
    "seraphina": VoicePreset(
        "seraphina",
        "de-DE-SeraphinaMultilingualNeural",
        "+5%",
        "+0Hz",
        "Kadın",
        "Doğal, yeni nesil kadın sesi (Multilingual V2)",
    ),
    "emma": VoicePreset(
        "emma",
        "en-US-EmmaMultilingualNeural",
        "+4%",
        "+0Hz",
        "Kadın",
        "Yumuşak, akıcı kadın sesi (Multilingual V2)",
    ),
    "emel": VoicePreset(
        "emel",
        "tr-TR-EmelNeural",
        "+6%",
        "+0Hz",
        "Kadın",
        "Standart Türkçe kadın sesi",
    ),
    "gemini-charon": VoicePreset(
        "gemini-charon",
        "gemini:Charon",
        "+0%",
        "+0Hz",
        "Erkek",
        "Gemini 2.5 Native Audio — Derin erkek sesi (Gemini anahtarı gerekir)",
    ),
    "gemini-fenrir": VoicePreset(
        "gemini-fenrir",
        "gemini:Fenrir",
        "+0%",
        "+0Hz",
        "Erkek",
        "Gemini 2.5 Native Audio — Güçlü erkek sesi (Gemini anahtarı gerekir)",
    ),
    "gemini-kore": VoicePreset(
        "gemini-kore",
        "gemini:Kore",
        "+0%",
        "+0Hz",
        "Kadın",
        "Gemini 2.5 Native Audio — Doğal kadın sesi (Gemini anahtarı gerekir)",
    ),
}

_MALE_FALLBACKS = [
    ("de-DE-FlorianMultilingualNeural", "+6%", "-4Hz"),
    ("en-US-BrianMultilingualNeural", "+5%", "-2Hz"),
    ("fr-FR-RemyMultilingualNeural", "+4%", "-2Hz"),
]

_FEMALE_FALLBACKS = [
    ("de-DE-SeraphinaMultilingualNeural", "+5%", "+0Hz"),
    ("en-US-EmmaMultilingualNeural", "+4%", "+0Hz"),
    ("tr-TR-EmelNeural", "+6%", "+0Hz"),
]

_MD_PATTERNS = [
    (re.compile(r"```.*?```", re.S), " "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),
    (re.compile(r"\*([^*]+)\*"), r"\1"),
    (re.compile(r"^#{1,6}\s*", re.M), ""),
    (re.compile(r"^\s*[-*•]\s+", re.M), ""),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
    (re.compile(r"https?://\S+"), ""),
    (re.compile(r"[*_#>|~^]+"), " "),
]


def resolve_voice(spec: str) -> VoicePreset:
    key = (spec or "jarvis").strip().lower()
    if key in VOICE_PRESETS:
        return VOICE_PRESETS[key]
    for p in VOICE_PRESETS.values():
        if p.voice_id.lower() == key:
            return p
    gender = "Kadın" if any(k in key for k in ("emel", "emma", "seraphina", "ava", "vivienne")) else "Erkek"
    return VoicePreset(spec, spec, "+4%", "+0Hz", gender, "Özel ses")


def clean(text: str) -> str:
    for pat, repl in _MD_PATTERNS:
        text = pat.sub(repl, text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    for sep in (". ", "! ", "? ", "; "):
        idx = cut.rfind(sep)
        if idx > max_chars * 0.5:
            return cut[: idx + 1]
    return cut.rsplit(" ", 1)[0] + "…"


def _cache_path(voice_id: str, rate: str, pitch: str, text: str) -> Path:
    h = hashlib.sha1(f"v2|{voice_id}|{rate}|{pitch}|{text}".encode()).hexdigest()[:16]
    return TTS_DIR / f"{h}.mp3"


# ---------- Ön-Isıtmalı (Pre-Warmed) WebSocket Motoru ----------
class _PrewarmedEdgeEngine:
    """LLM düşünürken Edge TTS WebSocket bağlantısını hazırda bekletir (~200ms ilk ses!)."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._session: aiohttp.ClientSession | None = None
        self._warm_ws: aiohttp.ClientWebSocketResponse | None = None
        self._warm_at: float = 0.0
        self._lock: asyncio.Lock | None = None
        self.prewarm()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            conn = aiohttp.TCPConnector(family=socket.AF_INET, ttl_dns_cache=600, keepalive_timeout=60)
            self._session = aiohttp.ClientSession(connector=conn)
        return self._session

    async def _open_ws(self) -> aiohttp.ClientWebSocketResponse:
        from edge_tts.communicate import SEC_MS_GEC_VERSION, WSS_HEADERS, WSS_URL, _SSL_CTX, connect_id, date_to_string
        from edge_tts.drm import DRM

        session = await self._ensure_session()
        ws = await session.ws_connect(
            f"{WSS_URL}&ConnectionId={connect_id()}"
            f"&Sec-MS-GEC={DRM.generate_sec_ms_gec()}"
            f"&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}",
            compress=15,
            headers=DRM.headers_with_muid(WSS_HEADERS),
            ssl=_SSL_CTX,
            timeout=8.0,
        )
        await ws.send_str(
            f"X-Timestamp:{date_to_string()}\r\n"
            "Content-Type:application/json; charset=utf-8\r\n"
            "Path:speech.config\r\n\r\n"
            '{"context":{"synthesis":{"audio":{"metadataoptions":{'
            '"sentenceBoundaryEnabled":"true","wordBoundaryEnabled":"false"'
            '},"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}\r\n'
        )
        return ws

    async def _prewarm_coro(self) -> None:
        async with self._get_lock():
            if (
                self._warm_ws is not None
                and not self._warm_ws.closed
                and (time.monotonic() - self._warm_at) < 20.0
            ):
                return
            if self._warm_ws is not None and not self._warm_ws.closed:
                try:
                    await self._warm_ws.close()
                except Exception:
                    pass
            try:
                self._warm_ws = await self._open_ws()
                self._warm_at = time.monotonic()
                log_.debug("TTS WebSocket ön-ısıtıldı (hazır)")
            except Exception as e:
                self._warm_ws = None
                log_.debug("TTS ön-ısıtma hatası: %s", e)

    async def _take_ws(self) -> aiohttp.ClientWebSocketResponse:
        async with self._get_lock():
            ws = self._warm_ws
            age = time.monotonic() - self._warm_at
            self._warm_ws = None
            if ws is not None and not ws.closed and age < 22.0:
                return ws
        if ws is not None and not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass
        return await self._open_ws()

    async def _stream_coro(
        self,
        text: str,
        voice_id: str,
        rate: str,
        pitch: str,
        out_path: Path,
        play_live: bool,
        on_start: Callable[[], None] | None,
    ) -> bool:
        from edge_tts.communicate import connect_id, date_to_string, get_headers_and_data, mkssml, ssml_headers_plus_data
        from edge_tts.data_classes import TTSConfig

        # mpv'yi ağ isteğiyle paralel olarak önceden başlat (0ms oynatıcı gecikmesi)
        proc: subprocess.Popen | None = None
        if play_live:
            try:
                proc = subprocess.Popen(
                    ["mpv", "--no-video", "--really-quiet", "--no-terminal", "--cache=no", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                proc = None

        ws = await self._take_ws()
        cfg = TTSConfig(voice_id, rate, "+0%", pitch, "SentenceBoundary")
        chunks: list[bytes] = []
        started = False

        try:
            await ws.send_str(ssml_headers_plus_data(connect_id(), date_to_string(), mkssml(cfg, text)))
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    if b"Path:turn.end" in msg.data.encode("utf-8", errors="ignore"):
                        break
                elif msg.type == aiohttp.WSMsgType.BINARY and len(msg.data) > 2:
                    hlen = int.from_bytes(msg.data[:2], "big")
                    params, data = get_headers_and_data(msg.data, hlen)
                    if params.get(b"Path") == b"audio" and data:
                        if not started:
                            started = True
                            if on_start:
                                try:
                                    on_start()
                                except Exception:
                                    pass
                        chunks.append(data)
                        if proc and proc.stdin:
                            try:
                                proc.stdin.write(data)
                                proc.stdin.flush()
                            except Exception:
                                pass
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break
        finally:
            try:
                await ws.close()
            except Exception:
                pass
            if proc:
                try:
                    if proc.stdin:
                        proc.stdin.close()
                    proc.wait(timeout=30)
                except Exception:
                    proc.kill()

        # Bir sonraki konuşma için yeni WebSocket'i arka planda hemen ısıt
        asyncio.create_task(self._prewarm_coro())

        if not chunks:
            return False

        raw_mp3 = b"".join(chunks)
        if len(raw_mp3) > 500:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(raw_mp3)
            _prune()
            if play_live and proc is None:
                audio.play_file(out_path)
            return True
        return False

    def prewarm(self) -> None:
        """LLM düşünmeye başladığında çağrılır; WebSocket'i arka planda hazırlar."""
        asyncio.run_coroutine_threadsafe(self._prewarm_coro(), self._loop)

    def stream_or_save(
        self,
        text: str,
        voice_id: str,
        rate: str,
        pitch: str,
        out_path: Path,
        play_live: bool = False,
        on_start: Callable[[], None] | None = None,
    ) -> bool:
        fut = asyncio.run_coroutine_threadsafe(
            self._stream_coro(text, voice_id, rate, pitch, out_path, play_live, on_start),
            self._loop,
        )
        return fut.result(timeout=45)


_ENGINE = _PrewarmedEdgeEngine()


def prewarm() -> None:
    """LLM yanıt üretirken ses bağlantısını önceden ısıtır."""
    try:
        _ENGINE.prewarm()
    except Exception:
        pass


def _gemini_key() -> str:
    k = os.environ.get("GEMINI_API_KEY", "").strip()
    if k:
        return k
    try:
        s = load()
        for p in s.providers:
            if p.id == "gemini" and p.key():
                return p.key()
    except Exception:
        pass
    return ""


def _gemini_tts(text: str, voice_name: str, path: Path) -> bool:
    """Gemini 2.5 Flash Preview TTS ile ultra doğal ses üretimi."""
    key = _gemini_key()
    if not key:
        return False
    try:
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent"
        body = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": voice_name}
                    }
                },
            },
        }
        r = requests.post(url, params={"key": key}, json=body, timeout=25)
        if r.status_code != 200:
            log_.debug("gemini tts HTTP %s: %s", r.status_code, r.text[:160])
            return False
        data = r.json()
        inline = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        pcm = base64.b64decode(inline["data"])
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(pcm)
        return path.exists() and path.stat().st_size > 500
    except Exception as e:
        log_.debug("gemini tts hatası: %s", e)
        return False


def _google_tts(text: str, lang: str, path: Path) -> bool:
    """Tüm nöral sesler çökerse son çare yedek."""
    try:
        q = urllib.parse.quote(text[:190])
        url = (
            "https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob"
            f"&tl={lang}&q={q}"
        )
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and r.content:
            path.write_bytes(r.content)
            return True
    except Exception as e:
        log_.debug("google tts hatası: %s", e)
    return False


def _prune() -> None:
    try:
        files = sorted(
            [*TTS_DIR.glob("*.mp3"), *TTS_DIR.glob("*.wav")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in files[200:]:
            old.unlink(missing_ok=True)
    except Exception:
        pass


def _candidates_for(preset: VoicePreset) -> list[tuple[str, str, str]]:
    fallbacks = _FEMALE_FALLBACKS if preset.gender == "Kadın" else _MALE_FALLBACKS
    candidates: list[tuple[str, str, str]] = []
    if not preset.voice_id.startswith("gemini:"):
        candidates.append((preset.voice_id, preset.rate, preset.pitch))
    for fb in fallbacks:
        if fb[0] not in [c[0] for c in candidates]:
            candidates.append(fb)
    return candidates


def synthesize(text: str, voice: str = "jarvis", lang: str = "tr", max_chars: int = 500) -> Path | None:
    """Metni sese çevirir ve dosya yolunu döndürür (doctor/test için)."""
    text = _clip(clean(text), max_chars)
    if not text:
        return None
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    preset = resolve_voice(voice)

    if preset.voice_id.startswith("gemini:"):
        gvoice = preset.voice_id.split(":", 1)[1]
        out_wav = TTS_DIR / f"{hashlib.sha1(f'gemini|{gvoice}|{text}'.encode()).hexdigest()[:16]}.wav"
        if out_wav.exists() and out_wav.stat().st_size > 500:
            return out_wav
        if _gemini_tts(text, gvoice, out_wav):
            _prune()
            return out_wav

    for cand_voice, rate, pitch in _candidates_for(preset):
        out = _cache_path(cand_voice, rate, pitch, text)
        if out.exists() and out.stat().st_size > 500:
            return out
        try:
            if _ENGINE.stream_or_save(text, cand_voice, rate, pitch, out, play_live=False):
                return out
        except Exception as e:
            log_.warning("nöral ses başarısız (%s): %s", cand_voice, e)

    out = TTS_DIR / f"{hashlib.sha1(f'g|{lang}|{text}'.encode()).hexdigest()[:16]}.mp3"
    if _google_tts(text, lang, out):
        return out
    return None


def speak(
    text: str,
    voice: str = "jarvis",
    lang: str = "tr",
    max_chars: int = 500,
    on_start: Callable[[], None] | None = None,
) -> None:
    """Metni gecikmesiz (canlı stream + ön-ısıtmalı WebSocket) seslendirir.
    on_start verilirse tam ilk ses karesi hoparlöre ulaştığı anda çağrılır (yazı-ses senkronizasyonu).
    """
    text = _clip(clean(text), max_chars)
    if not text:
        if on_start:
            on_start()
        return
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    preset = resolve_voice(voice)

    # 1) Önbellekte varsa anında (0ms ağ beklemesiyle) çal
    for cand_voice, rate, pitch in _candidates_for(preset):
        cached = _cache_path(cand_voice, rate, pitch, text)
        if cached.exists() and cached.stat().st_size > 500:
            if on_start:
                on_start()
            audio.play_file(cached)
            prewarm()
            return

    # 2) Gemini TTS seçiliyse
    if preset.voice_id.startswith("gemini:"):
        gvoice = preset.voice_id.split(":", 1)[1]
        out_wav = TTS_DIR / f"{hashlib.sha1(f'gemini|{gvoice}|{text}'.encode()).hexdigest()[:16]}.wav"
        if _gemini_tts(text, gvoice, out_wav):
            if on_start:
                on_start()
            audio.play_file(out_wav)
            return

    # 3) Ön-ısıtmalı WebSocket üzerinden canlı mpv stream (~200ms ilk ses!)
    for cand_voice, rate, pitch in _candidates_for(preset):
        out = _cache_path(cand_voice, rate, pitch, text)
        try:
            if _ENGINE.stream_or_save(text, cand_voice, rate, pitch, out, play_live=True, on_start=on_start):
                return
        except Exception as e:
            log_.warning("canlı ses akışı başarısız (%s): %s", cand_voice, e)

    # 4) Son çare yedek
    if on_start:
        on_start()
    out = TTS_DIR / f"{hashlib.sha1(f'g|{lang}|{text}'.encode()).hexdigest()[:16]}.mp3"
    if _google_tts(text, lang, out):
        audio.play_file(out)


def voices(lang_prefix: str = "tr") -> list[tuple[str, str, str]]:
    """Önerilen ses profillerini döndürür: (kısa_ad, cinsiyet, açıklama)."""
    out: list[tuple[str, str, str]] = []
    for p in VOICE_PRESETS.values():
        out.append((p.alias, p.gender, f"{p.desc} [{p.voice_id}]"))
    return out
