#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "未找到 .venv，请先执行: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

export SOP_CAMERA_SOURCE="${SOP_CAMERA_SOURCE:-/dev/v4l/by-id/usb-Jieli_Technology_USB_Composite_Device-video-index0}"
export SOP_CAMERA_COUNT="${SOP_CAMERA_COUNT:-3}"
export SOP_CAMERA_MODEL="${SOP_CAMERA_MODEL:-$PROJECT_ROOT/models/yolo11n.pt}"
export SOP_CAMERA_DEVICE="${SOP_CAMERA_DEVICE:-0}"
export SOP_DESKTOP_DIR="${SOP_DESKTOP_DIR:-/home/xjai/Desktop/sop xjai}"
export SOP_CAMERA_MAX_FPS="${SOP_CAMERA_MAX_FPS:-15}"
export SOP_CAMERA_WIDTH="${SOP_CAMERA_WIDTH:-1280}"
export SOP_CAMERA_HEIGHT="${SOP_CAMERA_HEIGHT:-720}"
exec .venv/bin/python server.py
