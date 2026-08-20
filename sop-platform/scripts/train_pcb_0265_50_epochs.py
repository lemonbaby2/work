from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

import torch
from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets/PCB插装0265_YOLOE关键帧预标注_待人工复核"
RUN_ROOT = ROOT / "runs/pcb_0265_yolo26"
OUTPUT_MODEL = ROOT / "models/yolo26n_PCB插装0265_50轮_待人工验收.pt"
REPORT_PATH = ROOT / "qa/pcb_0265_training_report.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train PCB 0265 student detector for at least 50 epochs")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--run-name", default="PCB插装0265_50轮")
    parser.add_argument("--output-model", type=Path, default=OUTPUT_MODEL)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--train-videos", nargs="+", default=["DJI_20260819151908_0265_D.MP4 only"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.epochs < 50:
        raise ValueError("Production training request requires at least 50 epochs")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required for this training task")
    dataset = args.dataset.resolve()
    output_model = args.output_model.resolve()
    report_path = args.report.resolve()
    data_yaml = dataset / "data.yaml"
    if not data_yaml.exists():
        raise RuntimeError(f"Dataset is not prepared: {data_yaml}")
    started = time.time()
    run_name = args.run_name
    last = RUN_ROOT / run_name / "weights/last.pt"
    previous = Path.cwd()
    os.chdir(ROOT / "models")
    try:
        model = YOLO(str(last if args.resume and last.exists() else ROOT / "models/yolo26n.pt"))
    finally:
        os.chdir(previous)
    result = model.train(
        data=str(data_yaml), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, device=0,
        workers=args.workers, project=str(RUN_ROOT), name=run_name, exist_ok=True,
        seed=20260820, deterministic=True, patience=0, close_mosaic=10, amp=True,
        cache="disk", plots=True, verbose=True, resume=args.resume and last.exists(),
    )
    save_dir = Path(result.save_dir)
    best = save_dir / "weights/best.pt"
    if not best.exists():
        raise RuntimeError(f"Best weights were not generated: {best}")
    output_model.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, output_model)
    model = YOLO(str(output_model))
    validation = model.val(data=str(data_yaml), split="val", imgsz=args.imgsz, batch=args.batch, device=0, workers=args.workers, project=str(RUN_ROOT), name="PCB插装0265_独立视频验证", exist_ok=True, plots=True, verbose=False)
    testing = model.val(data=str(data_yaml), split="test", imgsz=args.imgsz, batch=args.batch, device=0, workers=args.workers, project=str(RUN_ROOT), name="PCB插装0265_独立视频测试", exist_ok=True, plots=True, verbose=False)
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "gpu": torch.cuda.get_device_name(0),
        "model": str(output_model), "teacher": "YOLOE-26S open-vocabulary pseudo labels",
        "student": "YOLO26N", "epochs": args.epochs, "imgsz": args.imgsz, "batch": args.batch,
        "train_videos": args.train_videos,
        "validation_videos": ["station4/1.mp4", "station4/2.mp4"],
        "test_videos": ["station4/3.mp4", "station4/6.mp4"],
        "validation_teacher_agreement": {key: float(value) for key, value in validation.results_dict.items()},
        "test_teacher_agreement": {key: float(value) for key, value in testing.results_dict.items()},
        "processing_seconds": round(time.time() - started, 1),
        "truth_boundary": "Metrics measure agreement with automatic teacher candidates, not production accuracy. Human review and a locked human-labelled test set remain mandatory before deployment.",
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
