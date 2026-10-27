"""Konuşmayı yöneten ajan: hızlı yerel komutlar + LLM + araç çağrıları."""
from __future__ import annotations

import json
from pathlib import Path
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
    s = re.sub(r"\s+", " ", s)
    # Yerel Vosk modelinin Türkçe konuşmadaki yaygın ses/fonetik bozulmalarını düzelt
    s = re.sub(r"\b(sportif\s+ay(?:dan)?|sportif|spor\s+fifa|sporte\s+fay|sportif\s+ay)\b", "spotify", s)
    s = re.sub(r"\b(yutub|yutup|yutuptan|yutupdan)\b", "youtube", s)
    s = re.sub(r"\b(sarki\s+dur|sarkiyi\s+dur|sakiyi\s+durdur|sarkiyi\s+dogur)\b", "sarkiyi durdur", s)
    return s


def _detect_os_info() -> str:
    """Sistemin dağıtımını, masaüstü ortamını ve oturum türünü dinamik tespit eder."""
    import os
    distro = "Linux"
    try:
        os_rel = Path("/etc/os-release")
        if os_rel.exists():
            for line in os_rel.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("PRETTY_NAME="):
                    distro = line.split("=", 1)[1].strip('"\'')
                    break
                elif line.startswith("NAME=") and distro == "Linux":
                    distro = line.split("=", 1)[1].strip('"\'')
    except Exception:
        pass
    de = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or ""
    session_type = os.environ.get("XDG_SESSION_TYPE") or ""
    details = [distro]
    if de:
        details.append(de)
    if session_type:
        details.append(session_type)
    return ", ".join(details)


VOICE_RULES = """KURALLAR (SESLİ DİNLİYOR VE CEVAPLIYORSUN):
- Kullanıcının konuştuğu dilde yanıt ver.
- Yanıtların konuşma dilinde, doğal ve akıcı olsun. Yapay cümle sınırlarına takılmadan konuyu tam ve anlaşılır şekilde aktar.
- Yanıtın sesli okunacak: karmaşık markdown tabloları, emoji yığınları veya kod blokları kullanma; metin konuşma diline uygun olsun.
- Özel bir sistem veya medya işlemi gerçekleştireceksen yanıtında araç JSON'unu kullan:
  {{"tool": "arac_adi", "args": {{...}}}}"""

TEXT_RULES = """KURALLAR (YAZILI TERMİNAL / KONSOL MODUNDASIN):
- Kullanıcının yazdığı dilde yanıt ver.
- Tam yetkili bir sistem asistanısın. Yanıtların derinlemesine, profesyonel, eksiksiz ve çözüm odaklı olsun.
- Terminal ekranındasın: kod blokları (```dil), listeler, markdown ve açıklayıcı teknik detaylar KULLANABİLİRSİN. Kodları tam ve eksiksiz yaz.
- Özel bir sistem veya masaüstü işlemi yapman gerektiğinde yanıtında araç JSON'unu kullanabilirsin:
  {{"tool": "arac_adi", "args": {{...}}}}"""

SYSTEM_TEMPLATE = """Sen {name}'sun. Kullanıcının {os_info} bilgisayarında tam yetkiye sahip yapay zeka asistanısın. Bugün {date}.

{rules}

ARAÇLAR:
{tools}

Rehber:
- Spotify'dan müzik çalmak için play_spotify, YouTube için play_youtube, internet aramaları için web_search kullan.
- Ekrandaki pencereleri, kodları veya hata pencerelerini incelemek için analyze_screen kullan.
- Sistemdeki her türlü dosya, paket, servis, terminal veya masaüstü işlemi için tam yetkilisin; shell aracını kullanabilirsin.
"""


class Agent:
    def __init__(self, settings: Settings, chain: LLMChain, ctx: tools.Ctx):
        self.s = settings
        self.chain = chain
        self.ctx = ctx
        self.history: deque[tuple[str, str]] = deque(maxlen=max(2, settings.history_turns * 2))

    # ---------- dış arayüz ----------
    def handle(self, text: str, is_voice: bool = False) -> str:
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

        messages = [{"role": "system", "content": self._system(query=text, is_voice=is_voice)}]
        for u, a in self.history:
            messages.append({"role": "user", "content": u})
            messages.append({"role": "assistant", "content": a})
        messages.append({"role": "user", "content": text})

        try:
            reply = self.chain.chat(messages)
        except NoModelAvailable as e:
            log_.error("model yok: %s", e)
            return self._offline(text)

        out = self._handle_json(reply.text, is_voice=is_voice, original_query=text)
        self.history.append((text, out))
        return out

    # ---------- LLM çıktısı ----------
    def _system(self, query: str = "", is_voice: bool = False) -> str:
        rules = VOICE_RULES if is_voice else TEXT_RULES
        base = SYSTEM_TEMPLATE.format(
            name=self.s.name,
            os_info=_detect_os_info(),
            date=datetime.now().strftime("%d.%m.%Y %H:%M"),
            rules=rules,
            tools=tools.spec_text(),
        )
        mem_ctx = memory.relevant_context(query, max_chars=200)
        if mem_ctx:
            base += f"\nKULLANICI HAFIZASI: {mem_ctx}\n"
        if self.s.custom_prompt:
            base += f"\nKULLANICI ÖZEL TALİMATI:\n{self.s.custom_prompt}\n"
        return base

    def _handle_json(self, raw: str, is_voice: bool = False, original_query: str = "") -> str:
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
                "screen": "analyze_screen",
                "ocr": "analyze_screen",
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
            tool_res = t.fn(args, self.ctx)

            # ReAct Sentezi: Bilgi sorgulayan araçlarda kullanıcının sorusuna doğrudan doğal cevap ver
            info_tools = ("weather", "system_info", "note_list", "clipboard_get", "analyze_screen")
            if name in info_tools and original_query and len(tool_res) > 3:
                try:
                    synth_messages = [
                        {"role": "system", "content": self._system(query=original_query, is_voice=is_voice)},
                        {"role": "user", "content": original_query},
                        {"role": "assistant", "content": raw},
                        {
                            "role": "user",
                            "content": (
                                f"Araç çalıştırma sonucu: {tool_res}\n"
                                f"Bu sonuca dayanarak kullanıcının sorusuna ({original_query}) doğrudan, doğal ve net bir cevap ver."
                            ),
                        },
                    ]
                    synth_reply = self.chain.chat(synth_messages, max_tokens=250)
                    if synth_reply and synth_reply.text.strip():
                        return synth_reply.text.strip()
                except Exception as ex:
                    log_.debug("ReAct sentez hatası: %s", ex)

            return tool_res
        except Exception as e:
            log_.exception("araç hatası: %s", name)
            return f"İşlem sırasında hata oldu: {e}"
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
        if has("ekrana bak", "ekrani incele", "ekranı incele", "ekranda ne var", "ekrandaki hata"):
            return tools.analyze_screen({"question": text}, self.ctx)

        # 1) Müzik / Medya Durdurma (0ms anında yerel yürütme — LLM'e göndermeden hemen yap)
        if (
            has(
                "sarkiyi durdur", "sarki durdur", "muzigi durdur", "muzik durdur",
                "sarkiyi dur", "sarki dur", "muzigi dur", "muzik dur",
                "durdur", "sarki dursun", "muzik dursun", "videoyu durdur",
                "pause", "oynatmayi durdur", "kes sesi", "calma", "çalma"
            )
            or re.fullmatch(r"(?:hey\s+metro\s+|metro\s+)?(dur|durdur|kes|yeter|sessiz)[\s!.,]*", t)
        ):
            return tools.media({"action": "pause"}, self.ctx)

        # 2) Müzik / Medya Oynatma ve Devam Ettirme
        if (
            has(
                "sarkiyi devam", "muzigi devam", "devam et", "devam ettir", "surdur",
                "sarkiyi ac", "muzigi ac", "muzigi baslat", "sarkiyi baslat", "calmaya devam"
            )
            or re.fullmatch(r"(?:hey\s+metro\s+|metro\s+)?(cal|oynat|devam|surdur)[\s!.,]*", t)
        ):
            return tools.media({"action": "play"}, self.ctx)

        # 3) Sonraki / Önceki Şarkı
        if has("sonraki sarki", "diger sarki", "sarkiyi gec", "bunu gec", "atla", "sonraki parca", "next", "sonraki"):
            return tools.media({"action": "next"}, self.ctx)
        if has("onceki sarki", "basa sar", "onceki parca", "previous", "onceki"):
            return tools.media({"action": "previous"}, self.ctx)

        # 4) Ses Seviyesi (Aç, Kıs, Kapat)
        if has("sesi ac", "sesi yukselt", "sesi arttir", "sesi artir", "sesi biraz ac"):
            return tools.volume({"action": "up"}, self.ctx)
        if has("sesi kis", "sesi azalt", "sesi dusur", "sesi biraz kis"):
            return tools.volume({"action": "down"}, self.ctx)
        if has("sesi kapat", "sesi sustur", "sessize al", "sesi kes", "sustur", "mute"):
            return tools.volume({"action": "mute"}, self.ctx)

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
