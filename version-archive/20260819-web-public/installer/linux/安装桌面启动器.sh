#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DESKTOP_DIR="${XDG_DESKTOP_DIR:-/home/xjai/Desktop}"
LAUNCHER="$DESKTOP_DIR/宁波SOP分析平台.desktop"

mkdir -p "$DESKTOP_DIR"
sed "s|/home/xjai/sop_project/SOP分析平台_老板汇报版|$PROJECT_ROOT|g" \
  "$PROJECT_ROOT/installer/linux/启动SOP平台.desktop" > "$LAUNCHER"
chmod +x "$LAUNCHER"
echo "桌面启动器已安装：$LAUNCHER"
