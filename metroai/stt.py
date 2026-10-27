"""Sesi metne çevirme (STT) motorları:
- faster-whisper (yerel CTranslate2: base, small, tiny, turbo)
- Groq Cloud Whisper (whisper-large-v3)
- Gemini multimodal audio
- Vosk (yerel Kaldi yedek)
"""
from __future__ import annotations

import base64
import io
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
import requests

from . import log
from .settings import MODELS_DIR, Settings, load, save

log_ = log.get()

# ANSI renk kodları
GREEN = "\033[0;32m"
CYAN = "\033[0;36m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
BOLD = "\033[1m"
GRAY = "\033[0;90m"
WHITE = "\033[1;37m"
RESET = "\033[0m"

STT_MODELS: dict[str, dict[str, Any]] = {
    "whisper-base": {
        "id": "whisper-base",
        "name": "faster-whisper (Base)",
        "badge": "ÖNERİLEN YEREL",
        "engine": "whisper",
        "whisper_size": "base",
        "size_str": "~145 MB",
        "ram_str": "~450 MB",
        "speed": "200-350ms (Çok Hızlı)",
        "accuracy": "8.5 / 10",
        "desc": "Yerelde en dengeli model; Türkçe ekleri ve noktalama işaretlerini kusursuz yakalar.",
    },
    "whisper-small": {
        "id": "whisper-small",
        "name": "faster-whisper (Small)",
        "badge": "YÜKSEK HASSASİYET",
        "engine": "whisper",
        "whisper_size": "small",
        "size_str": "~460 MB",
        "ram_str": "~950 MB",
        "speed": "400-600ms (Hızlı)",
        "accuracy": "9.2 / 10",
        "desc": "Zorlu aksanlar, teknik terimler ve gürültülü ortamlar için ideal yerel model.",
    },
    "whisper-tiny": {
        "id": "whisper-tiny",
        "name": "faster-whisper (Tiny)",
        "badge": "ULTRA HAFİF",
        "engine": "whisper",
        "whisper_size": "tiny",
        "size_str": "~75 MB",
        "ram_str": "~250 MB",
        "speed": "100-200ms (Işık Hızında)",
        "accuracy": "7.5 / 10",
        "desc": "Eski donanımlar ve düşük bellekli sistemler için ultra hafif yerel model.",
    },
    "whisper-turbo": {
        "id": "whisper-turbo",
        "name": "faster-whisper (Large V3 Turbo)",
        "badge": "STÜDYO KALİTESİ",
        "engine": "whisper",
        "whisper_size": "deepdml/faster-whisper-large-v3-turbo-ct2",
        "size_str": "~800 MB",
        "ram_str": "~1500 MB",
        "speed": "600-900ms (Normal)",
        "accuracy": "9.8 / 10",
        "desc": "İnsan kulağı seviyesinde Türkçe anlama, stüdyo seviyesi transkripsiyon kalitesi.",
    },
    "groq": {
        "id": "groq",
        "name": "Groq Cloud Whisper (whisper-large-v3)",
        "badge": "BULUT / ÜCRETSİZ",
        "engine": "groq",
        "whisper_size": "",
        "size_str": "0 MB (Bulut)",
        "ram_str": "0 MB",
        "speed": "150-250ms (Işık Hızında)",
        "accuracy": "9.9 / 10",
        "desc": "Ücretsiz Groq API ile bulutta çalışır. 0 MB indirme, sıfır CPU/RAM yükü.",
    },
    "gemini": {
        "id": "gemini",
        "name": "Gemini 2.5 Flash Audio",
        "badge": "BULUT",
        "engine": "gemini",
        "whisper_size": "",
        "size_str": "0 MB (Bulut)",
        "ram_str": "0 MB",
        "speed": "600-1200ms",
        "accuracy": "9.5 / 10",
        "desc": "Google Gemini multimodal ses tanıma API'si (GEMINI_API_KEY gerektirir).",
    },
    "vosk-small-tr": {
        "id": "vosk-small-tr",
        "name": "Vosk Small TR (Klasik Kaldi)",
        "badge": "ESKİ MODEL",
        "engine": "vosk",
        "whisper_size": "",
        "size_str": "~45 MB",
        "ram_str": "~100 MB",
        "speed": "50ms (Anlık)",
        "accuracy": "3.0 / 10",
        "desc": "2020 Kaldi modeli. Uyanma kelimesi için yeterli ancak cümle tanımada zayıftır.",
    },
}


def is_stt_model_downloaded(model_id: str, s: Settings | None = None) -> bool:
    """Modelin yerelde hazır olup olmadığını kontrol eder."""
    if s is None:
        try:
            s = load()
        except Exception:
            s = Settings()

    if model_id == "groq":
        p = next((x for x in s.providers if x.id == "groq"), None)
        return bool(p and p.usable())

    if model_id == "gemini":
        p = next((x for x in s.providers if x.id == "gemini"), None)
        return bool(p and p.usable())

    if model_id == "vosk-small-tr":
        v_path = MODELS_DIR / "vosk-model-small-tr-0.3"
        return v_path.exists() and (v_path / "am").exists()

    if model_id.startswith("whisper-"):
        w_dir = MODELS_DIR / "whisper" / model_id
        if w_dir.exists() and (w_dir / "config.json").exists():
            return True
        return False

    return False


def download_vosk(dest_dir: Path) -> bool:
    """Vosk modelini alphacephei üzerinden indirir ve çıkartır."""
    url = "https://alphacephei.com/vosk/models/vosk-model-small-tr-0.3.zip"
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "tr.zip"
    try:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            done = 0
            with open(zip_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = int(done * 100 / total)
                            print(f"\r  İndiriliyor: %{pct} ({done // (1024*1024)}/{total // (1024*1024)} MB)", end="", flush=True)
        print()
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(dest_dir)
        zip_path.unlink(missing_ok=True)
        return True
    except Exception as e:
        print(f"\n  İndirme hatası: {e}")
        zip_path.unlink(missing_ok=True)
        return False


def download_stt_model(model_id: str) -> bool:
    """Seçilen modeli internetten çeker ve hazırlar."""
    info = STT_MODELS.get(model_id)
    if not info:
        print(f"Bilinmeyen model: {model_id}")
        return False

    if is_stt_model_downloaded(model_id):
        return True

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    if model_id.startswith("whisper-"):
        try:
            from faster_whisper import download_model
        except ImportError:
            print(f"  {RED}faster-whisper kütüphanesi eksik! Kuruluyor...{RESET}")
            import subprocess, sys
            subprocess.run([sys.executable, "-m", "pip", "install", "faster-whisper>=1.0.0"], check=True)
            from faster_whisper import download_model

        w_dir = MODELS_DIR / "whisper" / model_id
        w_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n  📥 {BOLD}{info['name']}{RESET} indiriliyor ({info['size_str']})...")
        print(f"  {GRAY}Hugging Face üzerinden model ağırlıkları çekiliyor, lütfen bekleyin...{RESET}")
        try:
            download_model(info["whisper_size"], output_dir=str(w_dir))
            print(f"  {GREEN}✓ {info['name']} başarıyla indirildi ve kuruldu.{RESET}")
            return True
        except Exception as e:
            print(f"  {RED}✗ Model indirme hatası: {e}{RESET}")
            return False

    elif model_id == "vosk-small-tr":
        print(f"\n  📥 {BOLD}Vosk Small TR{RESET} modeli indiriliyor (~45 MB)...")
        return download_vosk(MODELS_DIR)

    return True


def list_stt_models_display(current_id: str, s: Settings | None = None) -> list[tuple[str, str]]:
    """Modelleri konsol için biçimlendirilmiş liste olarak döner."""
    items = []
    for mid, info in STT_MODELS.items():
        is_cur = mid == current_id
        downloaded = is_stt_model_downloaded(mid, s)
        status_tag = f"{GREEN}[Hazır]{RESET}" if downloaded else f"{YELLOW}[İndirilmedi]{RESET}"
        if mid in ("groq", "gemini"):
            status_tag = f"{GREEN}[Bağlı]{RESET}" if downloaded else f"{GRAY}[Anahtarsız]{RESET}"
        items.append((mid, status_tag))
    return items


def select_stt_model_interactive(s: Settings) -> str:
    """Kullanıcıya modelleri listeler, seçim yaptırır ve gerekirse indirir."""
    current = getattr(s, "active_stt", "whisper-base")
    print(f"\n  {BOLD}🎤 Türkçe Ses Dinleme (STT) Modelleri:{RESET}")
    print(f"  {GRAY}Metro'nun konuştuklarını en doğru şekilde anlayabilmesi için dilediğin modeli seç.{RESET}\n")

    model_keys = list(STT_MODELS.keys())
    for idx, mid in enumerate(model_keys, 1):
        info = STT_MODELS[mid]
        is_cur = mid == current
        mark = f"{GREEN}●{RESET}" if is_cur else " "
        downloaded = is_stt_model_downloaded(mid, s)
        if mid in ("groq", "gemini"):
            stat = f"{GREEN}[Anahtar Bağlı]{RESET}" if downloaded else f"{GRAY}[API Anahtarı Gerekir]{RESET}"
        else:
            stat = f"{GREEN}[İndirildi]{RESET}" if downloaded else f"{YELLOW}[İndirilecek - {info['size_str']}]{RESET}"

        badge = f"{CYAN}[{info['badge']}]{RESET}"
        print(f"    {mark} {CYAN}{idx}){RESET} {BOLD}{info['name']:<38}{RESET} {badge:<24} {stat}")
        print(f"       {GRAY}└─ Hız: {info['speed']} | Doğruluk: {info['accuracy']} | RAM: {info['ram_str']}{RESET}")

    cur_idx = str(model_keys.index(current) + 1) if current in model_keys else "1"
    try:
        pick = input(f"\n  {WHITE}{BOLD}Model seçimin (1-{len(model_keys)}) [{cur_idx}]: {RESET}").strip()
    except (EOFError, KeyboardInterrupt):
        return current

    if not pick:
        pick = cur_idx

    chosen_id = current
    if pick.isdigit() and 1 <= int(pick) <= len(model_keys):
        chosen_id = model_keys[int(pick) - 1]
    elif pick in STT_MODELS:
        chosen_id = pick

    # Eğer seçilen model henüz indirilmediyse veya API anahtarı yoksa:
    info = STT_MODELS[chosen_id]
    if chosen_id == "groq":
        p = next((x for x in s.providers if x.id == "groq"), None)
        if not p or not p.usable():
            print(f"\n  {CYAN}👉 Ücretsiz Groq API Anahtarı:{RESET} https://console.groq.com/keys")
            key = input(f"  {WHITE}Groq API anahtarını yapıştır (boş=iptal): {RESET}").strip()
            if key and p:
                p.api_key = key
                p.enabled = True
                save(s)
                print(f"  {GREEN}✓ Groq Whisper STT bağlandı!{RESET}")
    elif chosen_id == "gemini":
        p = next((x for x in s.providers if x.id == "gemini"), None)
        if not p or not p.usable():
            print(f"\n  {CYAN}👉 Ücretsiz Gemini API Anahtarı:{RESET} https://aistudio.google.com/apikey")
            key = input(f"  {WHITE}Gemini API anahtarını yapıştır (boş=iptal): {RESET}").strip()
            if key and p:
                p.api_key = key
                p.enabled = True
                save(s)
                print(f"  {GREEN}✓ Gemini Audio bağlandı!{RESET}")
    elif not is_stt_model_downloaded(chosen_id, s):
        download_stt_model(chosen_id)

    s.active_stt = chosen_id
    save(s)
    print(f"\n  {GREEN}✓ Aktif Ses Dinleme Modeli:{RESET} {BOLD}{info['name']}{RESET}\n")
    return chosen_id


class Stt:
    _whisper_instance = None
    _whisper_loaded_model = None

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
        active = getattr(self.s, "active_stt", "whisper-base")

        # 1. Öncelikli olarak seçilen aktif STT modelini çalıştır
        if active.startswith("whisper-"):
            try:
                text = self._whisper(wav, active)
                return text.strip(), active
            except Exception as e:
                log_.warning("Yerel faster-whisper (%s) STT hatası: %s", active, e)

        elif active == "groq":
            try:
                text = self._groq(wav)
                return text.strip(), "groq"
            except Exception as e:
                log_.warning("Groq STT hatası: %s", e)

        elif active == "gemini":
            try:
                text = self._gemini(wav)
                return text.strip(), "gemini"
            except Exception as e:
                log_.warning("Gemini STT hatası: %s", e)

        elif active in ("vosk", "vosk-small-tr"):
            if self.local is not None:
                try:
                    text = self.local.transcribe(wav)
                    return text.strip(), "vosk"
                except Exception as e:
                    log_.warning("Vosk STT hatası: %s", e)

        # 2. Fallback Zinciri (Eğer seçilen model yanıt vermezse)
        for name in self.s.stt_order:
            if name == active:
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
                log_.warning("STT %s fallback hatası: %s", name, e)
                self._cool(name, 600)

        # Son çare yerel vosk
        if self.local is not None and "vosk" in self.s.stt_order:
            try:
                text = self.local.transcribe(wav)
                if text:
                    return text.strip(), "vosk"
            except Exception as e:
                log_.warning("yerel Vosk STT fallback hatası: %s", e)

        return "", "none"

    def _whisper(self, wav: bytes, model_id: str) -> str:
        """faster-whisper ile yerel transkripsiyon (CTranslate2 int8 / CUDA)."""
        from faster_whisper import WhisperModel
        import ctranslate2

        info = STT_MODELS.get(model_id, STT_MODELS["whisper-base"])
        whisper_dir = MODELS_DIR / "whisper" / model_id

        if Stt._whisper_instance is None or Stt._whisper_loaded_model != model_id:
            log_.info("faster-whisper başlatılıyor: %s", model_id)
            if not is_stt_model_downloaded(model_id, self.s):
                download_stt_model(model_id)

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
            model_target = str(whisper_dir) if (whisper_dir / "config.json").exists() else info["whisper_size"]

            Stt._whisper_instance = WhisperModel(
                model_target,
                device=device,
                compute_type=compute_type,
                download_root=str(MODELS_DIR / "whisper"),
            )
            Stt._whisper_loaded_model = model_id

        # WAV baytlarını float32 PCM dizisine dönüştür
        try:
            with wave.open(io.BytesIO(wav), "rb") as w:
                frames = w.readframes(w.getnframes())
                pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception:
            pcm = np.frombuffer(wav[44:], dtype=np.int16).astype(np.float32) / 32768.0

        if len(pcm) < 1600:  # < 0.1 sn sessizlik
            return ""

        segments, _ = Stt._whisper_instance.transcribe(
            pcm,
            language=self.s.language,
            beam_size=5,
            condition_on_previous_text=False,
        )
        return " ".join(seg.text for seg in segments).strip()

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
        cands = data.get("candidates", [])
        if not cands:
            return ""
        parts = cands[0].get("content", {}).get("parts", [])
        return "".join(part.get("text", "") for part in parts).strip()
