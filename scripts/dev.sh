#!/usr/bin/env bash
# Run PocketSlice locally without Docker, using the fake slicer from the test-suite
# (or a real OrcaSlicer if ORCA_BIN is set). Handy for frontend work.
#
#   ./scripts/dev.sh                      # http://127.0.0.1:8080, fake slicer, fixture presets
#   ORCA_BIN=/usr/bin/orca-slicer PROFILES_DIR=~/.config/OrcaSlicer ./scripts/dev.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export DATA_DIR="${DATA_DIR:-$ROOT/data}"
export PROFILES_DIR="${PROFILES_DIR:-$ROOT/backend/tests/fixtures/profiles}"
export ORCA_BIN="${ORCA_BIN:-$ROOT/backend/tests/fake_orca.py}"
export ORCA_SYSTEM_PROFILES="${ORCA_SYSTEM_PROFILES:-/nonexistent}"
export FAKE_ORCA_DELAY="${FAKE_ORCA_DELAY:-0.5}"
cd "$ROOT/backend"
python3 -m pip install -q -r requirements.txt
exec python3 -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8080}" --reload
