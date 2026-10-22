#!/usr/bin/env bash
# Dev run on a laptop (Linux/macOS): webcam + simulated valve + dev database.
# Close the terminal (or Ctrl+C) to stop both servers.
set -u
cd "$(dirname "$0")"
export BEAN_DB=data/dev.db
mkdir -p logs var/sessions

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

echo "[dev] starting engine (webcam + simulated valve) ..."
"$PY" engine.py --camera webcam --valve sim > logs/dev-engine.log 2>&1 &
ENG=$!
echo "[dev] starting PHP server ..."
php -S 127.0.0.1:8080 -t web > logs/dev-php.log 2>&1 &
PHP=$!

cleanup() { kill "$ENG" "$PHP" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "[dev] waiting for engine + PHP (up to 25 s) ..."
ok=0
for _ in $(seq 1 50); do
    if curl -sf http://127.0.0.1:5000/api/status >/dev/null \
       && curl -sf -o /dev/null http://127.0.0.1:8080/; then ok=1; break; fi
    sleep 0.5
done
if [ "$ok" = 0 ]; then
    echo "[dev] FAILED - see logs/dev-engine.log and logs/dev-php.log"
    exit 1
fi

echo "[dev] opening http://127.0.0.1:8080/ ..."
(xdg-open http://127.0.0.1:8080/ 2>/dev/null \
    || open http://127.0.0.1:8080/ 2>/dev/null) &

echo "Dev servers running. Close this terminal to stop them."
wait
