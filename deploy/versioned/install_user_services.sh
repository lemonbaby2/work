#!/usr/bin/env bash
set -euo pipefail

unit_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/systemd" && pwd)"

for unit in "$unit_dir"/*.service; do
  systemctl --user link --force "$unit"
done

systemctl --user daemon-reload
systemctl --user enable --now \
  sop-version-index.service \
  sop-version-library.service \
  sop-version-tracking.service \
  sop-version-collaborative.service \
  sop-version-web-public.service \
  sop-version-third-camera.service

systemctl --user --no-pager --full status \
  sop-version-index.service \
  sop-version-library.service \
  sop-version-tracking.service \
  sop-version-collaborative.service \
  sop-version-web-public.service \
  sop-version-third-camera.service
