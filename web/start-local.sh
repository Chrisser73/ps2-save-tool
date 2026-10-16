#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

BACKEND_PY="$ROOT_DIR/backend/.venv-unix/bin/python"
LOG_DIR="$ROOT_DIR/../logs"
mkdir -p "$LOG_DIR"

printf '\nPS2 Save Tool Web - Local Start\n'
printf '================================\n\n'

if [[ ! -x "$BACKEND_PY" || ! -d frontend/node_modules ]]; then
  printf 'Local dependencies are not set up yet.\n'
  printf 'Run ./setup.sh first.\n'
  exit 1
fi

port_open() {
  "$BACKEND_PY" - "$1" "$2" <<'PY' >/dev/null 2>&1
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.socket()
s.settimeout(0.35)
try:
    s.connect((host, port))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
PY
}

if port_open 127.0.0.1 6361; then
  printf '[SKIP] Backend is already running on port 6361.\n'
else
  printf '[START] Backend on port 6361...\n'
  (
    cd "$ROOT_DIR/backend"
    nohup "$BACKEND_PY" -m uvicorn app.main:app --host 0.0.0.0 --port 6361 \
      > "$LOG_DIR/web-backend.log" 2>&1 &
    echo $! > "$LOG_DIR/web-backend.pid"
  )
  sleep 1
  if port_open 127.0.0.1 6361; then
    printf '[ OK ] Backend started.\n'
  else
    printf '[WARN] Backend did not answer yet. Check %s/web-backend.log\n' "$LOG_DIR"
  fi
fi

if port_open 127.0.0.1 5173; then
  printf '[SKIP] Frontend is already running on port 5173.\n'
else
  printf '[START] Frontend on port 5173...\n'
  (
    cd "$ROOT_DIR/frontend"
    nohup npm run dev > "$LOG_DIR/web-frontend.log" 2>&1 &
    echo $! > "$LOG_DIR/web-frontend.pid"
  )
  sleep 1
  if port_open 127.0.0.1 5173; then
    printf '[ OK ] Frontend started.\n'
  else
    printf '[WARN] Frontend did not answer yet. Check %s/web-frontend.log\n' "$LOG_DIR"
  fi
fi

printf '\nFrontend: http://localhost:5173\n'
printf 'Backend:  http://localhost:6361\n'
printf 'API health: http://localhost:6361/api/health\n\n'
printf 'For another device on your LAN, open http://SERVER-IP:5173\n'
printf 'Local logs: %s/web-frontend.log and %s/web-backend.log\n\n' "$LOG_DIR" "$LOG_DIR"
