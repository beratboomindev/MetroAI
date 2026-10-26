"""Konuşmayı yöneten ajan: hızlı yerel komutlar + LLM + araç çağrıları."""
from __future__ import annotations

import json
import re
from collections import deque
from datetime import datetime

from . import log, memory, tools
from .llm import LLMChain, NoModelAvailable
from .settings import Settings

log_ = log.get()


def _fold(s: str) -> str:
    s = s.lower().strip()
    for a, b in (("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c"), ("â", "a")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s)


SYSTEM_PROMPT = """Sen {name}'sun. Kullanıcının CachyOS (Arch Linux, KDE Plasma, Wayland) bilgisayarında çalışan sesli yapay zeka asistanısın. Bugün {date}.

KURALLAR:
- Yanıtların konuşma dilinde, kısa ve doğal Türkçe olsun. En fazla 2 cümle.
- Yanıtın sesli okunacak: markdown, liste, emoji, kod bloğu, bağlantı KULLANMA.
- Gereksiz giriş cümlesi kurma, doğrudan cevap ver.
- Bilmediğin bir şeyi uydurma; bilmiyorsan söyle.
- Kullanıcı bilgisayarda bir işlem yapmanı isterse, düz metin yerine SADECE şu JSON'u döndür:
  {{"tool": "arac_adi", "args": {{...}}}}
  JSON dışında tek karakter bile yazma.

ARAÇLAR:
{tools}

Not:
- "Spotify'dan/Spotify'da şu şarkıyı çal" gibi isteklerde play_spotify kullan.
- "YouTube'dan çal" veya genel video isteklerinde play_youtube, "araştır/ara" isteklerinde web_search kullan.
- Sisteme tam erişimin var: diğer araçların kapsamadığı her türlü dosya, klasör, paket, pencere veya sistem işleminde shell aracını kullan.
"""


class Agent:
    def __init__(self, settings: Settings, chain: LLMChain, ctx: tools.Ctx):
        self.s = settings
        self.chain = chain
        self.ctx = ctx
        self.history: deque[tuple[str, str]] = deque(maxlen=max(2, settings.history_turns * 2))

    # ---------- dış arayüz ----------
    def handle(self, text: str) -> str:
        text = text.strip()
        if not text:
            return "Seni duyamadım."

        # 0-token yerel hafıza çıkarımı (isim, tercih, "unutma..." vb.)
        memory.auto_extract(text)

        fast = self._fast(text)
        if fast is not None:
            return fast

        # Bağlam (context) çok şiştiyse eski mesajları buda (son 2 konuşma çiftini koru)
        total_hist_chars = sum(len(u) + len(a) for u, a in self.history)
        if len(self.history) > max(4, self.s.history_turns) or total_hist_chars > 3200:
            recent = list(self.history)[-2:]
            self.history.clear()
            self.history.extend(recent)

        messages = [{"role": "system", "content": self._system(query=text)}]
        for u, a in self.history:
            messages.append({"role": "user", "content": u})
            messages.append({"role": "assistant", "content": a})
        messages.append({"role": "user", "content": text})

        try:
            reply = self.chain.chat(messages)
        except NoModelAvailable as e:
            log_.error("model yok: %s", e)
            return self._offline(text)

        out = self._handle_json(reply.text)
        self.history.append((text, out))
        return out

    # ---------- LLM çıktısı ----------
    def _system(self, query: str = "") -> str:
        base = SYSTEM_PROMPT.format(
            name=self.s.name,
            date=datetime.now().strftime("%d.%m.%Y %H:%M"),
            tools=tools.spec_text(),
        )
        mem_ctx = memory.relevant_context(query, max_chars=200)
        if mem_ctx:
            base += f"\nKULLANICI HAFIZASI: {mem_ctx}\n"
        if self.s.custom_prompt:
            base += f"\nKULLANICI ÖZEL TALİMATI:\n{self.s.custom_prompt}\n"
        return base

    def _handle_json(self, raw: str) -> str:
        payload = extract_json(raw)
        if not isinstance(payload, dict):
            stripped = raw.strip()
            if stripped.startswith("{") or stripped.startswith("```"):
                log_.warning("araç JSON'u çözülemedi: %s", stripped[:160])
                return "Bunu şu an yapamadım, farklı bir şekilde söyler misin?"
            return stripped

        name = payload.get("tool") or payload.get("name") or payload.get("action")
        args = payload.get("args") or payload.get("arguments") or payload.get("parameters") or {}
        if isinstance(name, str):
            name = {
                "run_shell": "shell",
                "bash": "shell",
                "exec": "shell",
                "terminal": "shell",
                "get_time": "time_now",
                "spotify": "play_spotify",
                "youtube": "play_youtube",
                "search": "web_search",
                "exit": "close_metro",
                "quit": "close_metro",
                "close": "close_metro",
                "close_self": "close_metro",
            }.get(name, name)
        if not isinstance(name, str) or name not in tools.TOOLS:
            return raw.strip()
        if not isinstance(args, dict):
            args = {}

        t = tools.TOOLS[name]
        log_.info("araç: %s %s", name, args)
        try:
            if t.dangerous and self.s.confirm_dangerous and name not in ("shell", "power"):
                if not self.ctx.confirm(f"{name} işlemini onaylıyor musun?"):
                    return "İptal ettim."
            return t.fn(args, self.ctx)
        except Exception as e:
            log_.exception("araç hatası: %s", name)
            return f"İşlem sırasında hata oldu: {e}"

    # ---------- yerel hızlı komutlar (token harcamaz) ----------
    def _fast(self, text: str) -> str | None:
        t = _fold(text)

        def has(*words: str) -> bool:
            return any(w in t for w in words)

        if re.fullmatch(r"(merhaba|selam|hey|alo|metro)[\s!.,]*", t):
            return "Buradayım, dinliyorum."
        if re.fullmatch(r"(tesekkurler|sagol|eline saglik)[\s!.,]*", t):
            return "Ne demek, her zaman."
        if has("saat kac", "saati soyle", "saati soyler"):
            return tools.time_now({}, self.ctx)
        if has("bugun gunlerden", "tarih ne", "hangi gun", "tarihi soyle"):
            return tools.time_now({}, self.ctx)
        if has("pil durumu", "batarya", "sarj ne kadar"):
            return tools.system_info({"what": "battery"}, self.ctx)
        if has("api bagla", "provider bagla", "api cek", "saglayici bagla", "modelleri cek"):
            return "Provider ve API bağlama menüsünü açıyorum. Metro Assistant penceresinden seçebilir veya /api yazabilirsin."
        msp = re.match(
            r"^(?:hey\s+metro\s+)?spotify(?:da|dan|'da|'dan)?\s+(.+?)\s+(?:sarkisini\s+|parcasini\s+)?(?:cal|ac|baslat|oynat)[\s!.,]*$",
            t,
        )
        if msp:
            return tools.play_spotify({"query": msp.group(1).strip()}, self.ctx)
        if re.fullmatch(
            r"(?:hey\s+metro\s+|metro\s+)?(kendini\s+kapat|kapat\s+kendini|kapan|metro\s+kapan|pencereyi\s+kapat|uygulamayi\s+kapat|asistani\s+kapat|cik|cikis|cikis\s+yap|gorusuruz)[\s!.,]*",
            t,
        ):
            return tools.close_metro({}, self.ctx)
        if re.fullmatch(r"(?:hey\s+metro\s+|metro\s+)?(uyu|dinlen|gizlen|sessize\s+gec)[\s!.,]*", t):
            self.ctx.sleep_minutes(15)
            return "Tamam, on beş dakika dinleniyorum. Beni tekrar çağırırsan buradayım."
        m = re.fullmatch(r"(?:zamanlayici|hatirlatici|timer)\s*(?:kur\s*)?(\d+)\s*(?:dakika|dk)?", t)
        if m:
            return tools.timer({"minutes": m.group(1), "label": "Süre doldu"}, self.ctx)
        return None

    def _offline(self, text: str) -> str:
        """Tüm modeller tükenince yerel olarak yapılabilecekler."""
        t = _fold(text)
        if any(w in t for w in ("hava", "sicaklik")):
            return tools.weather({}, self.ctx)
        if any(w in t for w in ("ses", "muzik", "sarki", "cal")):
            return "Şu an internete ulaşamıyorum. Ses komutları için sesi, müzik, ekran görüntüsü gibi kalıpları kullanabilirsin."
        return (
            "Şu an hiçbir ücretsiz modele ulaşamıyorum. Kota dolmuş olabilir. "
            "Birkaç dakika sonra tekrar dene ya da başka bir sağlayıcı anahtarı ekle."
        )


def extract_json(text: str) -> dict | None:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None
