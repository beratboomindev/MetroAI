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
  echo "[1/7] Eksik sistem paketleri kuruluyor (${need_pkgs[*]})..."
  if command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --needed --noconfirm git curl unzip python mpv libnotify
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y git curl unzip python3 python3-venv mpv libnotify-bin
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y git curl unzip python3 mpv libnotify
  fi
else
  echo "[1/7] Sistem araçları hazır."
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
    echo "[2/7] Mevcut MetroAI kurulumu güncelleniyor ($INSTALL_DIR)..."
    git -C "$INSTALL_DIR" pull --ff-only || true
  else
    echo "[2/7] MetroAI indiriliyor ($INSTALL_DIR)..."
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi
fi

cd "$INSTALL_DIR"

# 3) Python sanal ortamı ve paketler
if [[ ! -x venv/bin/python ]]; then
  echo "[3/7] Python sanal ortamı oluşturuluyor..."
  python3 -m venv venv
else
  echo "[3/7] Python sanal ortamı hazır."
fi

echo "[4/7] Python paketleri yükleniyor..."
venv/bin/python -m pip install --quiet --upgrade pip
venv/bin/python -m pip install --quiet -r requirements.txt

# 4) OpenCode kontrolü ve kurulum yardımcısı
export PATH="$HOME/.opencode/bin:$PATH"
if command -v opencode >/dev/null 2>&1; then
  echo "[5/7] OpenCode kurulu ($(command -v opencode)) — ücretsiz modeller hazır."
else
  echo "[5/7] OpenCode sistemde kurulu görünmüyor."
  echo "      (OpenCode ile API anahtarı olmadan ücretsiz yapay zeka modellerini kullanabilirsin)"
  install_oc="e"
  if [[ -r /dev/tty ]]; then
    printf "      OpenCode şimdi otomatik kurulsun mu? [E/h]: "
    read -r install_oc </dev/tty || install_oc="e"
  fi
  if [[ -z "$install_oc" || "$install_oc" =~ ^[EeYy] ]]; then
    echo "      ⬇ OpenCode indiriliyor ve kuruluyor..."
    curl -fsSL https://opencode.ai/install | bash || true
    export PATH="$HOME/.opencode/bin:$PATH"
  fi
fi

# 5) Türkçe uyandırma modeli
echo "[6/7] Türkçe ses tanıma modeli hazırlanıyor..."
if [[ ! -d models/vosk-model-small-tr-0.3 ]]; then
  mkdir -p models
  tmp="models/tr-model.zip"
  curl -L --fail --progress-bar -o "$tmp" \
    https://alphacephei.com/vosk/models/vosk-model-small-tr-0.3.zip
  unzip -q -o "$tmp" -d models/
  rm -f "$tmp"
fi

# 6) Varsayılan config.toml, isteğe bağlı konuşma anahtar tuşu, PATH ve Masaüstü Kısayolu
chmod +x metro
if [[ ! -f config.toml ]]; then
  venv/bin/python -c "from metroai import settings; settings.save(settings.load())"
fi

if [[ -r /dev/tty ]]; then
  venv/bin/python -c "from metroai import settings, tui; s = settings.load(); tui.prompt_hotkey_setup(s)" </dev/tty || true
fi

echo "[7/7] Sistem entegrasyonu ve arka plan servisi kuruluyor..."
venv/bin/python -c "from metroai import settings, tui; tui.ensure_always_on_top_rule(settings.load().always_on_top)" || true

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
echo "  • Sağlayıcı bağlamak  : metro chat içinde /api yaz"
echo ""
