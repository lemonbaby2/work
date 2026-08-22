#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE_PATH="${LABEL_STUDIO_IMAGE_FILE:-$PROJECT_ROOT/deploy/label-studio/label-studio-image.tar}"
SOURCE_IMAGE="${LABEL_STUDIO_SOURCE_IMAGE:-docker.1ms.run/heartexlabs/label-studio:1.23.0}"
LOCAL_IMAGE="${LABEL_STUDIO_IMAGE:-local/label-studio:1.23.0}"

if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 Docker，请先安装 Docker。" >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  if command -v sg >/dev/null 2>&1 && sg docker -c 'docker info' >/dev/null 2>&1; then
    exec sg docker -c "$0"
  fi
  echo "当前用户没有访问 Docker 服务的权限，请执行：sudo usermod -aG docker $USER，然后重新登录 Ubuntu。" >&2
  exit 1
fi

mkdir -p "$(dirname "$ARCHIVE_PATH")"
if ! docker image inspect "$SOURCE_IMAGE" >/dev/null 2>&1; then
  echo "正在准备一次性镜像：$SOURCE_IMAGE"
  docker pull "$SOURCE_IMAGE"
fi

docker tag "$SOURCE_IMAGE" "$LOCAL_IMAGE"
echo "正在导出固定本地镜像：$LOCAL_IMAGE"
docker save --output "$ARCHIVE_PATH" "$LOCAL_IMAGE"
chmod 0644 "$ARCHIVE_PATH"

IMAGE_ID="$(docker image inspect "$LOCAL_IMAGE" --format '{{.Id}}')"
IMAGE_VERSION="$(docker image inspect "$LOCAL_IMAGE" --format '{{index .Config.Labels \"org.opencontainers.image.version\"}}' 2>/dev/null || true)"
echo "本地镜像归档已生成：$ARCHIVE_PATH"
echo "镜像：$LOCAL_IMAGE"
echo "ID：$IMAGE_ID"
if [[ -n "$IMAGE_VERSION" ]]; then
  echo "版本：$IMAGE_VERSION"
fi
