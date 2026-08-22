#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PYTHON_BIN=${SOP_PYTHON:-/home/xjai/micromamba/envs/sop/bin/python}
CLOUDFLARED_BIN=${CLOUDFLARED_BIN:-$ROOT/runtime/bin/cloudflared}
PUBLIC_PORT=${SOP_PUBLIC_PORT:-8097}

cleanup() {
    if [[ -n "${SERVER_PID:-}" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

SOP_HOST=127.0.0.1 \
SOP_PORT="$PUBLIC_PORT" \
SOP_SECURE_COOKIE=1 \
"$PYTHON_BIN" "$ROOT/server.py" &
SERVER_PID=$!

for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:$PUBLIC_PORT/api/health" >/dev/null; then
        break
    fi
    sleep 1
done
curl -fsS "http://127.0.0.1:$PUBLIC_PORT/api/health" >/dev/null

if [[ -x "$CLOUDFLARED_BIN" ]]; then
    "$CLOUDFLARED_BIN" tunnel --no-autoupdate --url "http://127.0.0.1:$PUBLIC_PORT"
else
    echo "cloudflared is unavailable; using the temporary localhost.run HTTPS tunnel." >&2
    ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 \
        -o ExitOnForwardFailure=yes -R "80:127.0.0.1:$PUBLIC_PORT" nokey@localhost.run
fi
