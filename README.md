<div align="center">

<img src="assets/metro-icon.svg" alt="Metro Assistant Logo" width="128" height="128" />

# ◆ Metro Assistant

**Linux için Her Zaman Dinleyen, Sıfır Maliyetli, Tam Sistem Erişimli Sesli & Yazılı Yapay Zeka Asistanı**

[![Platform](https://img.shields.io/badge/Platform-Linux%20(Wayland%20%26%20X11)-0B0D11?style=for-the-badge&logo=linux&logoColor=38BDF8)](#)
[![Python](https://img.shields.io/badge/Python-3.11%2B-0B0D11?style=for-the-badge&logo=python&logoColor=38BDF8)](#)
[![LLM](https://img.shields.io/badge/LLM-OpenCode%20%7C%20Groq%20%7C%20Gemini%20%7C%20OpenRouter-0B0D11?style=for-the-badge&logo=openai&logoColor=38BDF8)](#)
[![Voice](https://img.shields.io/badge/TTS-Multilingual%20Neural%20V2%20(0ms%20Stream)-0B0D11?style=for-the-badge)](#)

<br />

**"Hey Metro"** dediğinde veya atadığın **kısayol tuşuna** bastığında anında uyanır, ekranın en üstünde kendi konsolunu açar, konuşmanı dinler (`ESC` ile istediğin an iptal edilebilir), yanıtı **0ms gecikmeyle** hem ekrana yazar hem de doğal insan sesiyle okur ve bilgisayarındaki her işlemi **güvenli onay mekanizmasıyla** gerçekleştirir.

</div>

---

## ⚡ Tek Komutla Kurulum

Hangi klasörde olursan ol, terminali aç ve sadece şu komutu yapıştır:

```bash
curl -fsSL https://raw.githubusercontent.com/beratboomindev/MetroAI/main/install.sh | bash
```

> **Kurulum sihirbazı her şeyi otomatik halleder:**
> 1. Eksik sistem paketlerini (`python3`, `mpv`, `git`, `unzip`) tespit edip kurar.
> 2. **OpenCode CLI** kurulu değilse otomatik kurmayı teklif eder (kredi kartı/anahtar olmadan ücretsiz modeller).
> 3. **Türkçe çevrimdışı ses modelini** (`vosk-model-small-tr-0.3`) indirir.
> 4. İsteğe bağlı **Konuşma Kısayol Tuşu (Hotkey)** atamanı sağlar (tercihen boş bırakılabilir).
> 5. `metro` komutunu sistem geneline (`~/.local/bin/metro`), uygulama menüsüne (**Metro Assistant**) ve başlangıç servisine (`systemd --user`) ekler.

Zaten klonladıysan:

```bash
./install.sh
```

---

## 🖥️ Konsol Önizlemesi

```text
╭──────────────────────────────────────────────────────────────────────────────╮
│ ◆ METRO ASSISTANT v1.0.0                                             ● HAZIR │
│ ──────────────────────────────────────────────────────────────────────────── │
│ Model: opencode/deepseek-v4.1-flash-free  •  Ses: metro  •  Tuş: F8          │
│ Komut: /model • /api • /voice • /hotkey • /ayarlar • /help                   │
╰──────────────────────────────────────────────────────────────────────────────╯

● Sen (groq:whisper-large-v3): Spotify'dan Duman Senden Daha Güzel aç
◆ Metro [opencode/deepseek-v4.1-flash-free]
  Spotify'da Duman Senden Daha Güzel çalınıyor.

metro ❯ _
```

---

## 🔥 Öne Çıkan Özellikler

| Özellik | Açıklama |
| :--- | :--- |
| **🎙️ Çoklu Konuşmacı Ses Tanıma (AGC)** | Otomatik Kazanç Kontrolü (AGC), kelime dağarcığı kısıtlı Vosk grameri ve fonetik tolerans sayesinde farklı ses tonlarını ve uzaklıkları zahmetsizce algılar. |
| **⌨️ İsteğe Bağlı Kısayol Tuşu & `ESC` İptali** | Kurulumda veya `/hotkey` ile istediğin bir tuşu (`F8`, `F9`, `Pause` vb.) konuşma tuşu yapabilirsin. Metro seni dinlerken **`ESC`** tuşuna bastığında dinlemeyi anında keser. |
| **📌 Her Zaman Üstte (Always-on-Top)** | KDE Plasma (Wayland/X11) KWin kuralı ile **Metro Assistant** penceresi diğer pencerelerin arkasına düşmez; `/top` komutuyla istediğin an açıp kapatabilirsin. |
| **🧠 Ücretsiz Çoklu-LLM Zinciri & Elle Seçim** | **OpenCode ücretsiz modelleri** 1. önceliklidir. Groq, Gemini 2.5, OpenRouter (`:free`), Cerebras, Mistral ve Pollinations arasında kota doldukça otomatik geçiş yapar veya `/model` ile istediğin modele kilitlenir. |
| **🔊 0ms Senkronize Neural V2 Ses (TTS)** | LLM düşünürken arka planda WebSocket (IPv4) bağlantısını ısıtır, ses paketlerini doğrudan `mpv` stdin'ine akıtır ve ilk ses dalgası çıktığı salisede metni ekrana basar. |
| **🛡️ Tam Sistem Erişimi + Güvenlik Kilidi** | Spotify'da doğrudan şarkı başlatır (`DBus MPRIS`), YouTube açar, ses/medya/zamanlayıcı yönetir, terminal komutu çalıştırır. **Yıkıcı komutlar bloklanır**, sistem/güç işlemleri ise **kullanıcıdan sesli veya yazılı onay (`evet/onaylıyorum`) almadan asla çalışmaz.** |

---

## 🛠️ Konsol Komutları ve Özelleştirme (`metro chat`)

Metro Assistant penceresi açıkken aşağıdaki slash komutlarıyla her şeyi anında özelleştirebilirsin:

| Komut | Kısayol | Açıklama |
| :--- | :--- | :--- |
| `/model` | `/m` | Numaralı listeden **aktif modeli elle seç**, yeni model ekle (`/model ekle groq/...`), sil veya `/model auto` ile otomatik zincire dön. |
| `/api` | `/p` | Sağlayıcı (Provider) bağla, API anahtarı gir veya OpenCode ücretsiz modellerini tek tuşla çek. |
| `/pull` | — | Bağlı tüm sağlayıcılardan en güncel model listesini API üzerinden çek. |
| `/voice` | `/v` | 8 farklı Multilingual Neural V2 ses profilini numaralı menüden seç ve canlı test et (`metro`, `brian`, `florian`, `seraphina` vb.). |
| `/hotkey` | `/tus` | Konuşmaya başlamak için anahtar tuş ata (`/hotkey f8`) veya kaldır (`/hotkey sil`). |
| `/wake <kelimeler>` | `/kelime` | Uyandırma kelimelerini anında değiştir (Örn: `/wake metro, hey metro, asistan`). |
| `/prompt <talimat>` | `/persona` | Metro'nun konuşma tarzını ve kişiliğini özelleştir (Sıfırlamak için `/prompt sıfırla`). |
| `/komut` | `/alias` | **Kendi özel slash komutlarını oluştur!** (Örn: `/komut ekle /rock Spotify'dan Türkçe rock çal`). |
| `/ayarlar` | `/set` | Tüm sistem, ses, model, tuş ve güvenlik ayarlarını tek bir numaralı menüden yönet. |
| `/top` | `/pin` | Pencereyi ekranda **Her Zaman Üstte** tutma modunu aç / kapat. |
| `/speak` | — | Yazılı sohbette sesli yanıt okumayı aç / kapat. |
| `/listen` | `/mic` | Mikrofondan hemen sesli komut dinle (`ESC` ile iptal edilebilir). |
| `/sh <komut>` | — | Sohbet içinden doğrudan kabuk (shell) komutu çalıştır. |
| `/clear` | — | Sohbet geçmişini ve ekranı temizle. |

---

## 🎙️ Ses Profilleri (Multilingual Neural V2)

Ses profilini sohbet içinden `/voice` yazarak veya terminalden `./metro voices --set <isim>` ile değiştirebilirsin:

- **`metro`** *(Varsayılan)* — Derin, tok ve karizmatik erkek asistan sesi (`Florian V2 + Prosody`)
- **`brian`** — Modern, sakin ve akıcı erkek sesi (`Brian V2`)
- **`florian`** — Doğal ve net Avrupa erkek sesi (`Florian V2`)
- **`andrew`** — Sıcak ve güven veren erkek sesi (`Andrew V2`)
- **`seraphina`** — Çok doğal ve gerçekçi kadın asistan sesi (`Seraphina V2`)
- **`ava`** — Sıcak, modern ve akıcı kadın sesi (`Ava V2`)
- **`ahmet` / `emel`** — Standart Türkçe erkek / kadın sesleri

---

## 💻 CLI Kullanımı

```bash
metro chat               # Etkileşimli Metro Assistant konsolunu aç
metro chat -w            # Yeni bir 'Her Zaman Üstte' penceresinde aç
metro ask "saat kaç"     # Tek seferlik yazılı soru sor (-s ile sesli yanıt)
metro say "Merhaba"      # Metni aktif ses profiliyle seslendir
metro voices             # Ses profillerini listele ve test et
metro status             # Servis ve LLM model zinciri durumunu göster
metro doctor             # Mikrofon, model, TTS ve paket sağlığını kontrol et
metro service restart    # Arka plan ses servisini yeniden başlat
```

---

## 📂 Proje Mimarisi

```text
MetroAI/
├── metro                # Ana çalıştırılabilir CLI giriş noktası
├── install.sh           # Tek komutla evrensel kurulum betiği
├── config.example.toml  # Örnek yapılandırma dosyası
├── assets/
│   ├── metro-icon.svg   # Vektörel uygulama logosu
│   └── metro-icon.png   # Masaüstü uygulama ikonu
└── metroai/
    ├── daemon.py        # 7/24 arka plan ses/tuş dinleme döngüsü
    ├── tui.py           # Her zaman üstte duran etkileşimli terminal arayüzü
    ├── wake.py          # Çoklu konuşmacı AGC + Vosk + fonetik uyandırma motoru
    ├── keys.py          # Global kısayol tuşu ve ESC iptal yakalayıcı (/dev/input + tty)
    ├── llm.py           # OpenCode / Groq / Gemini / OpenRouter yedekli model zinciri
    ├── tts.py           # Pre-warmed WebSocket + mpv stdin 0ms Neural V2 TTS
    ├── stt.py           # Groq Whisper -> Gemini -> Yerel Vosk konuşma tanıma
    ├── agent.py         # Konuşma bağlamı ve araç yönlendirici ajan
    └── tools.py         # Spotify (MPRIS), YouTube, medya, sistem ve onaylı shell araçları
```

---

<div align="center">
  <sub>Developed by <a href="https://github.com/beratboomindev">beratboomindev</a> • Powered by OpenCode & Multilingual Neural V2</sub>
</div>
