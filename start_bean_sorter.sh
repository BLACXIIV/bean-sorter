#!/usr/bin/env bash
# Bean Sorter launcher: engine + PHP + kiosk browser, with a lockfile so a
# second click only re-opens the browser. Extra engine args pass through
# (--simulate, --camera SRC, --valve real|sim); the production default is
# --camera pi --valve real.
set -u
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP"

SIM=""
for a in "$@"; do [ "$a" = "--simulate" ] && SIM="--simulate"; done
ENGINE_ARGS=(--camera pi --valve real)
[ -n "$SIM" ] && ENGINE_ARGS=(--simulate)

mkdir -p logs var/sessions var/chromium
LOCK="$APP/var/bean_sorter.lock"

BIND="127.0.0.1"
[ "${BEAN_LAN:-0}" = "1" ] && BIND="0.0.0.0"

CHROME="$(command -v chromium || command -v chromium-browser || true)"

open_kiosk() {
  [ -z "$CHROME" ] && return 1
  "$CHROME" --kiosk --noerrdialogs --disable-infobars \
    --disable-session-crashed-bubble --incognito --password-store=basic \
    --user-data-dir="$APP/var/chromium" http://127.0.0.1:8080/ &
  CHROME_PID=$!
  return 0
}

if [ -f "$LOCK" ]; then
  # already running -> just re-open the kiosk window
  open_kiosk || true
  exit 0
fi
echo $$ > "$LOCK"

ENGINE_PID=""; PHP_PID=""; CHROME_PID=""
cleanup() {
  [ -n "$ENGINE_PID" ] && kill "$ENGINE_PID" 2>/dev/null   # engine's finally{} turns the valve OFF
  [ -n "$PHP_PID" ] && kill "$PHP_PID" 2>/dev/null
  rm -f "$LOCK"
}
trap cleanup EXIT INT TERM

python3 engine.py "${ENGINE_ARGS[@]}" >> logs/engine.log 2>&1 &
ENGINE_PID=$!
PHP_CLI_SERVER_WORKERS=4 php -S "$BIND:8080" -t web >> logs/php.log 2>&1 &
PHP_PID=$!

ok=0
for _ in $(seq 1 50); do
  if curl -sf http://127.0.0.1:5000/api/status >/dev/null 2>&1 && \
     curl -sf "http://127.0.0.1:8080/" >/dev/null 2>&1; then
    ok=1; break
  fi
  sleep 0.5
done
if [ "$ok" != 1 ]; then
  MSG="Bean Sorter failed to start - see logs/engine.log and logs/php.log"
  if command -v zenity >/dev/null 2>&1; then zenity --error --text "$MSG" || true
  elif command -v notify-send >/dev/null 2>&1; then notify-send "Bean Sorter" "$MSG" || true
  else echo "$MSG" >&2; fi
  exit 1
fi

open_kiosk || true
if [ -n "$CHROME_PID" ]; then wait "$CHROME_PID" 2>/dev/null || true; fi
# chromium closed -> EXIT trap stops engine (valve OFF) + PHP and frees the lock
