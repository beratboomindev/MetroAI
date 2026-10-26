"""Sıfır ekstra API çağrılı, ultra düşük token tüketimli kalıcı hafıza (Memory) sistemi.

Nasıl ucuza (neredeyse 0 token maliyetle) çalışır?
1. Otomatik Çıkarım (0 Token): Kullanıcının cümlelerinden ("benim adım ...", "favori şarkım ...",
   "unutma ...", "bana ... de") yerel regex kurallarıyla ekstra LLM çağrısı yapmadan bilgi çıkarır.
2. Anahtar Kelime Eşleşmeli Seçim (Maks ~35 Token): Her mesajda tüm hafızayı göndermek yerine,
   çekirdek profili + sadece o anki soruyla ilgili en alakalı 3 bilgiyi tek satırda (~180 karakter)
   sistem promptuna ekler.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .settings import STATE_DIR

MEMORY_FILE = STATE_DIR / "memory.json"
MAX_FACTS = 60


def _fold(s: str) -> str:
    s = s.lower().strip()
    for a, b in (("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c"), ("â", "a")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s)


def _load() -> dict:
    if not MEMORY_FILE.exists():
        return {"profile": {}, "facts": []}
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"profile": {}, "facts": []}
        data.setdefault("profile", {})
        data.setdefault("facts", [])
        return data
    except Exception:
        return {"profile": {}, "facts": []}


def _save(data: dict) -> None:
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MEMORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MEMORY_FILE)


def add_fact(text: str, category: str = "genel") -> str:
    """Hafızaya tek satırlık kısa bir bilgi ekler (tekrarları önler)."""
    clean = re.sub(r"\s+", " ", text.strip()).rstrip(".")
    if not clean or len(clean) < 2:
        return ""
    if len(clean) > 140:
        clean = clean[:140]

    data = _load()
    folded_new = _fold(clean)

    # Aynı veya çok benzer bilgi varsa güncelle
    facts: list[dict] = data["facts"]
    for item in facts:
        if _fold(item.get("text", "")) == folded_new:
            item["ts"] = int(time.time())
            _save(data)
            return clean

    facts.append({
        "text": clean,
        "cat": category,
        "ts": int(time.time()),
    })
    if len(facts) > MAX_FACTS:
        data["facts"] = facts[-MAX_FACTS:]
    _save(data)
    return clean


def set_profile(key: str, value: str) -> None:
    clean_k = key.strip().lower()
    clean_v = re.sub(r"\s+", " ", value.strip())[:80]
    if not clean_k or not clean_v:
        return
    data = _load()
    data["profile"][clean_k] = clean_v
    _save(data)


def remove_fact(index_or_query: str) -> bool:
    data = _load()
    facts: list[dict] = data.get("facts", [])
    q = index_or_query.strip()

    if q.isdigit():
        idx = int(q) - 1
        if 0 <= idx < len(facts):
            facts.pop(idx)
            _save(data)
            return True
        return False

    fq = _fold(q)
    # Profilde varsa sil
    for pk in list(data.get("profile", {}).keys()):
        if fq in _fold(pk) or fq in _fold(str(data["profile"][pk])):
            del data["profile"][pk]
            _save(data)
            return True

    before = len(facts)
    data["facts"] = [f for f in facts if fq not in _fold(f.get("text", ""))]
    if len(data["facts"]) < before:
        _save(data)
        return True
    return False


def clear_all() -> None:
    _save({"profile": {}, "facts": []})


def list_all() -> tuple[dict[str, str], list[dict]]:
    data = _load()
    return data.get("profile", {}), data.get("facts", [])


def auto_extract(user_text: str) -> str | None:
    """Kullanıcı mesajından ekstra LLM çağrısı yapmadan (0 token) kişisel bilgileri yakalar."""
    raw = user_text.strip()
    if not raw or raw.startswith("/") or len(raw) > 250:
        return None
    low = raw.lower().strip()

    # 1) İsim / Hitap yakalama
    m_name = re.search(
        r"\b(?:benim\s+ad[ıi]m|ismim|bana)\s+([a-zA-ZçğıöşüÇĞİÖŞÜ]{2,20})(?:\s+de|\s+diyebilirsin)?\b",
        raw,
        re.I,
    )
    if m_name:
        name_val = m_name.group(1).strip().capitalize()
        if name_val.lower() not in ("bir", "bu", "su", "şu", "ne", "kim", "nasil", "nasıl", "yardim", "yardım"):
            set_profile("isim", name_val)
            return f"İsim: {name_val}"

    # 2) Doğrudan "unutma / aklında tut / hatırla" kalıpları
    m_rem = re.match(
        r"^(?:hey\s+metro\s+|metro\s+)?(?:şunu\s+|bunu\s+)?(?:unutma|akl[ıi]nda\s+tut|hat[ıi]rla|haf[ıi]zana\s+kaydet)[\s:,]+(.+)$",
        raw,
        re.I,
    )
    if m_rem:
        fact = m_rem.group(1).strip()
        return add_fact(fact, "not")

    # 3) Tercih / Favori / Kişisel durum kalıpları
    patterns = [
        (r"\b(?:en\s+sevdi[ğg]im|favori)\s+([a-zA-ZçğıöşüÇĞİÖŞÜ\s]{2,25})\s+(.+)$", "tercih"),
        (r"\bben\s+(\d{1,2})\s+ya[şs][ıi]nday[ıi]m\b", "profil"),
        (r"\b([a-zA-ZçğıöşüÇĞİÖŞÜ]{2,20})['’]?(?:da|de|ta|te)\s+ya[şs][ıi]yorum\b", "konum"),
        (r"\bmesle[ğg]im\s+(.+)$", "meslek"),
    ]
    for pat, cat in patterns:
        m = re.search(pat, raw, re.I)
        if m:
            return add_fact(raw, cat)

    if any(k in low for k in ("benim favori", "çok severim", "hiç sevmem", "alerjim var")):
        return add_fact(raw, "tercih")

    return None


def relevant_context(query: str = "", max_chars: int = 200) -> str:
    """O anki konuşmaya en uygun hafıza özetini (maks ~35 token) tek satırda döndürür."""
    data = _load()
    profile: dict[str, str] = data.get("profile", {})
    facts: list[dict] = data.get("facts", [])

    if not profile and not facts:
        return ""

    parts: list[str] = []
    for k, v in profile.items():
        parts.append(f"{k.capitalize()}: {v}")

    if facts:
        q_words = {
            w[:4]
            for w in re.findall(r"[a-z0-9]+", _fold(query))
            if len(w) >= 3 and w not in ("metro", "bana", "benim", "nedir", "nasil", "icin", "olan")
        }
        scored: list[tuple[int, int, str]] = []
        for idx, item in enumerate(facts):
            txt = item.get("text", "")
            f_words = {w[:4] for w in re.findall(r"[a-z0-9]+", _fold(txt)) if len(w) >= 3}
            overlap = len(q_words & f_words)
            # Eşleşen kelimeler + güncellik sırası
            scored.append((overlap, idx, txt))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        # En alakalı 3 (veya en güncel 2) bilgiyi al
        top_count = 3 if q_words else 2
        for _, _, txt in scored[:top_count]:
            if txt not in parts:
                parts.append(txt)

    line = " | ".join(parts)
    if len(line) > max_chars:
        line = line[: max_chars - 1] + "…"
    return line
