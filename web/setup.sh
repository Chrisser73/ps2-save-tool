#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ OK ]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

printf '\nPS2 Save Tool Web - Local Setup\n'
printf '================================\n\n'

[[ -f backend/requirements.txt ]] || fail "backend/requirements.txt was not found. Run this script from the web project root."
[[ -f frontend/package.json ]] || fail "frontend/package.json was not found. Run this script from the web project root."

PYTHON_BIN=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

[[ -n "$PYTHON_BIN" ]] || fail "Python 3.10+ is required. Install Python 3 and try again."
ok "$($PYTHON_BIN --version 2>&1)"

command -v node >/dev/null 2>&1 || fail "Node.js is required. Node.js 22 LTS is recommended."
command -v npm >/dev/null 2>&1 || fail "npm is required. Install Node.js/npm and try again."

if ! node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a>20 || (a===20 && b>=19) ? 0 : 1)' >/dev/null 2>&1; then
  fail "Node.js 20.19+ is required; Node.js 22 LTS is recommended. Found $(node --version)."
fi
ok "Node $(node --version) / npm $(npm --version)"

VENV_DIR="backend/.venv-unix"

if [[ ! -d "$VENV_DIR" ]]; then
  info "Creating backend Python virtual environment..."
  if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
    printf '\nCould not create the virtual environment.\n'
    printf 'On Debian / Ubuntu / Raspberry Pi OS install the venv package first, for example:\n'
    printf '  sudo apt install python3-venv\n\n'
    exit 1
  fi
else
  ok "Backend virtual environment already exists."
fi

VENV_PY="$ROOT_DIR/$VENV_DIR/bin/python"
[[ -x "$VENV_PY" ]] || fail "The backend virtual environment is incomplete: $VENV_PY"

info "Upgrading pip..."
"$VENV_PY" -m pip install --upgrade pip

info "Installing backend requirements..."
"$VENV_PY" -m pip install -r backend/requirements.txt
ok "Backend dependencies installed."

info "Installing frontend dependencies..."
(
  cd frontend
  npm ci
)
ok "Frontend dependencies installed."

chmod +x setup.sh start-local.sh test.sh 2>/dev/null || true

printf '\nSetup completed successfully.\n'
printf 'Nothing was started automatically.\n\n'
printf 'Start both local services with:\n'
printf '  ./start-local.sh\n\n'
printf 'Frontend: http://localhost:5173\n'
printf 'Backend:  http://localhost:6361\n\n'
