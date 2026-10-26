"""Mikrofon, kayıt (VAD) ve ses çalma."""
from __future__ import annotations

import io
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from . import log

log_ = log.get()

FRAME_MS = 30


def resolve_device(spec) -> int | None:
    """Ayarlardaki cihazı sounddevice indeksine çevirir."""
    if spec is None:
        return None
    if isinstance(spec, str) and spec.strip().lower() in ("", "default", "varsayilan"):
        return None
    try:
        if isinstance(spec, int) or (isinstance(spec, str) and spec.strip().lstrip("-").isdigit()):
            return int(spec)
    except ValueError:
        pass
    needle = str(spec).lower()
    devices = sd.query_devices()
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0 and dev["name"].lower() == needle:
            return idx
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0 and needle in dev["name"].lower():
            return idx
    log_.warning("Mikrofon bulunamadı: %s — varsayılan kullanılacak", spec)
    return None


def rms(frame: np.ndarray) -> float:
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))


class Mic:
    def __init__(self, samplerate: int = 16000, device=None):
        self.samplerate = samplerate
        self.device = resolve_device(device)
        self.frame_samples = int(samplerate * FRAME_MS / 1000)
        self.stream: sd.RawInputStream | None = None

    def open(self) -> None:
        if self.stream is not None:
            return
        self.stream = sd.RawInputStream(
            samplerate=self.samplerate,
            blocksize=self.frame_samples,
            device=self.device,
            dtype="int16",
            channels=1,
        )
        self.stream.start()

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None

    def read(self) -> np.ndarray:
        assert self.stream is not None
        data, overflowed = self.stream.read(self.frame_samples)
        if overflowed:
            log_.debug("mikrofon overflow")
        return np.frombuffer(bytes(data), dtype=np.int16).copy()

    def flush(self) -> None:
        """Konuşma sonrası biriken eski sesi atar."""
        if self.stream is None:
            return
        try:
            for _ in range(40):
                if self.stream.read_available < self.frame_samples:
                    break
                self.stream.read(self.frame_samples)
        except Exception:
            pass

    def calibrate(self, seconds: float = 0.6) -> float:
        """Ortam gürültü seviyesini ölçer (anlık seslerden etkilenmemek için alt çeyreği alır)."""
        self.open()
        vals = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            vals.append(rms(self.read()))
        vals.sort()
        base = vals[len(vals) // 4] if vals else 70.0
        return min(220.0, max(35.0, base))

    def record_command(
        self,
        noise_floor: float = 70.0,
        silence_sec: float = 0.9,
        max_sec: float = 15.0,
        start_timeout: float = 5.5,
        noise_multiplier: float = 1.8,
    ) -> np.ndarray | None:
        """Konuşma başlayınca kaydeder, sessizlikte durur (farklı ses seviyelerine uyumlu)."""
        self.open()
        threshold = min(460.0, max(125.0, noise_floor * noise_multiplier))
        pre_roll: list[np.ndarray] = []
        pre_roll_max = int(0.45 * 1000 / FRAME_MS)
        silence_frames_needed = int(silence_sec * 1000 / FRAME_MS)
        max_frames = int(max_sec * 1000 / FRAME_MS)
        start_frames_needed = 2

        collected: list[np.ndarray] = []
        voiced = 0
        silent = 0
        speaking = False
        started_at = time.monotonic()

        while len(collected) < max_frames:
            frame = self.read()
            level = rms(frame)

            if not speaking:
                pre_roll.append(frame)
                if len(pre_roll) > pre_roll_max:
                    pre_roll.pop(0)
                if level > threshold:
                    voiced += 1
                    if voiced >= start_frames_needed:
                        speaking = True
                        collected.extend(pre_roll)
                        pre_roll.clear()
                else:
                    voiced = 0
                if time.monotonic() - started_at > start_timeout:
                    return None
                continue

            collected.append(frame)
            if level > threshold:
                silent = 0
            else:
                silent += 1
                if silent >= silence_frames_needed and len(collected) > 10:
                    break

        if not collected:
            return None
        return np.concatenate(collected)


def to_wav_bytes(samples: np.ndarray, samplerate: int = 16000) -> bytes:
    # Kısık sesli veya uzaktaki konuşmacılar için otomatik ses seviyesi dengeleme (AGC)
    if samples.size > 0:
        f = samples.astype(np.float32)
        peak = float(np.max(np.abs(f)))
        if 300.0 < peak < 14000.0:
            gain = min(4.0, 18000.0 / peak)
            samples = np.clip(f * gain, -32768.0, 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(samples.astype(np.int16).tobytes())
    return buf.getvalue()


def chime(samplerate: int = 16000) -> None:
    """Kısa iki tonlu 'ding'."""
    try:
        t1 = np.linspace(0, 0.11, int(samplerate * 0.11), endpoint=False)
        t2 = np.linspace(0, 0.14, int(samplerate * 0.14), endpoint=False)
        env = np.minimum(1.0, np.linspace(0, 1, len(t1)) * 8)
        a = 0.22 * env * np.sin(2 * np.pi * 880 * t1)
        b = 0.22 * np.linspace(1, 0, len(t2)) ** 1.5 * np.sin(2 * np.pi * 1320 * t2)
        wave_ = np.concatenate([a, b, np.zeros(int(samplerate * 0.03))]).astype(np.float32)
        sd.play(wave_, samplerate)
        sd.wait()
    except Exception as e:  # ses aygıtı yoksa sessizce geç
        log_.debug("chime çalınamadı: %s", e)


def play_file(path: Path) -> bool:
    """mpv ile çalar; mpv yoksa ffplay dener."""
    for cmd in (
        ["mpv", "--no-video", "--really-quiet", "--no-terminal", str(path)],
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
    ):
        try:
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                return True
        except FileNotFoundError:
            continue
    return False
