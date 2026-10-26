#!/usr/bin/env bash
# Metro Assistant — Tek Komutla Otomatik Kurulum Betiği
# Her yerden çalıştırılabilir:
#   curl -fsSL https://raw.githubusercontent.com/beratboomindev/MetroAI/main/install.sh | bash
set -euo pipefail

REPO_URL="https://github.com/beratboomindev/MetroAI.git"
INSTALL_DIR="${METRO_DIR:-$HOME/MetroAI}"

echo ""
echo "╭──────────────────────────────────────────────────────╮"
echo "│  ◆ Metro Assistant — Otomatik Kurulum Başlatılıyor   │"
echo "╰──────────────────────────────────────────────────────╯"
echo ""

# 1) Eksik sistem araçlarını kontrol et ve gerekirse kur
need_pkgs=()
for cmd in git curl unzip python3 mpv; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    need_pkgs+=("$cmd")
  fi
done

if (( ${#need_pkgs[@]} > 0 )); then
  echo "[1/6] Eksik sistem paketleri kuruluyor (${need_pkgs[*]})..."
  if command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --needed --noconfirm git curl unzip python mpv libnotify
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y git curl unzip python3 python3-venv mpv libnotify-bin
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y git curl unzip python3 mpv libnotify
  fi
else
  echo "[1/6] Sistem araçları hazır."
fi

# 2) Repo dışından (curl | bash ile) çalıştırıldıysa ~/MetroAI içine klonla
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
fi

if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/requirements.txt" && -d "$SCRIPT_DIR/metroai" ]]; then
  INSTALL_DIR="$SCRIPT_DIR"
else
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    echo "[2/6] Mevcut MetroAI kurulumu güncelleniyor ($INSTALL_DIR)..."
    git -C "$INSTALL_DIR" pull --ff-only || true
  else
    echo "[2/6] MetroAI indiriliyor ($INSTALL_DIR)..."
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi
fi

cd "$INSTALL_DIR"

# 3) Python sanal ortamı ve paketler
if [[ ! -x venv/bin/python ]]; then
  echo "[3/6] Python sanal ortamı oluşturuluyor..."
  python3 -m venv venv
else
  echo "[3/6] Python sanal ortamı hazır."
fi

echo "[4/6] Python paketleri yükleniyor..."
venv/bin/python -m pip install --quiet --upgrade pip
venv/bin/python -m pip install --quiet -r requirements.txt

# 4) Türkçe uyandırma modeli
echo "[5/6] Türkçe ses tanıma modeli hazırlanıyor..."
if [[ ! -d models/vosk-model-small-tr-0.3 ]]; then
  mkdir -p models
  tmp="models/tr-model.zip"
  curl -L --fail --progress-bar -o "$tmp" \
    https://alphacephei.com/vosk/models/vosk-model-small-tr-0.3.zip
  unzip -q -o "$tmp" -d models/
  rm -f "$tmp"
fi

# 5) Hızlı Özelleştirme Sihirbazı (Model, Ses, Uyandırma Kelimesi, Kısayol Tuşu, Her Zaman Üstte)
chmod +x metro
export PATH="$HOME/.opencode/bin:$PATH"
if [[ ! -f config.toml ]]; then
  venv/bin/python -c "from metroai import settings; settings.save(settings.load())"
fi

if [[ -r /dev/tty && "${METRO_NONINTERACTIVE:-0}" != "1" ]]; then
  ./metro setup </dev/tty || true
else
  venv/bin/python -c "from metroai import settings, tui; tui.ensure_always_on_top_rule(settings.load().always_on_top)" || true
fi

# 6) Sistem entegrasyonu (PATH, Masaüstü Kısayolu ve Arka Plan Servisi)
echo "[6/6] Sistem entegrasyonu ve arka plan servisi kuruluyor..."
mkdir -p "$HOME/.local/bin"
ln -sf "$INSTALL_DIR/metro" "$HOME/.local/bin/metro"

for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
  if [[ -f "$rc" ]] && ! grep -q '.local/bin' "$rc"; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc"
  fi
done

ICON_PATH="$INSTALL_DIR/assets/metro-icon.png"
if [[ ! -f "$ICON_PATH" ]]; then
  ICON_PATH="audio-input-microphone"
fi

mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/metro-assistant.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Metro Assistant
Comment=Sesli ve Yazılı Yapay Zeka Asistanı
Exec=$INSTALL_DIR/metro chat -w
Icon=$ICON_PATH
Terminal=false
Categories=Utility;AudioVideo;
StartupNotify=false
EOF

# Arka plan servisini otomatik kur ve başlat
./metro service install >/dev/null 2>&1 || true

echo ""
echo "╭──────────────────────────────────────────────────────────────╮"
echo "│  ✓ Kurulum Tamamlandı! Metro Assistant şu an seni dinliyor.  │"
echo "╰──────────────────────────────────────────────────────────────╯"
echo ""
echo "  • Sesli çağırmak için : Sadece \"Hey Metro\" de veya atadığın tuşa bas!"
echo "  • Dinlemeyi bırakmak  : ESC tuşuna bas"
echo "  • Konsolu açmak için  : metro chat"
echo "  • Ayarları değiştirmek: metro setup  (veya konsolda /ayarlar)"
echo ""
