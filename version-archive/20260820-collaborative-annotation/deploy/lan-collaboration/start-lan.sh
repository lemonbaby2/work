#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export SOP_HOST="${SOP_HOST:-0.0.0.0}"
export SOP_PORT="${SOP_PORT:-8097}"
export SOP_COLLAB_DB="${SOP_COLLAB_DB:-$ROOT/runtime/collaboration.sqlite3}"
export SOP_DEFAULT_PASSWORD="${SOP_DEFAULT_PASSWORD:?Set SOP_DEFAULT_PASSWORD before first start}"
export CVAT_URL="${CVAT_URL:-http://$(hostname -I | awk '{print $1}'):8081}"
PYTHON_BIN="${SOP_PYTHON:-/home/xjai/micromamba/envs/sop/bin/python3.12}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

cd "$ROOT"
exec "$PYTHON_BIN" server.py
