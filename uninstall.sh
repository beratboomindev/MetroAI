#!/usr/bin/env bash
# Metro Assistant — Kaldırma (Uninstall) Betiği
# Her yerden çalıştırılabilir:
#   curl -fsSL https://raw.githubusercontent.com/beratboomindev/MetroAI/main/uninstall.sh | bash
# veya yerel olarak:
#   ./uninstall.sh [--purge] [-y]
set -euo pipefail

# Renkler
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

PURGE=0
ASSUME_YES=0
KEEP_DATA=0

# Parametre analizi
for arg in "$@"; do
  case "$arg" in
    --purge|--all)
      PURGE=1
      ;;
    -y|--yes|-f|--force)
      ASSUME_YES=1
      ;;
    --keep-data)
      KEEP_DATA=1
      ;;
    -h|--help)
      echo -e "${BOLD}Kullanım:${NC} $0 [SEÇENEKLER]"
      echo ""
      echo "Seçenekler:"
      echo "  -y, --yes       Onay istemeden varsayılan kaldırma işlemini uygular"
      echo "  --purge, --all  Tüm modelleri, Python venv ortamını, hafıza/ayar verilerini ve proje dizinini tamamen siler"
      echo "  --keep-data     Yalnızca servis, kısayol ve CLI bağlantısını kaldırır; ayarları ve modelleri korur"
      echo "  -h, --help      Bu yardım iletisini görüntüler"
      exit 0
      ;;
    *)
      echo -e "${YELLOW}Bilinmeyen parametre: $arg${NC}"
      ;;
  esac
done

# Dizin tespiti
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
fi

INSTALL_DIR="${METRO_DIR:-}"
if [[ -z "$INSTALL_DIR" ]]; then
  if [[ -n "$SCRIPT_DIR" && -d "$SCRIPT_DIR/metroai" ]]; then
    INSTALL_DIR="$SCRIPT_DIR"
  else
    INSTALL_DIR="$HOME/MetroAI"
  fi
fi

echo ""
echo -e "${CYAN}╭──────────────────────────────────────────────────────────────╮${NC}"
echo -e "${CYAN}│  ${BOLD}◆ Metro Assistant — Kaldırma İşlemi Başlatılıyor${NC}${CYAN}            │${NC}"
echo -e "${CYAN}╰──────────────────────────────────────────────────────────────╯${NC}"
echo ""

# Kullanıcı onayı (Eğer -y verilmediyse)
if [[ "$ASSUME_YES" -eq 0 && -r /dev/tty ]]; then
  echo -e "${YELLOW}Metro Assistant sistemden kaldırılacak.${NC}"
  read -r -p "Devam etmek istiyor musunuz? [E/h] " confirm </dev/tty || confirm=""
  confirm="${confirm:-E}"
  if [[ ! "$confirm" =~ ^[eEyY]$ ]]; then
    echo -e "${RED}Kaldırma işlemi iptal edildi.${NC}"
    exit 0
  fi
  echo ""
fi

# 1) Systemd arka plan servisini durdur ve devre dışı bırak
echo -e "[1/6] ${CYAN}Arka plan servisi durduruluyor ve kapatılıyor...${NC}"
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user stop metro.service 2>/dev/null || true
  systemctl --user disable metro.service 2>/dev/null || true
  systemctl --user reset-failed metro.service 2>/dev/null || true
fi

# 2) Çalışan Metro süreçlerini sonlandır
echo -e "[2/6] ${CYAN}Çalışan Metro Assistant süreçleri kontrol ediliyor...${NC}"
pkill -f "python.*-m metroai" 2>/dev/null || true
pkill -f "metro chat" 2>/dev/null || true

# 3) Systemd servis dosyasını ve kalıntılarını sil
echo -e "[3/6] ${CYAN}Servis dosyaları ve sistem kayıtları temizleniyor...${NC}"
SERVICE_FILE="$HOME/.config/systemd/user/metro.service"
if [[ -f "$SERVICE_FILE" ]]; then
  rm -f "$SERVICE_FILE"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload 2>/dev/null || true
  fi
  echo -e "      ${GREEN}✓${NC} $SERVICE_FILE kaldırıldı."
fi

# 4) CLI çalıştırıcı bağlantısını kaldır (~/.local/bin/metro)
echo -e "[4/6] ${CYAN}CLI bağlantısı ve masaüstü entegrasyonu temizleniyor...${NC}"
if [[ -L "$HOME/.local/bin/metro" || -f "$HOME/.local/bin/metro" ]]; then
  rm -f "$HOME/.local/bin/metro"
  echo -e "      ${GREEN}✓${NC} ~/.local/bin/metro kaldırıldı."
fi

# Masaüstü kısayolu
DESKTOP_ENTRY="$HOME/.local/share/applications/metro-assistant.desktop"
if [[ -f "$DESKTOP_ENTRY" ]]; then
  rm -f "$DESKTOP_ENTRY"
  echo -e "      ${GREEN}✓${NC} $DESKTOP_ENTRY kaldırıldı."
fi

# Uygulama ikonları
ICONS_BASE="$HOME/.local/share/icons/hicolor"
for sz in "128x128" "256x256" "512x512"; do
  rm -f "$ICONS_BASE/$sz/apps/metro-assistant.png" 2>/dev/null || true
done
rm -f "$ICONS_BASE/scalable/apps/metro-assistant.svg" 2>/dev/null || true

# Masaüstü ve ikon önbelleğini tazele
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
fi
if command -v kbuildsycoca6 >/dev/null 2>&1; then
  kbuildsycoca6 --noincremental 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

# 5) KDE KWin "Her Zaman Üstte" pencere kuralını temizle
echo -e "[5/6] ${CYAN}Pencere kuralları ve izole terminal ayarları temizleniyor...${NC}"
KWIN_RC="$HOME/.config/kwinrulesrc"
if [[ -f "$KWIN_RC" ]] && command -v python3 >/dev/null 2>&1; then
  python3 - << 'EOF' 2>/dev/null || true
import sys
from pathlib import Path

rc = Path.home() / ".config" / "kwinrulesrc"
if not rc.exists():
    sys.exit(0)

try:
    lines = rc.read_text(encoding="utf-8", errors="ignore").splitlines()
    new_lines = []
    in_metro = False
    found = False

    for line in lines:
        stripped = line.strip()
        if stripped == "[metro-assistant]":
            in_metro = True
            found = True
            continue
        if in_metro and stripped.startswith("["):
            in_metro = False

        if in_metro:
            continue

        if stripped.startswith("Order="):
            parts = stripped.split("=", 1)[1].split(",")
            cleaned = [p.strip() for p in parts if p.strip() and p.strip() != "metro-assistant"]
            new_lines.append(f"Order={','.join(cleaned)}")
        else:
            new_lines.append(line)

    if found:
        # count anahtarını güncelle
        order_count = None
        for l in new_lines:
            if l.startswith("Order="):
                val = l.split("=", 1)[1]
                order_count = len([x for x in val.split(",") if x])
                break

        if order_count is not None:
            updated_lines = []
            for l in new_lines:
                if l.startswith("count="):
                    updated_lines.append(f"count={order_count}")
                else:
                    updated_lines.append(l)
            new_lines = updated_lines

        rc.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
except Exception:
    pass
EOF

  # KWin'i yeniden yapılandır
  if command -v qdbus6 >/dev/null 2>&1; then
    qdbus6 org.kde.KWin /KWin reconfigure 2>/dev/null || true
  elif command -v qdbus >/dev/null 2>&1; then
    qdbus org.kde.KWin /KWin reconfigure 2>/dev/null || true
  fi
fi

# İzole Kitty / Metro terminal konfigürasyonunu kaldır (~/.config/metro)
if [[ -d "$HOME/.config/metro" ]]; then
  rm -rf "$HOME/.config/metro"
  echo -e "      ${GREEN}✓${NC} ~/.config/metro temizlendi."
fi

# 6) Veri ve Proje Dizini Temizliği
echo -e "[6/6] ${CYAN}Veri ve çalışma dizini temizliği...${NC}"

remove_data=0
remove_dir=0

if [[ "$PURGE" -eq 1 ]]; then
  remove_data=1
  remove_dir=1
elif [[ "$KEEP_DATA" -eq 1 ]]; then
  remove_data=0
  remove_dir=0
elif [[ "$ASSUME_YES" -eq 1 ]]; then
  remove_data=0
  remove_dir=0
else
  if [[ -r /dev/tty ]]; then
    echo ""
    echo -e "${YELLOW}Sistem entegrasyonları kaldırıldı.${NC}"
    read -r -p "Yapılandırma, hafıza ve indirilen modeller (config.toml, state, models, venv) silinsin mi? [e/H] " answer </dev/tty || answer=""
    if [[ "$answer" =~ ^[eEyY]$ ]]; then
      remove_data=1
      read -r -p "MetroAI ana klasörü ($INSTALL_DIR) tamamen silinsin mi? [e/H] " answer_dir </dev/tty || answer_dir=""
      if [[ "$answer_dir" =~ ^[eEyY]$ ]]; then
        remove_dir=1
      fi
    fi
  fi
fi

if [[ -d "$INSTALL_DIR" ]]; then
  if [[ "$remove_dir" -eq 1 ]]; then
    echo -e "      ${YELLOW}•${NC} MetroAI dizini tamamen siliniyor ($INSTALL_DIR)..."
    cd "$HOME"
    rm -rf "$INSTALL_DIR"
    echo -e "      ${GREEN}✓${NC} Proje dizini silindi."
  elif [[ "$remove_data" -eq 1 ]]; then
    echo -e "      ${YELLOW}•${NC} Veri dosyaları temizleniyor..."
    rm -rf "$INSTALL_DIR/venv" "$INSTALL_DIR/models" "$INSTALL_DIR/state" "$INSTALL_DIR/config.toml"
    find "$INSTALL_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    echo -e "      ${GREEN}✓${NC} Sanal ortam, modeller, hafıza ve ayarlar temizlendi."
  else
    echo -e "      ${BLUE}ℹ${NC} Proje dosyaları ve ayarlar korundu ($INSTALL_DIR)."
  fi
fi

echo ""
echo -e "${GREEN}╭──────────────────────────────────────────────────────────────╮${NC}"
echo -e "${GREEN}│  ✓ Metro Assistant sisteminizden başarıyla kaldırıldı.       │${NC}"
echo -e "${GREEN}╰──────────────────────────────────────────────────────────────╯${NC}"
echo ""
