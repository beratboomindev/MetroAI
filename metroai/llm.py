"""Ücretsiz model zinciri: biri tükenirse otomatik diğerine geçer."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from . import log
from .settings import CACHE_DIR, STATE_DIR, Provider, Settings

log_ = log.get()

COOLDOWN_FILE = STATE_DIR / "cooldowns.json"
OPENCODE_SESSION_FILE = STATE_DIR / "opencode_session.json"
DISCOVERY_TTL = 6 * 3600
MAX_OPENCODE_TURNS = 15
MAX_OPENCODE_CHARS = 9500


@dataclass
class Reply:
    text: str
    provider: str
    model: str


class NoModelAvailable(RuntimeError):
    pass


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


_API_HINTS = ("whisper", "tts", "embed", "guard", "moderation", "rerank", "vision-only", "distil-whisper")


def _opencode_bin() -> str | None:
    found = shutil.which("opencode")
    if found:
        return found
    for cand in (Path.home() / ".opencode/bin/opencode", Path.home() / ".local/bin/opencode"):
        if cand.exists():
            return str(cand)
    return None


class LLMChain:
    def __init__(self, settings: Settings):
        self.s = settings
        self.session = requests.Session()
        self.cooldowns: dict[str, dict] = _load_json(COOLDOWN_FILE, {})
        self._discovery: dict[str, list[str]] = {}
        self.last_used: str | None = None

    # ---------- keşif ----------
    def _discover_opencode(self, p: Provider, cache: Path, cached: dict) -> list[str]:
        oc = _opencode_bin()
        ids: list[str] = []
        if oc:
            try:
                r = subprocess.run([oc, "models"], capture_output=True, text=True, timeout=15)
                if r.returncode == 0:
                    for line in r.stdout.splitlines():
                        line = line.strip()
                        if line.startswith("opencode/") and (
                            not p.free_only or line.endswith("-free") or line.endswith("big-pickle")
                        ):
                            ids.append(line)
            except Exception as e:
                log_.debug("opencode models hatası: %s", e)
        if not ids:
            try:
                r2 = self.session.get(
                    f"{p.base_url}/models",
                    headers={"Authorization": f"Bearer {p.key() or 'public'}"},
                    timeout=15,
                )
                if r2.status_code == 200:
                    for item in r2.json().get("data", []):
                        mid = item.get("id", "")
                        if mid and (not p.free_only or mid.endswith("-free") or mid == "big-pickle"):
                            ids.append(mid if mid.startswith("opencode/") else f"opencode/{mid}")
            except Exception as e:
                log_.debug("opencode /models hatası: %s", e)
        if ids:
            _save_json(cache, {"fetched_at": time.time(), "models": ids})
            self._discovery[p.id] = ids
            return ids
        return cached.get("models", []) or list(p.models)

    def _discover(self, p: Provider, force: bool = False) -> list[str]:
        cache = CACHE_DIR / f"models_{p.id}.json"
        cached = _load_json(cache, {})
        if not force and cached.get("fetched_at", 0) + DISCOVERY_TTL > time.time():
            return cached.get("models", [])
        if p.id == "opencode":
            return self._discover_opencode(p, cache, cached)
        headers = {"Authorization": f"Bearer {p.key()}"} if p.key() else {}
        try:
            r = self.session.get(f"{p.base_url}/models", headers=headers, timeout=20)
            if r.status_code != 200:
                log_.debug("%s model listesi alınamadı (%s)", p.id, r.status_code)
                return cached.get("models", [])
            data = r.json().get("data", [])
        except Exception as e:
            log_.debug("%s model listesi hatası: %s", p.id, e)
            return cached.get("models", [])

        found: list[tuple[str, int, bool]] = []
        for item in data:
            mid = item.get("id") or item.get("name")
            if not mid:
                continue
            pricing = item.get("pricing") or {}
            try:
                price = float(pricing.get("prompt", 0) or 0)
            except (TypeError, ValueError):
                price = 0.0
            is_free = mid.endswith(":free") or price == 0.0
            ctx = int(item.get("context_length") or item.get("context_window") or 0)
            found.append((mid, ctx, is_free))

        if p.free_only:
            found = [f for f in found if f[2]]
        if not p.models:
            found = [f for f in found if not any(h in f[0].lower() for h in _API_HINTS)]
            found.sort(key=lambda f: f[1], reverse=True)

        ids = [f[0] for f in found]
        _save_json(cache, {"fetched_at": time.time(), "models": ids})
        self._discovery[p.id] = ids
        log_.debug("%s: %d model keşfedildi", p.id, len(ids))
        return ids

    def candidates(self) -> list[tuple[Provider, str]]:
        out: list[tuple[Provider, str]] = []
        pinned_target = (self.s.active_model or "").strip()
        pinned_pair: tuple[Provider, str] | None = None

        for p in self.s.providers:
            if not p.usable():
                continue
            models = list(p.models)
            if p.dynamic:
                discovered = self._discovery.get(p.id) or self._discover(p)
                if discovered:
                    if models:
                        ordered = [m for m in models if m in discovered]
                        extras = [
                            m for m in discovered
                            if m not in ordered and not any(h in m.lower() for h in _API_HINTS)
                        ]
                        ordered += extras[: (0 if p.free_only else 2)]
                        models = ordered or discovered[:5]
                    else:
                        models = discovered[:6]
            for m in models:
                full_key = f"{p.id}/{m}"
                if pinned_target and (
                    pinned_target == full_key
                    or pinned_target == m
                    or (p.id == "opencode" and f"opencode/{pinned_target}" == m)
                ):
                    pinned_pair = (p, m)
                if self._cooling(p.id, m):
                    continue
                out.append((p, m))

        # Kullanıcı elle bir model seçtiyse fakat listede yoksa yine de sağlayıcıyı bulup en başa ekle
        if pinned_target and pinned_pair is None and "/" in pinned_target:
            pid, mid = pinned_target.split("/", 1)
            for p in self.s.providers:
                if p.id == pid and p.usable():
                    cand_model = pinned_target if pid == "opencode" else mid
                    pinned_pair = (p, cand_model)
                    break

        if pinned_pair is not None:
            out = [pinned_pair] + [ item for item in out if not (item[0].id == pinned_pair[0].id and item[1] == pinned_pair[1]) ]
        return out

    def reset_cooldowns(self) -> None:
        self.cooldowns.clear()
        _save_json(COOLDOWN_FILE, self.cooldowns)

    def reset_session(self) -> None:
        """Aktif OpenCode oturumunu temizler ve bir sonraki mesajda taze oturum başlatır."""
        sess = _load_json(OPENCODE_SESSION_FILE, {})
        old_sid = sess.get("session_id", "")
        if old_sid:
            oc = _opencode_bin()
            if oc:
                try:
                    subprocess.Popen(
                        [oc, "session", "delete", old_sid],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    pass
        try:
            OPENCODE_SESSION_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    # ---------- bekleme süreleri ----------
    @staticmethod
    def _ck(p: str, m: str) -> str:
        return f"{p}/{m}"

    def _cooling(self, p: str, m: str) -> bool:
        entry = self.cooldowns.get(self._ck(p, m))
        if not entry:
            return False
        if entry.get("until", 0) <= time.time():
            self.cooldowns.pop(self._ck(p, m), None)
            return False
        return True

    def cool_down(self, p: str, m: str, seconds: int, reason: str) -> None:
        self.cooldowns[self._ck(p, m)] = {
            "until": time.time() + seconds,
            "reason": reason[:200],
            "at": time.time(),
        }
        _save_json(COOLDOWN_FILE, self.cooldowns)
        log_.warning("%s/%s devre dışı (%ds): %s", p, m, seconds, reason[:120])

    # ---------- çağrı ----------
    def chat(self, messages: list[dict], max_tokens: int = 450, temperature: float = 0.6) -> Reply:
        cands = self.candidates()
        if not cands:
            raise NoModelAvailable(self.status_text())

        errors: list[str] = []
        for provider, model in cands:
            try:
                text = self._call(provider, model, messages, max_tokens, temperature)
                self.last_used = model if model.startswith(f"{provider.id}/") else f"{provider.id}/{model}"
                log_.info("yanıt: %s", self.last_used)
                return Reply(text=text, provider=provider.id, model=model)
            except _ModelError as e:
                self.cool_down(provider.id, model, e.cooldown, e.reason)
                errors.append(f"{provider.id}/{model}: {e.reason}")
                if e.refresh:
                    self._discover(provider, force=True)

        raise NoModelAvailable("\n".join(errors) or self.status_text())

    def _call_opencode(self, p: Provider, model: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
        # 1) Kullanıcı özel OPENCODE_API_KEY tanımladıysa doğrudan HTTP dene
        if p.key():
            bare = model.removeprefix("opencode/")
            try:
                return self._call_http(p, bare, messages, max_tokens, temperature)
            except _ModelError:
                pass

        # 2) Resmi opencode CLI üzerinden ücretsiz modelleri çağır (Tek Oturum + Şişince Otomatik Yenileme)
        oc = _opencode_bin()
        if not oc:
            raise _ModelError("opencode CLI bulunamadı", 3600)

        full_model = model if "/" in model else f"opencode/{model}"

        sess = _load_json(OPENCODE_SESSION_FILE, {})
        sid = str(sess.get("session_id") or "").strip()
        turns = int(sess.get("turns") or 0)
        total_chars = int(sess.get("chars") or 0)

        # Bağlam (context) çok şiştiyse eski oturumu arka planda sil ve taze oturuma geç
        if sid and (turns >= MAX_OPENCODE_TURNS or total_chars >= MAX_OPENCODE_CHARS):
            log_.info("OpenCode bağlamı doldu (%d tur, %d krk) -> taze oturum açılıyor", turns, total_chars)
            try:
                subprocess.Popen(
                    [oc, "session", "delete", sid],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass
            sid = ""
            turns = 0
            total_chars = 0

        user_msg = ""
        sys_msg = ""
        for m in messages:
            role = m.get("role", "")
            content = (m.get("content") or "").strip()
            if role == "system":
                sys_msg = content
            elif role == "user":
                user_msg = content

        if not sid:
            # İlk mesajda (veya bağlam yenilendiğinde) sistem kurallarını + son bağlamı bir kez tanıt
            parts: list[str] = []
            if sys_msg:
                parts.append(sys_msg)
            # Yenilenen oturumda son 2 konuşma çiftini özet bağlam olarak aktar
            non_sys = [m for m in messages if m.get("role") != "system"]
            for m in non_sys[-5:]:
                r_label = "Kullanıcı" if m.get("role") == "user" else "Asistan"
                c_text = (m.get("content") or "").strip()
                if c_text:
                    parts.append(f"{r_label}: {c_text}")
            parts.append(
                "Not: Kendi dosya/bash araçlarını ÇALIŞTIRMA. Yalnızca yukarıdaki kurallara göre kısa düz metin veya araç JSON'u döndür."
            )
            prompt = "\n\n".join(parts)
            cmd = [oc, "run", "--format", "json", "--title", "Metro Assistant", "-m", full_model, prompt]
        else:
            # Mevcut oturuma devam et: kendini tekrar tanıtma, sadece yeni mesajı gönder (minimum token!)
            mem_line = ""
            if "KULLANICI HAFIZASI:" in sys_msg:
                for line in sys_msg.splitlines():
                    if line.startswith("KULLANICI HAFIZASI:"):
                        mem_line = f"[{line}]\n"
                        break
            prompt = (
                f"{mem_line}{user_msg}\n"
                "(Kendi bash/dosya aracını çalıştırma; sadece Türkçe düz metin veya {\"tool\":...} JSON döndür.)"
            )
            cmd = [oc, "run", "--format", "json", "-s", sid, "-m", full_model, prompt]

        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=45,
            )
        except subprocess.TimeoutExpired:
            raise _ModelError("opencode zaman aşımı", 120)
        except Exception as e:
            raise _ModelError(f"opencode hatası: {e}", 180)

        texts: list[str] = []
        err_msg = ""
        new_sid = sid
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if not new_sid and ev.get("sessionID"):
                new_sid = str(ev["sessionID"])
            etype = ev.get("type")
            if etype == "text":
                t = (ev.get("part") or {}).get("text") or ""
                if t:
                    texts.append(t)
            elif etype == "error":
                err_msg = str(ev.get("error") or ev)

        out = "".join(texts).strip()
        if out:
            if new_sid:
                _save_json(
                    OPENCODE_SESSION_FILE,
                    {
                        "session_id": new_sid,
                        "turns": turns + 1,
                        "chars": total_chars + len(prompt) + len(out),
                        "updated_at": time.time(),
                    },
                )
            return out
        # Eğer eski session silindiyse ve hata verdiyse oturumu sıfırla
        if sid:
            try:
                OPENCODE_SESSION_FILE.unlink(missing_ok=True)
            except Exception:
                pass
        if err_msg or r.returncode != 0:
            detail = (err_msg or r.stderr or r.stdout or f"kod {r.returncode}").strip()[:180]
            raise _ModelError(f"opencode ({detail})", 180)
        raise _ModelError("opencode boş yanıt", 180)

    def _call(self, p: Provider, model: str, messages, max_tokens, temperature) -> str:
        if p.id == "opencode":
            return self._call_opencode(p, model, messages, max_tokens, temperature)
        return self._call_http(p, model, messages, max_tokens, temperature)

    def _call_http(self, p: Provider, model: str, messages, max_tokens, temperature) -> str:
        url = f"{p.base_url}/chat/completions"
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if p.key():
            headers["Authorization"] = f"Bearer {p.key()}"
        try:
            r = self.session.post(url, headers=headers, json=body, timeout=(10, 75))
        except requests.Timeout:
            raise _ModelError("zaman aşımı", 120)
        except requests.RequestException as e:
            raise _ModelError(f"bağlantı hatası: {e}", 180)

        if r.status_code != 200:
            raise self._classify(p, model, r)

        try:
            data = r.json()
            choice = data["choices"][0]
            msg = choice.get("message", {})
            content = msg.get("content")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            text = (content or msg.get("reasoning_content") or "").strip()
        except Exception as e:
            raise _ModelError(f"geçersiz yanıt: {e}", 300)

        if not text:
            raise _ModelError("boş yanıt (token/limit)", 300)
        return text

    def _classify(self, p: Provider, model: str, r: requests.Response) -> "_ModelError":
        body = (r.text or "")[:600]
        low = body.lower()
        code = r.status_code

        if code == 429:
            lowered = low
            if any(k in lowered for k in ("per day", "daily", "tokens per day", "quota exceeded", "insufficient")):
                wait = 3600
            elif "per minute" in lowered or "rate limit" in lowered:
                wait = 65
            else:
                wait = 600
            ra = r.headers.get("retry-after")
            if ra and ra.isdigit():
                wait = max(wait, int(ra))
            m = re.search(r"retry after (\d+)s", low)
            if m:
                wait = max(15, int(m.group(1)) + 2)
            return _ModelError(f"kota/limit ({code})", wait)
        if code == 402:
            m = re.search(r"retry after (\d+)s", low)
            if "free_limit" in low or "rate limit" in low or m:
                wait = int(m.group(1)) + 2 if m else 32
                return _ModelError("ücretsiz limit (yavaş mod)", max(15, wait))
            return _ModelError("kredi bitti", 21600)
        if code in (401, 403):
            return _ModelError(f"anahtar geçersiz ({code})", 21600)
        if code == 404:
            return _ModelError(f"model yok ({code})", 43200, refresh=True)
        if code == 400:
            if any(k in low for k in ("model", "not found", "decommission", "does not exist", "unsupported")):
                return _ModelError(f"model desteklenmiyor ({code})", 43200, refresh=True)
            return _ModelError(f"geçersiz istek ({code})", 300)
        if code >= 500:
            return _ModelError(f"sunucu hatası ({code})", 180)
        return _ModelError(f"HTTP {code}", 300)

    # ---------- durum ----------
    def status_text(self) -> str:
        lines = ["Hiçbir ücretsiz model kullanılabilir değil."]
        for p in self.s.providers:
            if not p.usable():
                lines.append(f"  • {p.id}: API anahtarı yok")
                continue
            cooling = [m for m in (p.models or ["?"]) if self._cooling(p.id, m)]
            total = len(p.models) or len(self._discovery.get(p.id, []))
            lines.append(f"  • {p.id}: {total - len(cooling)}/{total} model hazır")
        return "\n".join(lines)

    def status_rows(self) -> list[dict]:
        rows = []
        for p in self.s.providers:
            models = p.models or self._discovery.get(p.id, [])
            if not models:
                rows.append({
                    "provider": p.id,
                    "model": "(model listesi boş)",
                    "key": bool(p.key()),
                    "keyless": p.keyless,
                    "cooldown_s": 0,
                    "reason": "",
                })
                continue
            for m in models:
                entry = self.cooldowns.get(self._ck(p.id, m))
                left = 0
                if entry and entry.get("until", 0) > time.time():
                    left = int(entry["until"] - time.time())
                rows.append(
                    {
                        "provider": p.id,
                        "model": m,
                        "key": bool(p.key()),
                        "keyless": p.keyless,
                        "cooldown_s": left,
                        "reason": (entry or {}).get("reason", ""),
                    }
                )
        return rows


class _ModelError(Exception):
    def __init__(self, reason: str, cooldown: int, refresh: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.cooldown = cooldown
        self.refresh = refresh
