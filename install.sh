#!/usr/bin/env bash
# Metro kurulum betiği — sudo GEREKTİRMEZ.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

echo "== Metro kurulumu =="

if ! command -v python3 >/dev/null; then
  echo "python3 bulunamadı. Kur: sudo pacman -S python" >&2
  exit 1
fi

if [[ ! -x venv/bin/python ]]; then
  echo "[1/4] Python sanal ortamı oluşturuluyor..."
  python3 -m venv venv
else
  echo "[1/4] Sanal ortam mevcut."
fi

echo "[2/4] Python paketleri kuruluyor..."
venv/bin/python -m pip install --quiet --upgrade pip
venv/bin/python -m pip install --quiet -r requirements.txt

echo "[3/4] Seslendirme/çalma araçları kontrol ediliyor..."
for bin in mpv ffmpeg xdg-open notify-send; do
  if ! command -v "$bin" >/dev/null; then
    echo "  ! $bin yok — kurmak için: sudo pacman -S $bin"
  fi
done

echo "[4/4] Türkçe uyandırma modeli..."
if [[ -d models/vosk-model-small-tr-0.3 ]]; then
  echo "  model hazır."
else
  mkdir -p models
  tmp="models/tr-model.zip"
  curl -L --fail --progress-bar -o "$tmp" \
    https://alphacephei.com/vosk/models/vosk-model-small-tr-0.3.zip
  unzip -q -o "$tmp" -d models/
  rm -f "$tmp"
  echo "  model indirildi."
fi

chmod +x metro
echo
echo "Kurulum tamam. Şimdi API anahtarlarını gir:"
echo "  ./metro setup"
echo "Sonra test et:"
echo "  ./metro doctor && ./metro check"
echo "Arka planda başlat:"
echo "  ./metro service install"
