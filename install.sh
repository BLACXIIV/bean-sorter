#!/usr/bin/env bash
# Installs dependencies (needs internet ONCE), makes scripts executable and
# drops the launcher on the desktop.  --autostart also starts it on boot.
set -u
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sudo apt update
sudo apt install -y python3-flask python3-picamera2 python3-opencv python3-numpy \
  python3-gpiozero python3-lgpio php-cli php-sqlite3 php-zip php-gd php-mbstring \
  php-xml chromium || \
sudo apt install -y python3-flask python3-picamera2 python3-opencv python3-numpy \
  python3-gpiozero python3-lgpio php-cli php-sqlite3 php-zip php-gd php-mbstring \
  php-xml chromium-browser

chmod +x "$APP/start_bean_sorter.sh" "$APP/install.sh"

DESK_SRC="$APP/BeanSorter.desktop"
DESK_OUT="$(mktemp)"
sed "s|__APP__|$APP|g" "$DESK_SRC" > "$DESK_OUT"

for d in "$HOME/Desktop" "$HOME/.local/share/applications"; do
  mkdir -p "$d"
  cp "$DESK_OUT" "$d/BeanSorter.desktop"
  chmod +x "$d/BeanSorter.desktop"
  gio set "$d/BeanSorter.desktop" metadata::trusted true 2>/dev/null || true
done

if [ "${1:-}" = "--autostart" ]; then
  mkdir -p "$HOME/.config/autostart"
  cp "$DESK_OUT" "$HOME/.config/autostart/BeanSorter.desktop"
  echo "Autostart enabled."
fi
rm -f "$DESK_OUT"
echo "Done. Double-click 'Bean Sorter' on the desktop to start."
