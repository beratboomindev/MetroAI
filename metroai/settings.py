"""Ayar yükleme / kaydetme ve yollar."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("METRO_CONFIG", ROOT / "config.toml"))
STATE_DIR = ROOT / "state"
CACHE_DIR = STATE_DIR / "cache"
LOG_DIR = STATE_DIR / "logs"
MODELS_DIR = ROOT / "models"


@dataclass
class Provider:
    id: str
    base_url: str
    models: list[str] = field(default_factory=list)
    api_key: str = ""
    api_key_env: str = ""
    free_only: bool = False
    dynamic: bool = True
    enabled: bool = True
    keyless: bool = False

    def key(self) -> str:
        k = self.api_key or (os.environ.get(self.api_key_env, "") if self.api_key_env else "")
        return k.strip()

    def usable(self) -> bool:
        return self.enabled and (self.keyless or bool(self.key()))


@dataclass
class Settings:
    name: str = "Metro"
    user_name: str = ""
    wake_words: list[str] = field(default_factory=lambda: ["metro"])
    language: str = "tr"
    voice: str = "metro"
    ack_phrase: str = ""
    chime: bool = True
    confirm_dangerous: bool = True
    auto_open_terminal: bool = True
    always_on_top: bool = True
    terminal: str = "auto"
    hotkey: str = ""
    active_model: str = ""
    custom_prompt: str = ""
    save_history: bool = True
    aliases: dict[str, str] = field(default_factory=dict)
    max_reply_chars: int = 500
    history_turns: int = 6
    providers: list[Provider] = field(default_factory=list)
    stt_order: list[str] = field(default_factory=lambda: ["groq", "gemini", "vosk"])
    stt_model: str = "whisper-large-v3"
    vosk_model: str = "models/vosk-model-small-tr-0.3"
    input_device: str | int | None = None
    sample_rate: int = 16000
    silence_sec: float = 0.85
    max_record_sec: float = 15.0
    noise_multiplier: float = 1.8
    raw: dict[str, Any] = field(default_factory=dict)


DEFAULT_PROVIDERS: list[dict[str, Any]] = [
    {
        # En yüksek öncelik: OpenCode ücretsiz modelleri (anahtarsız / opencode CLI veya OPENCODE_API_KEY)
        "id": "opencode",
        "base_url": "https://opencode.ai/zen/v1",
        "api_key_env": "OPENCODE_API_KEY",
        "models": [
            "opencode/deepseek-v4.1-flash-free",
            "opencode/glm-5.3-flash-free",
            "opencode/mimo-v2.6-flash-free",
            "opencode/nemotron-3.5-lightning-free",
            "opencode/beam-free",
            "opencode/flint-40b-free",
            "opencode/big-pickle",
        ],
        "free_only": True,
        "keyless": True,
    },
    {
        "id": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "models": ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
    },
    {
        "id": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key_env": "GEMINI_API_KEY",
        "models": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"],
    },
    {
        "id": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "models": [],
        "free_only": True,
    },
    {
        "id": "cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "api_key_env": "CEREBRAS_API_KEY",
        "models": ["llama-3.3-70b", "llama-3.1-8b"],
    },
    {
        "id": "mistral",
        "base_url": "https://api.mistral.ai/v1",
        "api_key_env": "MISTRAL_API_KEY",
        "models": ["mistral-small-latest", "open-mistral-nemo"],
    },
    {
        # Anahtarsız, tamamen ücretsiz yedek. Kimlik gerektirmez.
        "id": "pollinations",
        "base_url": "https://text.pollinations.ai/openai",
        "api_key_env": "",
        "models": ["openai-fast"],
        "keyless": True,
    },
]


def load() -> Settings:
    s = Settings()
    data: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)

    s.raw = data
    a = data.get("assistant", {})
    s.name = a.get("name", s.name)
    s.user_name = str(a.get("user_name", s.user_name)).strip()
    s.wake_words = [w.lower() for w in a.get("wake_words", s.wake_words)]
    s.language = a.get("language", s.language)
    s.voice = a.get("voice", s.voice)
    s.ack_phrase = a.get("ack_phrase", s.ack_phrase)
    s.chime = bool(a.get("chime", s.chime))
    s.confirm_dangerous = bool(a.get("confirm_dangerous", s.confirm_dangerous))
    s.auto_open_terminal = bool(a.get("auto_open_terminal", s.auto_open_terminal))
    s.always_on_top = bool(a.get("always_on_top", s.always_on_top))
    s.terminal = str(a.get("terminal", s.terminal))
    s.hotkey = str(a.get("hotkey", s.hotkey)).strip()
    s.active_model = str(a.get("active_model", s.active_model)).strip()
    s.custom_prompt = str(a.get("custom_prompt", s.custom_prompt)).strip()
    s.save_history = bool(a.get("save_history", s.save_history))
    s.max_reply_chars = int(a.get("max_reply_chars", s.max_reply_chars))
    s.history_turns = int(a.get("history_turns", s.history_turns))

    raw_aliases = data.get("aliases", {})
    if isinstance(raw_aliases, dict):
        s.aliases = {str(k).strip(): str(v).strip() for k, v in raw_aliases.items() if str(k).strip() and str(v).strip()}

    stt = data.get("stt", {})
    s.stt_order = stt.get("provider_order", s.stt_order)
    s.stt_model = stt.get("model", s.stt_model)
    s.vosk_model = stt.get("vosk_model", s.vosk_model)

    au = data.get("audio", {})
    s.input_device = au.get("input_device", s.input_device)
    s.sample_rate = int(au.get("sample_rate", s.sample_rate))
    s.silence_sec = float(au.get("silence_sec", s.silence_sec))
    s.max_record_sec = float(au.get("max_record_sec", s.max_record_sec))
    s.noise_multiplier = float(au.get("noise_multiplier", s.noise_multiplier))

    raw_providers = data.get("providers")
    if not raw_providers:
        raw_providers = DEFAULT_PROVIDERS

    for p in raw_providers:
        s.providers.append(
            Provider(
                id=p["id"],
                base_url=p["base_url"].rstrip("/"),
                models=list(p.get("models", [])),
                api_key=p.get("api_key", "") or "",
                api_key_env=p.get("api_key_env", ""),
                free_only=bool(p.get("free_only", False)),
                dynamic=bool(p.get("dynamic", True)),
                enabled=bool(p.get("enabled", True)),
                keyless=bool(p.get("keyless", False)),
            )
        )
    return s


def vosk_path(s: Settings) -> Path:
    p = Path(s.vosk_model)
    return p if p.is_absolute() else ROOT / p


def ensure_dirs() -> None:
    for d in (STATE_DIR, CACHE_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _toml_str(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_list(items: list[str]) -> str:
    return "[" + ", ".join(_toml_str(i) for i in items) + "]"


def render_config(s: Settings) -> str:
    lines: list[str] = []
    lines.append("# Metro yapılandırması — `./metro setup` ile yeniden oluşturabilirsin.\n")
    lines.append("[assistant]")
    lines.append(f"name = {_toml_str(s.name)}")
    lines.append(f"user_name = {_toml_str(s.user_name)}")
    lines.append(f"wake_words = {_toml_list(s.wake_words)}")
    lines.append(f"language = {_toml_str(s.language)}")
    lines.append(f"voice = {_toml_str(s.voice)}")
    lines.append(f"ack_phrase = {_toml_str(s.ack_phrase)}")
    lines.append(f"chime = {'true' if s.chime else 'false'}")
    lines.append(f"confirm_dangerous = {'true' if s.confirm_dangerous else 'false'}")
    lines.append(f"auto_open_terminal = {'true' if s.auto_open_terminal else 'false'}")
    lines.append(f"always_on_top = {'true' if s.always_on_top else 'false'}")
    lines.append(f"terminal = {_toml_str(s.terminal)}")
    lines.append(f"hotkey = {_toml_str(s.hotkey)}")
    lines.append(f"active_model = {_toml_str(s.active_model)}")
    lines.append(f"custom_prompt = {_toml_str(s.custom_prompt)}")
    lines.append(f"save_history = {'true' if s.save_history else 'false'}")
    lines.append(f"max_reply_chars = {s.max_reply_chars}")
    lines.append(f"history_turns = {s.history_turns}\n")

    if s.aliases:
        lines.append("[aliases]")
        for k, v in s.aliases.items():
            lines.append(f"{_toml_str(k)} = {_toml_str(v)}")
        lines.append("")

    lines.append("[stt]")
    lines.append(f"provider_order = {_toml_list(s.stt_order)}")
    lines.append(f"model = {_toml_str(s.stt_model)}")
    lines.append(f"vosk_model = {_toml_str(s.vosk_model)}\n")

    lines.append("[audio]")
    dev = s.input_device
    if dev is None or (isinstance(dev, str) and not dev.strip()):
        dev_line = '"default"'
    elif isinstance(dev, str):
        dev_line = _toml_str(dev)
    else:
        dev_line = str(dev)
    lines.append(f"input_device = {dev_line}")
    lines.append(f"sample_rate = {s.sample_rate}")
    lines.append(f"silence_sec = {s.silence_sec}")
    lines.append(f"max_record_sec = {s.max_record_sec}")
    lines.append(f"noise_multiplier = {s.noise_multiplier}\n")

    for p in s.providers:
        lines.append("[[providers]]")
        lines.append(f"id = {_toml_str(p.id)}")
        lines.append(f"base_url = {_toml_str(p.base_url)}")
        lines.append(f"api_key = {_toml_str(p.api_key)}")
        lines.append(f"api_key_env = {_toml_str(p.api_key_env)}")
        lines.append(f"models = {_toml_list(p.models)}")
        lines.append(f"free_only = {'true' if p.free_only else 'false'}")
        lines.append(f"dynamic = {'true' if p.dynamic else 'false'}")
        lines.append(f"enabled = {'true' if p.enabled else 'false'}")
        if p.keyless:
            lines.append("keyless = true")
        lines.append("")
    return "\n".join(lines)


def save(s: Settings) -> None:
    CONFIG_PATH.write_text(render_config(s), encoding="utf-8")
    try:
        CONFIG_PATH.chmod(0o600)  # API anahtarları içerir
    except OSError:
        pass
