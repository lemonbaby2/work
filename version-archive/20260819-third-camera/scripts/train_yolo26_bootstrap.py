from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import torch
from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets/新增两视频_YOLOE26_SAHI细粒度预标注_待人工复核"
MODEL_DIR = ROOT / "models"
RUN_DIR = ROOT / "runs/frontier_yolo26"
OUTPUT_MODEL = MODEL_DIR / "yolo26n_两视频小目标蒸馏_待人工验收.pt"


def main() -> None:
    started = time.time()
    if not torch.cuda.is_available():
        raise RuntimeError("未检测到CUDA GPU，停止训练")
    previous = Path.cwd()
    os.chdir(MODEL_DIR)
    try:
        model = YOLO("yolo26n.pt")
    finally:
        os.chdir(previous)
    result = model.train(
        data=str(DATASET / "data.yaml"),
        epochs=12,
        imgsz=960,
        batch=4,
        device=0,
        workers=2,
        project=str(RUN_DIR),
        name="两视频小目标蒸馏",
        exist_ok=True,
        seed=20260816,
        deterministic=True,
        patience=6,
        close_mosaic=2,
        amp=True,
        plots=True,
        verbose=True,
    )
    save_dir = Path(result.save_dir)
    best = save_dir / "weights/best.pt"
    if not best.exists():
        raise RuntimeError(f"训练完成但未找到最佳权重：{best}")
    shutil.copy2(best, OUTPUT_MODEL)
    validation_model = YOLO(str(OUTPUT_MODEL))
    metrics = validation_model.val(
        data=str(DATASET / "data.yaml"),
        split="val",
        imgsz=960,
        batch=4,
        device=0,
        workers=2,
        project=str(RUN_DIR),
        name="两视频小目标蒸馏_验证",
        exist_ok=True,
        plots=True,
        verbose=False,
    )
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "gpu": torch.cuda.get_device_name(0),
        "model": str(OUTPUT_MODEL),
        "teacher": "YOLOE-26S-seg + 四窗口重叠切片",
        "student": "YOLO26N",
        "epochs": 12,
        "imgsz": 960,
        "train_run": str(save_dir),
        "metrics_against_teacher_pseudo_labels": {key: float(value) for key, value in metrics.results_dict.items()},
        "truth_boundary": "这些指标只衡量学生模型与自动教师预标注的一致性，不是对人工真值的量产精度；正式上线前必须人工复核并在冻结测试集重新验收。",
        "processing_seconds": round(time.time() - started, 1),
    }
    (ROOT / "qa/yolo26_bootstrap_training_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
