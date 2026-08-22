#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export SOP_HOST="${SOP_HOST:-127.0.0.1}"
export SOP_PORT="${SOP_PORT:-8096}"
export SOP_PUBLIC_READONLY=1
exec python3 -u server.py
