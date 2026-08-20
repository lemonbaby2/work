#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$ROOT/../.." && pwd)"
DESTINATION="$(realpath -m "${1:-$ROOT/runtime/delivery_soplzp0820_20260820}")"
SOURCE_0264="$PROJECT_ROOT/视频数据/8月19号/DJI_20260819145537_0264_D.MP4"
SOURCE_0265="$PROJECT_ROOT/视频数据/8月19号/DJI_20260819151908_0265_D.MP4"

mkdir -p "$DESTINATION"

required=(
  "$ROOT/runtime/SOP平台.exe"
  "$ROOT/runtime/windows-python-embed/python-3.12.10-embed-amd64.zip"
  "$ROOT/models/yolo26n_PCB插装0264_50轮_待人工验收.pt"
  "$ROOT/models/yolo26n_PCB插装0265_50轮_待人工验收.pt"
  "$ROOT/models/yolo26n_PCB插装0264_0265联合50轮_待人工验收.pt"
  "$ROOT/models/yolo26n_PCB插装0264_ROI增强5轮_待人工验收.pt"
  "$ROOT/models/yolo26n_PCB插装0265_ROI增强5轮_待人工验收.pt"
  "$ROOT/models/yolo26n_PCB插装0264_0265联合_ROI增强5轮_待人工验收.pt"
  "$ROOT/qa/pcb_0264_training_report.json"
  "$ROOT/qa/pcb_0265_training_report.json"
  "$ROOT/qa/pcb_0264_0265_joint_training_report.json"
  "$ROOT/qa/pcb_0264_roi_training_5epochs_report.json"
  "$ROOT/qa/pcb_0265_roi_training_5epochs_report.json"
  "$ROOT/qa/pcb_0264_0265_joint_roi_training_5epochs_report.json"
  "$ROOT/qa/pcb_model_comparison_report.json"
)
for path in "${required[@]}"; do
  [[ -f "$path" ]] || { echo "Required delivery artifact is missing: $path" >&2; exit 1; }
done

sqlite3 "$ROOT/runtime/sop_annotations.sqlite3" ".timeout 30000" ".backup '$DESTINATION/05_sop_annotations.sqlite3'"
sqlite3 "$DESTINATION/05_sop_annotations.sqlite3" "PRAGMA quick_check;" > "$DESTINATION/05_数据库完整性检查.txt"

WINDOWS_STAGE="$DESTINATION/.windows_app_stage"
rm -rf "$WINDOWS_STAGE"
mkdir -p "$WINDOWS_STAGE/app/models" "$WINDOWS_STAGE/app/runtime" "$WINDOWS_STAGE/app/web/media"
rsync -a "$ROOT/" "$WINDOWS_STAGE/app/" \
  --exclude='/datasets' --exclude='/runs' --exclude='/runtime' --exclude='/web/media' \
  --exclude='/models/*.pt' --exclude='/models/*.pth' --exclude='/models/*.onnx' --exclude='/models/*.ts' \
  --exclude='/*.pt' --exclude='/*.pth' --exclude='/*.onnx' --exclude='/*.ts' \
  --exclude='/weights' --exclude='/__pycache__' --exclude='*.pyc'
cp "$ROOT/runtime/SOP平台.exe" "$WINDOWS_STAGE/app/SOP平台.exe"
unzip -q "$ROOT/runtime/windows-python-embed/python-3.12.10-embed-amd64.zip" -d "$WINDOWS_STAGE/app/python-runtime"
rsync -a "$ROOT/models/" "$WINDOWS_STAGE/app/models/"
rsync -a "$ROOT/web/media/" "$WINDOWS_STAGE/app/web/media/"
cp "$DESTINATION/05_sop_annotations.sqlite3" "$WINDOWS_STAGE/app/runtime/sop_annotations.sqlite3"
(
  cd "$WINDOWS_STAGE"
  zip -q -r "$DESTINATION/00_Windows_SOP平台_可运行应用.zip" app
)
unzip -tq "$DESTINATION/00_Windows_SOP平台_可运行应用.zip" >/dev/null
for member in \
  "app/SOP平台.exe" "app/server.py" "app/python-runtime/python.exe" \
  "app/runtime/sop_annotations.sqlite3" "app/config/pcb_model_registry.json" \
  "app/models/yolo26n_PCB插装0264_50轮_待人工验收.pt" \
  "app/models/yolo26n_PCB插装0265_50轮_待人工验收.pt" \
  "app/models/yolo26n_PCB插装0264_0265联合50轮_待人工验收.pt" \
  "app/models/yolo26n_PCB插装0264_ROI增强5轮_待人工验收.pt" \
  "app/models/yolo26n_PCB插装0265_ROI增强5轮_待人工验收.pt" \
  "app/models/yolo26n_PCB插装0264_0265联合_ROI增强5轮_待人工验收.pt"; do
  unzip -Z1 "$DESTINATION/00_Windows_SOP平台_可运行应用.zip" | grep -Fx "$member" >/dev/null || {
    echo "Windows application package is missing: $member" >&2
    exit 1
  }
done
rm -rf "$WINDOWS_STAGE"

tar -C "$ROOT" -czf "$DESTINATION/01_SOP平台源码_soplzp0820.tar.gz" \
  --exclude='./datasets/*/images' --exclude='./datasets/*/labels' --exclude='./runs' \
  --exclude='./runtime' --exclude='./web/media' --exclude='./models/*.pt' \
  --exclude='./models/*.pth' --exclude='./models/*.ts' --exclude='./__pycache__' .

tar -C "$ROOT" -czf "$DESTINATION/02_0264_0265_ROI增强关键帧标注数据.tar.gz" \
  --exclude='*.npy' --exclude='*.cache' \
  datasets/PCB插装0264_YOLOE_ROI增强_待人工复核 \
  datasets/PCB插装0265_YOLOE_ROI增强_待人工复核 \
  datasets/PCB插装0264_0265联合_YOLOE_ROI增强_待人工复核 \
  web/data/video_0264_frame_annotations.jsonl web/data/video_0264_fine_object_candidates.jsonl \
  web/data/video_0265_frame_annotations.jsonl web/data/video_0265_fine_object_candidates.jsonl

tar -C "$ROOT" -czf "$DESTINATION/03_六模型权重与统一测试报告.tar.gz" \
  models/yolo26n_PCB插装0264_50轮_待人工验收.pt \
  models/yolo26n_PCB插装0265_50轮_待人工验收.pt \
  models/yolo26n_PCB插装0264_0265联合50轮_待人工验收.pt \
  models/yolo26n_PCB插装0264_ROI增强5轮_待人工验收.pt \
  models/yolo26n_PCB插装0265_ROI增强5轮_待人工验收.pt \
  models/yolo26n_PCB插装0264_0265联合_ROI增强5轮_待人工验收.pt \
  qa/pcb_0264_training_report.json qa/pcb_0265_training_report.json \
  qa/pcb_0264_0265_joint_training_report.json qa/pcb_model_comparison_report.json \
  qa/pcb_0264_roi_training_5epochs_report.json qa/pcb_0265_roi_training_5epochs_report.json \
  qa/pcb_0264_0265_joint_roi_training_5epochs_report.json \
  web/analysis/pcb_model_comparison config/pcb_model_registry.json

tar -C "$ROOT" -czf "$DESTINATION/04_网页代理视频_0264_0265.tar.gz" \
  web/media/local/DJI_20260819145537_0264_D_720p.mp4 \
  web/media/local/DJI_20260819151908_0265_D_720p.mp4

{
  echo "GitHub: https://github.com/lemonbaby2/work/tree/soplzp0820"
  echo "Branch: soplzp0820"
  echo "Generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  echo "Truth status: automatic candidates pending human review; production release HOLD"
  echo "Original videos are delivered as separate files in the same Windows D: directory."
} > "$DESTINATION/README_交付说明.txt"

(
  cd "$DESTINATION"
  find . -maxdepth 1 -type f ! -name SHA256SUMS.txt -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS.txt
)
sha256sum "$SOURCE_0264" "$SOURCE_0265" > "$DESTINATION/SHA256SUMS_4K原始视频.txt"
echo "$DESTINATION"
