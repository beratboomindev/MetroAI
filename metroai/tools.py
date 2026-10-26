"""Metro'nun sistem araçları (agent'ın elleri)."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from . import log
from .settings import STATE_DIR

log_ = log.get()
NOTES = STATE_DIR / "notes.md"


class Ctx(Protocol):
    def confirm(self, question: str, detail: str = "") -> bool: ...
    def say(self, text: str) -> None: ...
    def sleep_minutes(self, minutes: float) -> None: ...


@dataclass
class Tool:
    name: str
    desc: str
    params: dict[str, str]
    fn: Callable[[dict, Ctx], str]
    dangerous: bool = False


TOOLS: dict[str, Tool] = {}


def tool(name: str, desc: str, params: dict[str, str], dangerous: bool = False):
    def wrap(fn):
        TOOLS[name] = Tool(name, desc, params, fn, dangerous)
        return fn

    return wrap


def spec_text() -> str:
    lines = []
    for t in TOOLS.values():
        args = ", ".join(f"{k}: {v}" for k, v in t.params.items()) or "argüman yok"
        lines.append(f"- {t.name}({args}) → {t.desc}")
    return "\n".join(lines)


# ---------- yardımcılar ----------
def _fold(s: str) -> str:
    s = s.lower()
    for a, b in (("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c"), ("â", "a")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def _spawn(cmd: list[str] | str) -> bool:
    try:
        if isinstance(cmd, str):
            subprocess.Popen(
                cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        else:
            subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        return True
    except Exception as e:
        log_.warning("başlatılamadı %s: %s", cmd, e)
        return False


def _run(cmd: list[str], timeout: float = 15) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or r.stderr).strip()
    except FileNotFoundError:
        return 127, f"{cmd[0]} bulunamadı"
    except subprocess.TimeoutExpired:
        return 124, "zaman aşımı"
    except Exception as e:
        return 1, str(e)


APP_ALIASES: dict[str, list[str]] = {
    "tarayici": ["cachy-browser", "firefox", "chromium", "brave", "google-chrome-stable"],
    "browser": ["cachy-browser", "firefox", "chromium"],
    "terminal": ["konsole", "ghostty", "kitty", "alacritty", "foot"],
    "dosya": ["dolphin", "nautilus", "thunar", "pcmanfm"],
    "dosya yoneticisi": ["dolphin", "nautilus", "thunar"],
    "ayarlar": ["systemsettings", "systemsettings5", "gnome-control-center"],
    "kod": ["code", "codium", "kate", "gedit", "kdevelop"],
    "editor": ["kate", "code", "gedit"],
    "muzik": ["spotify", "elisa", "amberol", "audacious"],
    "video": ["vlc", "smplayer", "mpv"],
    "hesap makinesi": ["kcalc", "gnome-calculator"],
    "takvim": ["korganizer", "gnome-calendar"],
    "ekran goruntusu": ["spectacle"],
    "sistem monitoru": ["plasma-systemmonitor", "gnome-system-monitor"],
}

DESKTOP_DIRS = [
    Path("/usr/share/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
    Path.home() / ".local/share/applications",
]


def _desktop_exec(query: str) -> str | None:
    q = _fold(query)
    best: tuple[int, str] | None = None
    for d in DESKTOP_DIRS:
        if not d.exists():
            continue
        for f in d.glob("*.desktop"):
            try:
                txt = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if "NoDisplay=true" in txt:
                continue
            names = re.findall(r"^(?:Name|GenericName|Keywords)(?:\[[^\]]+\])?=(.+)$", txt, re.M)
            exec_line = re.search(r"^Exec=(.+)$", txt, re.M)
            if not exec_line:
                continue
            score = 0
            for n in names:
                nn = _fold(n)
                if nn == q:
                    score = max(score, 100)
                elif q in nn:
                    score = max(score, 60)
                elif nn in q and len(nn) > 3:
                    score = max(score, 40)
            if score and (best is None or score > best[0]):
                cmd = exec_line.group(1)
                cmd = re.sub(r"@[^ ]*|%[fFuUdDnNickvm]", "", cmd).strip()
                best = (score, cmd)
    return best[1] if best else None


def _which_any(names: list[str]) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return n
    return None


# ---------- araçlar ----------
@tool("open_app", "Uygulama açar", {"app": "uygulama adı, örn. tarayıcı, terminal, spotify"})
def open_app(args: dict, ctx: Ctx) -> str:
    raw = str(args.get("app", "")).strip()
    if not raw:
        return "Hangi uygulamayı açacağımı söylemedin."
    key = _fold(raw)

    binary = _which_any(APP_ALIASES.get(key, []))
    if binary and _spawn([binary]):
        return f"{raw} açılıyor."
    if shutil.which(raw) and _spawn([raw]):
        return f"{raw} açılıyor."

    desktop = _desktop_exec(raw)
    if desktop and _spawn(desktop):
        return f"{raw} açılıyor."

    return f"{raw} adında bir uygulama bulamadım."


@tool("open_url", "Tarayıcıda adres açar", {"url": "tam adres, örn. https://..."})
def open_url(args: dict, ctx: Ctx) -> str:
    url = str(args.get("url", "")).strip()
    if not url:
        return "Adres belirtilmedi."
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if _spawn(["xdg-open", url]):
        return "Adresi açtım."
    return "Tarayıcı açılamadı."


@tool("web_search", "İnternette arama yapar", {"query": "aranacak söz"})
def web_search(args: dict, ctx: Ctx) -> str:
    q = str(args.get("query", "")).strip()
    if not q:
        return "Ne arayacağımı söylemedin."
    import urllib.parse

    url = "https://duckduckgo.com/?q=" + urllib.parse.quote(q)
    _spawn(["xdg-open", url])
    return f"{q} için arama açıldı."


@tool("close_app", "Çalışan bir uygulamayı kapatır", {"app": "kapatılacak uygulama adı, örn. spotify, firefox, discord"})
def close_app(args: dict, ctx: Ctx) -> str:
    raw = str(args.get("app", "")).strip()
    if not raw:
        return "Hangi uygulamayı kapatacağımı söylemedin."
    key = _fold(raw)
    candidates = APP_ALIASES.get(key, [raw.lower()])
    for name in candidates:
        rc, _ = _run(["pkill", "-f", name])
        if rc == 0:
            return f"{raw} kapatıldı."
    return f"Açık bir {raw} uygulaması bulamadım."


def _spotify_find_track(query: str) -> tuple[str | None, str]:
    """API anahtarı gerektirmeden şarkının Spotify Track ID'sini ve başlığını bulur."""
    import requests
    import urllib.parse

    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"}
    track_id: str | None = None
    try:
        r = requests.post(
            "https://lite.duckduckgo.com/lite/",
            data={"q": f"open.spotify.com/track {query}"},
            headers=headers,
            timeout=6,
        )
        txt = urllib.parse.unquote(r.text)
        matches = re.findall(r"open\.spotify\.com/(?:intl-[a-z-]+/)?track/([A-Za-z0-9]{22})", txt)
        if matches:
            track_id = matches[0]
    except Exception as e:
        log_.debug("spotify arama hatası: %s", e)

    if not track_id:
        return None, query

    title = query
    try:
        oe = requests.get(
            f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{track_id}",
            timeout=4,
        ).json()
        if oe.get("title"):
            title = oe["title"]
    except Exception:
        pass
    return track_id, title


def _spotify_dbus_open(uri: str) -> bool:
    rc, _ = _run(
        [
            "dbus-send",
            "--print-reply",
            "--dest=org.mpris.MediaPlayer2.spotify",
            "/org/mpris/MediaPlayer2",
            "org.mpris.MediaPlayer2.Player.OpenUri",
            f"string:{uri}",
        ],
        timeout=5,
    )
    return rc == 0


@tool("play_spotify", "Spotify masaüstü uygulamasında şarkı/sanatçı arayıp doğrudan çalar", {"query": "şarkı veya sanatçı adı"})
def play_spotify(args: dict, ctx: Ctx) -> str:
    q = str(args.get("query", "")).strip()
    if not q:
        return "Spotify'da ne çalacağımı söylemedin."

    track_id, title = _spotify_find_track(q)
    uri = f"spotify:track:{track_id}" if track_id else f"spotify:search:{q}"

    # Spotify zaten açıksa doğrudan DBus OpenUri ile anında çal
    if _spotify_dbus_open(uri):
        return f"Spotify'da {title} çalınıyor."

    # Spotify kapalıysa önce başlat, DBus'a gelmesini bekle ve çal
    if shutil.which("spotify"):
        _spawn(["spotify", f"--uri={uri}"])
        for _ in range(16):
            time.sleep(0.5)
            if _spotify_dbus_open(uri):
                return f"Spotify açıldı, {title} çalınıyor."
        return f"Spotify açılıyor ve {title} başlatılıyor."

    # Masaüstü uygulaması yoksa web üzerinden aç
    web_url = f"https://open.spotify.com/track/{track_id}" if track_id else f"https://open.spotify.com/search/{q}"
    _spawn(["xdg-open", web_url])
    return f"Spotify web üzerinde {title} açıldı."


@tool("play_youtube", "YouTube'da video/müzik bulup doğrudan oynatır", {"query": "video veya şarkı adı"})
def play_youtube(args: dict, ctx: Ctx) -> str:
    q = str(args.get("query", "")).strip()
    if not q:
        return "Ne çalacağımı söylemedin."
    import urllib.parse

    ytdlp = shutil.which("yt-dlp") or str(Path.home() / ".local/bin/yt-dlp")
    if Path(ytdlp).exists():
        rc, vid = _run([ytdlp, "--get-id", "--no-playlist", f"ytsearch1:{q}"], timeout=8)
        vid = vid.strip().splitlines()[0] if rc == 0 and vid.strip() else ""
        if vid and len(vid) == 11:
            _spawn(["xdg-open", f"https://www.youtube.com/watch?v={vid}"])
            return f"YouTube'da {q} çalınıyor."

    _spawn(["xdg-open", "https://www.youtube.com/results?search_query=" + urllib.parse.quote(q)])
    return f"YouTube'da {q} açıldı."


@tool("volume", "Ses seviyesini ayarlar", {"action": "up|down|mute|set", "value": "set için 0-100"})
def volume(args: dict, ctx: Ctx) -> str:
    action = str(args.get("action", "toggle")).lower()
    value = args.get("value")
    if action == "set" and value is not None:
        try:
            v = max(0, min(150, int(value)))
        except Exception:
            return "Ses değerini anlayamadım."
        rc, _ = _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{v}%"])
        return f"Ses %{v} yapıldı." if rc == 0 else "Sesi ayarlayamadım."
    if action in ("up", "arttir", "ac"):
        _run(["wpctl", "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", "5%+"])
        return "Sesi artırdım."
    if action in ("down", "azalt", "kis"):
        _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"])
        return "Sesi azalttım."
    if action in ("mute", "sessiz"):
        rc, out = _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"])
        muted = "MUTED" in _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])[1]
        return "Sesi kapattım." if muted else "Sesi açtım."
    return "Ses komutunu anlayamadım."


@tool("media", "Müzik/video oynatıcıyı kontrol eder", {"action": "play|pause|toggle|next|previous|stop"})
def media(args: dict, ctx: Ctx) -> str:
    action = str(args.get("action", "toggle")).lower()
    mapping = {
        "play": ["playerctl", "play"],
        "pause": ["playerctl", "pause"],
        "toggle": ["playerctl", "play-pause"],
        "next": ["playerctl", "next"],
        "previous": ["playerctl", "previous"],
        "stop": ["playerctl", "stop"],
    }
    cmd = mapping.get(action)
    if not cmd:
        return "Medya komutunu anlayamadım."
    rc, out = _run(cmd)
    if rc != 0:
        return "Şu an çalan bir şey yok."
    return {
        "play": "Çalıyor.",
        "pause": "Durdurdum.",
        "toggle": "Tamam.",
        "next": "Sonraki parça.",
        "previous": "Önceki parça.",
        "stop": "Kapattım.",
    }[action]


@tool("brightness", "Ekran parlaklığını ayarlar", {"action": "up|down|set", "value": "set için 0-100"})
def brightness(args: dict, ctx: Ctx) -> str:
    action = str(args.get("action", "up")).lower()
    if action == "set" and args.get("value") is not None:
        rc, _ = _run(["brightnessctl", "set", f"{int(args['value'])}%"])
        return "Parlaklık ayarlandı." if rc == 0 else "Parlaklığı ayarlayamadım."
    rc, _ = _run(["brightnessctl", "set", "10%+" if action == "up" else "10%-"])
    return "Parlaklık değişti." if rc == 0 else "Parlaklığı değiştiremiyorum."


@tool("screenshot", "Ekran görüntüsü alır", {})
def screenshot(args: dict, ctx: Ctx) -> str:
    out_dir = Path.home() / "Pictures" / "Metro"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"metro-{datetime.now():%Y%m%d-%H%M%S}.png"
    rc, msg = _run(["spectacle", "-b", "-n", "-f", "-o", str(path)], timeout=25)
    if path.exists():
        _run(["notify-send", "Metro", f"Ekran görüntüsü: {path.name}"])
        return "Ekran görüntüsünü kaydettim."
    rc, msg = _run(["import", "-window", "root", str(path)], timeout=20)
    if path.exists():
        return "Ekran görüntüsünü kaydettim."
    return "Ekran görüntüsü alamadım."


@tool("lock_screen", "Ekranı kilitler", {})
def lock_screen(args: dict, ctx: Ctx) -> str:
    rc, _ = _run(["loginctl", "lock-session"])
    return "Ekranı kilitledim." if rc == 0 else "Kilitleyemedim."


@tool("system_info", "Sistem bilgisi verir", {"what": "cpu|ram|disk|battery|uptime|all"})
def system_info(args: dict, ctx: Ctx) -> str:
    what = str(args.get("what", "all")).lower()
    parts: list[str] = []

    def cpu() -> str:
        load = os.getloadavg()[0]
        return f"İşlemci yükü {load:.1f}"

    def ram() -> str:
        try:
            mem = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                k, v = line.split(":", 1)
                mem[k] = int(v.strip().split()[0])
            total = mem["MemTotal"] / 1024 / 1024
            avail = mem["MemAvailable"] / 1024 / 1024
            return f"Bellek {total - avail:.1f} gigabayt dolu, {total:.0f} gigabaytın"
        except Exception:
            return "Bellek bilgisi alınamadı"

    def disk() -> str:
        u = shutil.disk_usage("/")
        return f"Diskte {u.free / 2**30:.0f} gigabayt boş"

    def battery() -> str:
        for bat in sorted(Path("/sys/class/power_supply").glob("BAT*")):
            try:
                cap = (bat / "capacity").read_text().strip()
                status = (bat / "status").read_text().strip()
                return f"Pil yüzde {cap}, {status.lower()}"
            except Exception:
                continue
        return "Pil bulunamadı"

    def uptime() -> str:
        secs = float(Path("/proc/uptime").read_text().split()[0])
        h, m = int(secs // 3600), int((secs % 3600) // 60)
        return f"Sistem {h} saat {m} dakikadır açık"

    fn = {"cpu": cpu, "ram": ram, "disk": disk, "battery": battery, "uptime": uptime}
    if what in fn:
        return fn[what]()
    parts = [cpu(), ram(), disk(), battery(), uptime()]
    return ". ".join(parts) + "."


@tool("weather", "Hava durumunu söyler", {"city": "şehir adı, boşsa konum"})
def weather(args: dict, ctx: Ctx) -> str:
    city = str(args.get("city", "")).strip()
    target = city.replace(" ", "+") if city else ""
    try:
        import requests

        r = requests.get(
            f"https://wttr.in/{target}?format=%l:+%c+%t,+hissedilen+%f&lang=tr",
            timeout=12,
            headers={"User-Agent": "curl"},
        )
        txt = (r.text or "").strip()
        if r.status_code == 200 and txt and "<" not in txt:
            return txt
    except Exception as e:
        log_.debug("hava durumu hatası: %s", e)
    return "Hava durumunu şu an alamıyorum."


@tool("time_now", "Saati ve tarihi söyler", {})
def time_now(args: dict, ctx: Ctx) -> str:
    now = datetime.now()
    aylar = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
             "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
    gunler = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
    return f"{now:%H:%M}. Bugün {now.day} {aylar[now.month - 1]} {now.year}, {gunler[now.weekday()]}."


@tool("clipboard_get", "Panodaki metni okur", {})
def clipboard_get(args: dict, ctx: Ctx) -> str:
    rc, out = _run(["wl-paste", "--no-newline"], timeout=8)
    if rc == 0 and out:
        return f"Panoda şu yazıyor: {out[:300]}"
    return "Pano boş."


@tool("clipboard_set", "Panoya metin kopyalar", {"text": "kopyalanacak metin"})
def clipboard_set(args: dict, ctx: Ctx) -> str:
    text = str(args.get("text", ""))
    if not text:
        return "Kopyalanacak metin yok."
    try:
        subprocess.run(["wl-copy"], input=text, text=True, timeout=8)
        return "Panoya kopyaladım."
    except Exception:
        return "Panoya kopyalayamadım."


@tool("notify", "Masaüstü bildirimi gösterir", {"text": "bildirim metni"})
def notify(args: dict, ctx: Ctx) -> str:
    text = str(args.get("text", "")).strip()
    if not text:
        return "Bildirim metni yok."
    _run(["notify-send", "Metro", text], timeout=6)
    return "Bildirimi gösterdim."


@tool("note_save", "Not kaydeder", {"text": "not içeriği"})
def note_save(args: dict, ctx: Ctx) -> str:
    text = str(args.get("text", "")).strip()
    if not text:
        return "Not içeriği boş."
    NOTES.parent.mkdir(parents=True, exist_ok=True)
    with NOTES.open("a", encoding="utf-8") as f:
        f.write(f"- [{datetime.now():%Y-%m-%d %H:%M}] {text}\n")
    return "Notu kaydettim."


@tool("note_list", "Son notları okur", {})
def note_list(args: dict, ctx: Ctx) -> str:
    if not NOTES.exists():
        return "Henüz not yok."
    lines = [l.strip("- \n") for l in NOTES.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return "Henüz not yok."
    return "Son notlar: " + " | ".join(lines[-3:])


@tool("timer", "Geri sayım kurar", {"minutes": "dakika", "label": "hatırlatma metni"})
def timer(args: dict, ctx: Ctx) -> str:
    try:
        minutes = float(args.get("minutes", 0))
    except (TypeError, ValueError):
        return "Süreyi anlayamadım."
    if minutes <= 0 or minutes > 600:
        return "Geçerli bir süre söyle."
    label = str(args.get("label", "Süre doldu")).strip() or "Süre doldu"
    secs = minutes * 60

    def fire():
        time.sleep(secs)
        _run(["notify-send", "Metro", label], timeout=6)
        ctx.say(label)

    threading.Thread(target=fire, daemon=True).start()
    return f"{minutes:g} dakikalık zamanlayıcı kurdum."


@tool("sleep", "Metro'yu bir süre sessize alır", {"minutes": "dakika"})
def sleep_tool(args: dict, ctx: Ctx) -> str:
    try:
        minutes = float(args.get("minutes", 5))
    except (TypeError, ValueError):
        minutes = 5
    ctx.sleep_minutes(minutes)
    return f"{minutes:g} dakika dinleniyorum."


_FORBIDDEN_PAT = re.compile(
    r"(rm\s+-[rf]+\s+/(?:\s|$|\*)|mkfs\.|dd\s+if=.*of=/dev/sd|dd\s+if=.*of=/dev/nvme|:\(\)\{.*\})"
)


@tool(
    "shell",
    "Linux kabuğunda komut çalıştırır (her zaman onay ister)",
    {"command": "çalıştırılacak bash komutu"},
    dangerous=True,
)
def shell(args: dict, ctx: Ctx) -> str:
    cmd = str(args.get("command", "")).strip()
    if not cmd:
        return "Komut boş."
    if _FORBIDDEN_PAT.search(cmd):
        return "Bu komut sisteme kalıcı zarar verebileceği için güvenlik kilidi tarafından engellendi."
    if not ctx.confirm("Şu komutu çalıştırayım mı?", detail=cmd):
        return "Komutu iptal ettim."
    rc, out = _run(["bash", "-lc", cmd], timeout=60)
    out = out.strip().replace("\n", " ")
    if len(out) > 240:
        out = out[:240] + "…"
    if rc == 0:
        if not out or out.startswith("/"):
            return "Tamam, komutu çalıştırdım."
        return out
    return f"Komut hata verdi: {out[:180]}"


@tool(
    "power",
    "Bilgisayarı kapatır, yeniden başlatır veya uyutur (her zaman onay ister)",
    {"action": "shutdown|reboot|suspend"},
    dangerous=True,
)
def power(args: dict, ctx: Ctx) -> str:
    action = str(args.get("action", "")).lower()
    name = {"shutdown": "kapatmamı", "reboot": "yeniden başlatmamı", "suspend": "uyutmamı"}.get(action)
    if not name:
        return "Geçersiz güç komutu."
    if not ctx.confirm(f"Bilgisayarı {name} onaylıyor musun?"):
        return "İptal ettim."
    cmd = {"shutdown": ["systemctl", "poweroff"], "reboot": ["systemctl", "reboot"],
           "suspend": ["systemctl", "suspend"]}[action]
    _spawn(cmd)
    return "Tamam."
