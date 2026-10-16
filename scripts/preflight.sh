#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/preflight-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

printf 'PS2 Save Tool preflight\n'
printf 'Log: %s\n\n' "$LOG_FILE"

make --no-print-directory -C "$ROOT_DIR" version-check badge-check test web-build

printf '\nPreflight passed. Log saved to: %s\n' "$LOG_FILE"
