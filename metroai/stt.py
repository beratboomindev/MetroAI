"""Sesi metne çevirme: Groq Whisper → Gemini → yerel Vosk."""
from __future__ import annotations

import base64
import time

import requests

from . import log
from .settings import Settings

log_ = log.get()


class Stt:
    def __init__(self, settings: Settings, local=None):
        self.s = settings
        self.local = local  # WakeWord (vosk) nesnesi
        self.session = requests.Session()
        self._cooldown: dict[str, float] = {}

    def _cooling(self, name: str) -> bool:
        until = self._cooldown.get(name, 0)
        return until > time.time()

    def _cool(self, name: str, seconds: int = 600) -> None:
        self._cooldown[name] = time.time() + seconds

    def _provider(self, pid: str):
        for p in self.s.providers:
            if p.id == pid and p.usable():
                return p
        return None

    def transcribe(self, wav: bytes) -> tuple[str, str]:
        order = self.s.stt_order
        for name in order:
            if name == "vosk":
                continue
            if self._cooling(name):
                continue
            try:
                if name == "groq":
                    text = self._groq(wav)
                elif name == "gemini":
                    text = self._gemini(wav)
                else:
                    continue
                if text:
                    return text.strip(), name
                self._cool(name, 300)
            except Exception as e:
                log_.warning("STT %s hatası: %s", name, e)
                self._cool(name, 600)

        if self.local is not None and "vosk" in order:
            try:
                text = self.local.transcribe(wav)
                if text:
                    return text.strip(), "vosk"
            except Exception as e:
                log_.warning("yerel STT hatası: %s", e)
        return "", "none"

    def _groq(self, wav: bytes) -> str:
        p = self._provider("groq")
        if not p:
            return ""
        r = self.session.post(
            f"{p.base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {p.key()}"},
            files={"file": ("command.wav", wav, "audio/wav")},
            data={
                "model": self.s.stt_model,
                "language": self.s.language,
                "response_format": "json",
                "temperature": "0",
            },
            timeout=(10, 45),
        )
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
        return r.json().get("text", "")

    def _gemini(self, wav: bytes) -> str:
        p = self._provider("gemini")
        if not p:
            return ""
        base = p.base_url.removesuffix("/openai")
        model = "gemini-2.5-flash"
        body = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": (
                                "Bu ses kaydındaki konuşmayı olduğu gibi Türkçe metne çevir. "
                                "Sadece transkripti yaz, açıklama veya noktalama yorumu ekleme."
                            )
                        },
                        {
                            "inline_data": {
                                "mime_type": "audio/wav",
                                "data": base64.b64encode(wav).decode(),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 300},
        }
        r = self.session.post(
            f"{base}/models/{model}:generateContent",
            params={"key": p.key()},
            json=body,
            timeout=(10, 60),
        )
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
        data = r.json()
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(part.get("text", "") for part in parts).strip()
