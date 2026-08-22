#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$PROJECT_ROOT/deploy/label-studio/data" "$PROJECT_ROOT/runtime/label_studio_imports"
cd "$PROJECT_ROOT/deploy/label-studio"
exec docker compose up -d
