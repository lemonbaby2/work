#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$ROOT/../.." && pwd)"
DESTINATION="${1:-$ROOT/runtime/delivery_soplzp0820_20260820}"
SOURCE_0264="$PROJECT_ROOT/视频数据/8月19号/DJI_20260819145537_0264_D.MP4"
SOURCE_0265="$PROJECT_ROOT/视频数据/8月19号/DJI_20260819151908_0265_D.MP4"

mkdir -p "$DESTINATION"

required=(
  "$ROOT/models/yolo26n_PCB插装0264_50轮_待人工验收.pt"
  "$ROOT/models/yolo26n_PCB插装0265_50轮_待人工验收.pt"
  "$ROOT/models/yolo26n_PCB插装0264_0265联合50轮_待人工验收.pt"
  "$ROOT/qa/pcb_0264_training_report.json"
  "$ROOT/qa/pcb_0265_training_report.json"
  "$ROOT/qa/pcb_0264_0265_joint_training_report.json"
  "$ROOT/qa/pcb_model_comparison_report.json"
)
for path in "${required[@]}"; do
  [[ -f "$path" ]] || { echo "Required delivery artifact is missing: $path" >&2; exit 1; }
done

tar -C "$ROOT" -czf "$DESTINATION/01_SOP平台源码_soplzp0820.tar.gz" \
  --exclude='./datasets/*/images' --exclude='./datasets/*/labels' --exclude='./runs' \
  --exclude='./runtime' --exclude='./web/media' --exclude='./models/*.pt' \
  --exclude='./models/*.pth' --exclude='./models/*.ts' --exclude='./__pycache__' .

tar -C "$ROOT" -czf "$DESTINATION/02_0264_0265关键帧标注数据.tar.gz" \
  --exclude='*.npy' --exclude='*.cache' \
  datasets/PCB插装0264_YOLOE关键帧预标注_待人工复核 \
  datasets/PCB插装0265_YOLOE关键帧预标注_待人工复核 \
  datasets/PCB插装0264_0265联合_YOLOE关键帧预标注_待人工复核 \
  web/data/video_0264_frame_annotations.jsonl web/data/video_0264_fine_object_candidates.jsonl \
  web/data/video_0265_frame_annotations.jsonl web/data/video_0265_fine_object_candidates.jsonl

tar -C "$ROOT" -czf "$DESTINATION/03_三模型权重与统一测试报告.tar.gz" \
  models/yolo26n_PCB插装0264_50轮_待人工验收.pt \
  models/yolo26n_PCB插装0265_50轮_待人工验收.pt \
  models/yolo26n_PCB插装0264_0265联合50轮_待人工验收.pt \
  qa/pcb_0264_training_report.json qa/pcb_0265_training_report.json \
  qa/pcb_0264_0265_joint_training_report.json qa/pcb_model_comparison_report.json \
  web/analysis/pcb_model_comparison config/pcb_model_registry.json

tar -C "$ROOT" -czf "$DESTINATION/04_网页代理视频_0264_0265.tar.gz" \
  web/media/local/DJI_20260819145537_0264_D_720p.mp4 \
  web/media/local/DJI_20260819151908_0265_D_720p.mp4

sqlite3 "$ROOT/runtime/sop_annotations.sqlite3" ".timeout 30000" ".backup '$DESTINATION/05_sop_annotations.sqlite3'"
sqlite3 "$DESTINATION/05_sop_annotations.sqlite3" "PRAGMA quick_check;" > "$DESTINATION/05_数据库完整性检查.txt"

{
  echo "GitHub: https://github.com/lemonbaby2/work/tree/soplzp0820"
  echo "Branch: soplzp0820"
  echo "Generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  echo "Truth status: automatic candidates pending human review; production release HOLD"
  echo "Original videos are delivered as separate files in the same Windows D: directory."
} > "$DESTINATION/README_交付说明.txt"

(
  cd "$DESTINATION"
  sha256sum ./* > SHA256SUMS.txt
)
sha256sum "$SOURCE_0264" "$SOURCE_0265" > "$DESTINATION/SHA256SUMS_4K原始视频.txt"
echo "$DESTINATION"
