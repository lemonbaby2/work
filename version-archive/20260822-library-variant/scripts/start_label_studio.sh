#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
DATA_DIR="$PROJECT_ROOT/deploy/label-studio/data"
IMPORT_DIR="$PROJECT_ROOT/runtime/label_studio_imports"
mkdir -p "$DATA_DIR" "$IMPORT_DIR"
cd "$PROJECT_ROOT/deploy/label-studio"
export LABEL_STUDIO_IMAGE="${LABEL_STUDIO_IMAGE:-local/label-studio:1.23.0}"
IMAGE_ARCHIVE="${LABEL_STUDIO_IMAGE_FILE:-$PROJECT_ROOT/deploy/label-studio/label-studio-image.tar}"

if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 Docker，请先安装 Docker 和 Docker Compose 后再启动 Label Studio。" >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  if command -v sg >/dev/null 2>&1 && sg docker -c 'docker info' >/dev/null 2>&1; then
    exec sg docker -c "$SCRIPT_PATH"
  fi
  echo "当前用户没有访问 Docker 服务的权限，请执行：sudo usermod -aG docker $USER，然后重新登录 Ubuntu。" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "未找到 docker compose，请先安装 Docker Compose 插件。" >&2
  exit 1
fi
if ! docker image inspect "$LABEL_STUDIO_IMAGE" >/dev/null 2>&1; then
  if [[ -f "$IMAGE_ARCHIVE" ]]; then
    echo "未找到镜像 $LABEL_STUDIO_IMAGE，正在从本地归档加载：$IMAGE_ARCHIVE"
    docker load -i "$IMAGE_ARCHIVE"
  fi
fi
if ! docker image inspect "$LABEL_STUDIO_IMAGE" >/dev/null 2>&1; then
  echo "未找到可用的 Label Studio 镜像：$LABEL_STUDIO_IMAGE" >&2
  echo "请先执行 ./scripts/cache_label_studio_image.sh 生成本地归档，或设置 LABEL_STUDIO_IMAGE 指向内网镜像仓库。" >&2
  exit 1
fi
# Label Studio runs as UID 1001 inside the container. These local-only bind
# mounts need a writable directory even when a previous container owned them.
docker run --rm --user 0 --entrypoint /bin/sh \
  -v "$DATA_DIR:/label-studio/data" \
  -v "$IMPORT_DIR:/label-studio/imports" \
  "$LABEL_STUDIO_IMAGE" \
  -c 'chmod a+rwx /label-studio/data /label-studio/imports'
docker compose up -d
echo "Label Studio 已启动：http://127.0.0.1:8080"
