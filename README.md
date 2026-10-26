# Metro — CachyOS için her zaman dinleyen ücretsiz sesli asistan

"Hey Metro" dediğinde uyanan, seninle Türkçe konuşan, bilgisayarında iş yapan bir
Jarvis. **Tamamen ücretsiz**: yerel model çalıştırmaz, GPU istemez, tek ihtiyacı
ücretsiz API anahtarıdır.

## Nasıl çalışır?

```
mikrofon ──► Vosk (yerel, %1 CPU) ──► "metro" duyuldu mu?
                                          │ evet
                                          ▼
                              kayıt + sessizlik algılama (VAD)
                                          ▼
                    Groq Whisper ──► Gemini ──► Vosk  (ses → yazı)
                                          ▼
              Ajan: hızlı yerel komutlar veya ücretsiz LLM zinciri
                                          ▼
                     edge-tts (Emel/Ahmet) ──► hoparlör
```

- **Uyandırma & Metro Code Konsolu**: Vosk Türkçe modeli, çevrimdışı, hafif. "metro", "hey metro",
  "metre" gibi yakın telaffuzları da yakalar. Uyandığında Claude Code / J.A.R.V.I.S. tarzı etkileşimli
  **Metro Code** terminal penceresi otomatik açılır; hem sesli hem yazılı aynı oturumda sohbet edebilirsin.
- **Model zinciri**: OpenCode (:free) → Groq → Gemini → OpenRouter (:free) → Cerebras → Mistral → Pollinations.
  Bir model kota/429/401 hatası verirse **o model dinlenmeye alınır**, sıradaki
  otomatik devreye girer. Bekleme süreleri `state/cooldowns.json` dosyasında
  kalıcıdır, servis yeniden başlasa da hatırlanır.
- **Sıfır gecikmeli Jarvis Sesi**: Ön-ısıtmalı (pre-warmed) WebSocket ve canlı `mpv` akışı sayesinde
  yazılı yanıt ile sesli konuşma aynı anda (0 ms farkla) başlar.
- **Model listeleri otomatik tazelenir**: sağlayıcıların `/models` uç noktası
  okunur, ölmüş modeller zincirden düşer, OpenCode ve OpenRouter'da yeni `:free` modeller
  kendiliğinden eklenir.

## Kurulum

```bash
cd ~/MetroAI
./install.sh          # venv + paketler + Türkçe uyandırma modeli
./metro setup         # ücretsiz API anahtarları + ses seçimi
./metro doctor        # her şey yerinde mi?
```

`./metro setup` sırasında şu ücretsiz anahtarlardan **en az birini** girmen yeterli:

| Sağlayıcı  | Anahtar sayfası                        | Not |
|------------|----------------------------------------|-----|
| Groq       | https://console.groq.com/keys          | En hızlı; Whisper STT de buradan |
| Gemini     | https://aistudio.google.com/apikey     | Geniş ücretsiz kota |
| OpenRouter | https://openrouter.ai/keys             | Onlarca `:free` model |
| Cerebras   | https://cloud.cerebras.ai              | Ücretsiz katman |
| Mistral    | https://console.mistral.ai/api-keys    | Ücretsiz katman |

## Kullanım

```bash
./metro                      # ön planda çalıştır (test için)
./metro chat                 # Claude Code / Jarvis tarzı etkileşimli sohbet konsolu
./metro chat -w              # ayrı bir terminal penceresinde Metro Code aç
./metro voices --set jarvis  # Jarvis ses profilini seç ve test et
./metro service install      # arka planda + oturum açılışında otomatik
./metro status               # durum + model zinciri
./metro service logs -f      # canlı log
```

### Sesli komut örnekleri

- "Hey Metro, saat kaç?"
- "Metro, tarayıcıyı aç."
- "Hey Metro, sesi yüzde otuza indir."
- "Metro, YouTube'da Barış Manço çal."
- "Metro, yarın için hava nasıl?"
- "Metro, ekran görüntüsü al."
- "Metro, 10 dakika sonra çayı hatırlat."
- "Metro, not al: yarın fatura ödenecek."
- "Metro, diskte ne kadar yer var?"
- "Metro, uyu." (15 dakika sessize alır)

Tehlikeli işlemler (kabuk komutu, kapatma) **sesli onay** ister: "onaylıyor musun?"
sorusuna "evet" demen gerekir.

### Metin modu (mikrofonsuz test)

```bash
./metro ask "bugün günlerden ne"
./metro ask "sesi kıs" -s        # -s: yanıtı seslendir
./metro say "Merhaba, ben Metro."
./metro listen                   # mikrofonu 12 sn dinle, yazıya çevir
./metro check                    # tüm sağlayıcıları canlı test et
```

## Ayarlar

Her şey `config.toml` içinde. Sık kullanılanlar:

| Ayar | Açıklama |
|------|----------|
| `assistant.wake_words` | Uyandırma kelimeleri, virgülle: `["metro", "metre"]` |
| `assistant.voice` | `tr-TR-EmelNeural` / `tr-TR-AhmetNeural` (`./metro voices`) |
| `assistant.ack_phrase` | Uyandıktan sonra söylenecek cümle (boş = sadece çan) |
| `assistant.confirm_dangerous` | Tehlikeli işlemlerde onay (`false` yapma!) |
| `audio.input_device` | `./metro devices` ile bul, numara veya isim parçası |
| `audio.silence_sec` | Komutun bittiğini anlama süresi (gürültülü ortamda artır) |
| `audio.noise_multiplier` | Konuşma eşiği: düşük = hassas, yüksek = az yanlış tetikleme |
| `providers[].api_key` | Anahtarlar burada, dosya izni `600` |

Ortam değişkeniyle de anahtar verebilirsin: `GROQ_API_KEY`, `GEMINI_API_KEY`,
`OPENROUTER_API_KEY`, `CEREBRAS_API_KEY`, `MISTRAL_API_KEY`.

## Araçlar (agent'ın yapabildikleri)

`open_app`, `open_url`, `web_search`, `play_youtube`, `volume`, `media`,
`brightness`, `screenshot`, `lock_screen`, `system_info`, `weather`, `time_now`,
`clipboard_get`, `clipboard_set`, `notify`, `note_save`, `note_list`, `timer`,
`sleep`, `shell` (onaylı), `power` (onaylı).

Yeni araç eklemek: `metroai/tools.py` içine `@tool(...)` dekoratörüyle fonksiyon
yaz. LLM'e otomatik tanıtılır.

## Sorun giderme

| Belirti | Çözüm |
|---------|-------|
| "Hey Metro" algılanmıyor | `./metro listen` ile mikrofonu test et; `audio.input_device` değiştir; gürültülü ortamda `silence_sec` düşür |
| Kendi sesine uyanıyor | Çan/onay cümlesini kapat: `chime = false`, `ack_phrase = ""` |
| "Hiçbir ücretsiz modele ulaşamıyorum" | `./metro check`; kota dolduysa birkaç dakika bekle veya başka sağlayıcı anahtarı ekle |
| Ses gelmiyor | `mpv` kurulu mu? `./metro say "test"` |
| Servis başlamıyor | `./metro service logs`; genelde eksik anahtar veya mikrofon izni |
| Mikrofon servis altında sessiz | `systemctl --user restart pipewire` sonra `./metro service restart` |

## Dosya düzeni

```
MetroAI/
├── metro              # başlatıcı
├── install.sh         # kurulum
├── config.toml        # ayarlar + anahtarlar (chmod 600)
├── metroai/           # kod
│   ├── cli.py         # komutlar
│   ├── daemon.py      # ana döngü
│   ├── wake.py        # Vosk uyandırma
│   ├── stt.py         # Whisper/Gemini/Vosk
│   ├── tts.py         # edge-tts
│   ├── llm.py         # ücretsiz model zinciri
│   ├── agent.py       # niyet + araç çağrısı
│   └── tools.py       # sistem araçları
├── models/            # Vosk Türkçe model
└── state/             # loglar, bekleme süreleri, notlar, önbellek
```
