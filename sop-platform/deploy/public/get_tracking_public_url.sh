#!/usr/bin/env bash
set -euo pipefail

unit="sop-tracking-public-tunnel.service"
url="$(journalctl --user --unit "$unit" --since '-10 minutes' --no-pager --output cat | grep -Eo 'https://[[:alnum:]-]+\.lhr\.life' | tail -n 1 || true)"

if [[ -z "$url" ]]; then
    echo "The public URL is not available yet. Check: systemctl --user status $unit" >&2
    exit 1
fi

printf '%s\n' "$url"
