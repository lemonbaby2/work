#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "用法: $0 <Windows用户名> [Windows主机IP]" >&2
  exit 2
fi

WINDOWS_USER="$1"
WINDOWS_HOST="${2:-192.168.1.128}"
DELIVERY_DIR="/home/xjai/Desktop/sop xjai/交付_20260819_第三摄像头"

if ! timeout 3 bash -c "</dev/tcp/$WINDOWS_HOST/22" 2>/dev/null; then
  echo "Windows主机 $WINDOWS_HOST 的22端口未开放。请先以管理员运行 Windows管理员_开启SCP接收.ps1。" >&2
  exit 3
fi

scp "$DELIVERY_DIR/SOP完整部署包_第三摄像头_20260819.zip" \
    "$DELIVERY_DIR/SOP前后端源码包_20260819.zip" \
    "$WINDOWS_USER@$WINDOWS_HOST:Desktop/SOP交付包/"

echo "上传完成。请在 Windows 桌面 SOP交付包 目录核对两个 ZIP 文件。"
