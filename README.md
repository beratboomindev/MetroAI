<div align="center">

<img src="assets/metro-icon.svg" alt="Metro Assistant Logo" width="128" height="128" />

# ◆ Metro Assistant

**Linux için Her Zaman Dinleyen, Sıfır Maliyetli, Tam Sistem Erişimli Sesli & Yazılı Yapay Zeka Asistanı**

[![Platform](https://img.shields.io/badge/Platform-Linux%20(Wayland%20%26%20X11)-0B0D11?style=for-the-badge&logo=linux&logoColor=38BDF8)](#)
[![Python](https://img.shields.io/badge/Python-3.11%2B-0B0D11?style=for-the-badge&logo=python&logoColor=38BDF8)](#)
[![LLM](https://img.shields.io/badge/LLM-OpenCode%20%7C%20Groq%20%7C%20Gemini%20%7C%20OpenRouter-0B0D11?style=for-the-badge&logo=openai&logoColor=38BDF8)](#)
[![Voice](https://img.shields.io/badge/TTS-Multilingual%20Neural%20V2%20(0ms%20Stream)-0B0D11?style=for-the-badge)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-0B0D11?style=for-the-badge&color=38BDF8)](LICENSE)

<br />

**"Hey Metro"** dediğinde veya atadığın **kısayol tuşuna** bastığında anında uyanır, ekranın en üstünde kendi konsolunu açar, konuşmanı dinler (`ESC` ile istediğin an iptal edilebilir), yanıtı **0ms gecikmeyle** hem ekrana yazar hem de doğal insan sesiyle okur ve bilgisayarındaki her işlemi **sesli + ekranlı güvenlik onayıyla** gerçekleştirir.

</div>

---

## ⚡ Tek Komutla Kurulum

Hangi klasörde olursan ol, terminali aç ve sadece şu komutu yapıştır:

```bash
curl -fsSL https://raw.githubusercontent.com/beratboomindev/MetroAI/main/install.sh | bash
```

> **5 Adımlı Akıllı Kurulum Sihirbazı (`metro setup`):**
> 1. **Yapay Zeka & Model Seçimi:** Eksik sistem paketlerini (`python3`, `mpv`, `git`, `unzip`) ve **OpenCode CLI**'ı otomatik kurar; ücretsiz modelleri çekip varsayılan modeli seçtirir veya API anahtarı (`Groq`, `Gemini`, `OpenRouter` vb.) eklemeni sağlar.
> 2. **Ses Profili (Canlı Test):** 8 farklı Multilingual Neural V2 ses profilini dinleyerek seçmeni sağlar.
> 3. **Uyandırma Kelimeleri & Konuşma Tarzı:** Özel uyandırma kelimelerini (`metro`, `hey metro` vb.) ve asistan kişiliğini ayarlar.
> 4. **Konuşma Anahtar Tuşu (Hotkey):** İstediğin tuşu (`F8`, `F9`, `Insert` vb.) bas-konuş tuşu olarak atar (tercihen boş bırakılabilir).
> 5. **Pencere Görünümü & Sohbet Geçmişi:** **Her Zaman Üstte** pencere kuralını, masaüstü/görev çubuğu ikonunu, isteğe bağlı sohbet geçmişini ve `systemd --user` arka plan servisini otomatik yapılandırır.

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
│ Model: opencode/mimo-v2.6-flash-free  •  Ses: metro  •  Tuş: F8              │
│ Komut: /model • /api • /voice • /hotkey • /ayarlar • /help                   │
╰──────────────────────────────────────────────────────────────────────────────╯

● Sen (groq:whisper-large-v3): Hesap makinesi scripti yazıp çalıştır
◆ Metro [opencode/mimo-v2.6-flash-free]
  Şu komutu çalıştırayım mı?
  ┌─ Çalıştırılacak Komut:
  │ python3 -c "import tkinter..."
  └─ Onay için [e] Evet / [h] Hayır yazın veya sesli söyleyin:

metro ❯ _
```

---

## 🔥 Öne Çıkan Özellikler

| Özellik | Açıklama |
| :--- | :--- |
| **♻️ Tek Oturum (Session Reuse) & Otomatik Context Temizleme** | OpenCode ve diğer sağlayıcılarda her mesajda yeni sohbet açıp kendini tekrar tanıtmaz; aynı oturum üzerinden (`-s <sessionID>`) devam eder. Bağlam (context) şiştiğinde eski oturumu arka planda otomatik silip son 2 mesajın özetiyle taze oturuma geçer — arayüz ise her zaman temiz kalır. |
| **🧬 Ultra-Düşük Tokenlı Kalıcı Hafıza (`/hafiza`)** | Yerel JSON tabanlı (`state/memory.json`) sıfır-API-maliyetli hafıza motoru, konuşmalardan önemli bilgileri (isim, tercih, proje notları) otomatik yakalar ve her mesaja en fazla **~35 tokenlık** tek satır özet olarak ekler. |
| **🗂️ İsteğe Bağlı Sohbet Geçmişi (`/gecmis`)** | Sohbet geçmişi kaydını istediğin zaman açıp kapatabilirsin (`/gecmis kapat`). Kayıtlı sohbetleri numaralı listeden görüntüleyip `/gecmis <no>` ile kaldığın yerden devam edebilir veya `/yeni` ile sıfır sohbet açabilirsin. |
| **🛡️ Sesli + Ekranlı Komut Onayı (`e/h`)** | Terminal/sistem komutlarında uzun kodu sesli okumak yerine yalnızca **"Şu komutu çalıştırayım mı?"** diye sorar, komutu ekranda kutu içinde gösterir ve aynı anda hem mikrofonu dinler hem de klavyeden **`e` / `h`** yanıtını kabul eder. |
| **🎙️ Çoklu Konuşmacı Ses Tanıma (AGC)** | Otomatik Kazanç Kontrolü (AGC), kelime dağarcığı kısıtlı Vosk grameri ve fonetik tolerans sayesinde farklı ses tonlarını ve uzaklıkları zahmetsizce algılar. |
| **⌨️ Global Kısayol Tuşu & `ESC` İptali** | Kurulumda veya `/hotkey` ile istediğin bir tuşu (`F8`, `F9`, `Pause` vb.) konuşma tuşu yapabilirsin (`/dev/input` + terminal desteği). Metro seni dinlerken **`ESC`** tuşuna bastığında dinlemeyi anında keser. |
| **📌 Her Zaman Üstte (Always-on-Top)** | KDE Plasma (Wayland/X11) KWin kuralı ile **Metro Assistant** penceresi diğer pencerelerin arkasına düşmez; `/top` komutuyla anında açılıp kapatılabilir. |
| **🔊 0ms Senkronize Neural V2 Ses (TTS)** | LLM düşünürken arka planda WebSocket (IPv4) bağlantısını ısıtır, ses paketlerini doğrudan `mpv` stdin'ine akıtır ve ilk ses dalgası çıktığı salisede metni ekrana basar. |

---

## 🛠️ Konsol Komutları ve Özelleştirme (`metro chat`)

Metro Assistant penceresi açıkken aşağıdaki slash komutlarıyla her şeyi anında özelleştirebilirsin:

| Komut | Kısayol | Açıklama |
| :--- | :--- | :--- |
| `/model` | `/m` | Numaralı listeden **aktif modeli elle seç**, yeni model ekle (`/model ekle groq/...`), sil veya `/model auto` ile otomatik zincire dön. |
| `/api` | `/p` | Sağlayıcı (Provider) bağla, API anahtarı gir veya OpenCode ücretsiz modellerini tek tuşla çek. |
| `/hafiza` | `/mem` | Kalıcı hafızadaki kayıtları listele, yeni bilgi ekle (`/hafiza ekle ...`), sil veya temizle. |
| `/gecmis` | `/history` | Kaydedilen eski sohbetleri listele, `/gecmis <no>` ile yükle veya `/gecmis kapat` ile geçmiş kaydını kapat. |
| `/yeni` | `/new` | Ekranı temizle ve arka planda sıfır bir sohbet oturumu başlat. |
| `/voice` | `/v` | 8 farklı Multilingual Neural V2 ses profilini numaralı menüden seç ve canlı test et (`metro`, `brian`, `florian`, `seraphina` vb.). |
| `/hotkey` | `/tus` | Konuşmaya başlamak için anahtar tuş ata (`/hotkey f8`) veya kaldır (`/hotkey sil`). |
| `/wake <kelimeler>` | `/kelime` | Uyandırma kelimelerini anında değiştir (Örn: `/wake metro, hey metro, asistan`). |
| `/prompt <talimat>` | `/persona` | Metro'nun konuşma tarzını ve kişiliğini özelleştir (Sıfırlamak için `/prompt sıfırla`). |
| `/komut` | `/alias` | **Kendi özel slash komutlarını oluştur!** (Örn: `/komut ekle /rock Spotify'dan Türkçe rock çal`). |
| `/ayarlar` | `/set` | Tüm sistem, ses, model, tuş, hafıza, geçmiş ve güvenlik ayarlarını tek bir numaralı menüden yönet. |
| `/top` | `/pin` | Pencereyi ekranda **Her Zaman Üstte** tutma modunu aç / kapat. |
| `/speak` | — | Yazılı sohbette sesli yanıt okumayı aç / kapat. |
| `/listen` | `/mic` | Mikrofondan hemen sesli komut dinle (`ESC` ile iptal edilebilir). |
| `/sh <komut>` | — | Sohbet içinden doğrudan kabuk (shell) komutu çalıştır. |
| `/clear` | — | Ekranı temizle ve başlığı yenile. |

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
metro setup              # 5 adımlı özelleştirilmiş kurulum sihirbazını çalıştır
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
├── LICENSE              # MIT Lisansı
├── assets/
│   ├── metro-icon.svg   # Vektörel uygulama logosu (> M)
│   └── metro-icon.png   # Masaüstü ve görev çubuğu uygulama ikonu
└── metroai/
    ├── daemon.py        # 7/24 arka plan ses/tuş dinleme döngüsü
    ├── tui.py           # Her zaman üstte duran etkileşimli terminal arayüzü ve geçmiş yöneticisi
    ├── wake.py          # Çoklu konuşmacı AGC + Vosk + fonetik uyandırma motoru
    ├── keys.py          # Global kısayol tuşu ve ESC iptal yakalayıcı (/dev/input + X11 + tty)
    ├── llm.py           # Tek oturumlu OpenCode & çoklu-LLM yedekli model zinciri
    ├── memory.py        # Ultra-düşük tokenlı yerel kalıcı hafıza motoru
    ├── tts.py           # Pre-warmed WebSocket + mpv stdin 0ms Neural V2 TTS
    ├── stt.py           # Groq Whisper -> Gemini -> Yerel Vosk konuşma tanıma
    ├── agent.py         # Konuşma bağlamı, otomatik context rotasyonu ve araç yönlendirici ajan
    └── tools.py         # Spotify (MPRIS), YouTube, medya, sistem ve onaylı shell araçları
```

---

## 📄 Lisans

Bu proje [MIT Lisansı](LICENSE) altında lisanslanmıştır. Dilediğiniz gibi kullanabilir, değiştirebilir ve dağıtabilirsiniz.

---

<div align="center">
  <sub>Developed by <a href="https://github.com/beratboomindev">beratboomindev</a> • Powered by OpenCode & Multilingual Neural V2</sub>
</div>
