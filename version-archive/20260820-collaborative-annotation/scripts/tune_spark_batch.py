from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import torch
import yaml
from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets" / "新增两视频_YOLOE26_SAHI细粒度预标注_待人工复核"
OUTPUT = ROOT / "qa" / "spark_batch_profile.json"


def runtime_data_yaml() -> Path:
    source = yaml.safe_load((DATASET / "data.yaml").read_text(encoding="utf-8"))
    source["path"] = str(DATASET)
    path = ROOT / "runtime" / "spark_automotive_dataset.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(source, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def benchmark_model(model_path: Path, batches: list[int], imgsz: int) -> dict[str, object]:
    model = YOLO(str(model_path))
    rows = []
    for batch in batches:
        tensor = torch.zeros((batch, 3, imgsz, imgsz), device="cuda", dtype=torch.float32)
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        try:
            model.predict(tensor, device=0, half=True, verbose=False)
            torch.cuda.synchronize()
            timings = []
            for _ in range(3):
                started = time.perf_counter()
                model.predict(tensor, device=0, half=True, verbose=False)
                torch.cuda.synchronize()
                timings.append(time.perf_counter() - started)
            elapsed = statistics.median(timings)
            rows.append({"batch": batch, "ok": True, "latency_ms": round(elapsed * 1000, 2), "per_image_ms": round(elapsed * 1000 / batch, 3), "images_per_second": round(batch / elapsed, 2), "peak_cuda_mb": round(torch.cuda.max_memory_allocated() / 1024 / 1024, 1)})
        except RuntimeError as exc:
            rows.append({"batch": batch, "ok": False, "error": str(exc)[:500]})
            if "out of memory" in str(exc).lower():
                break
        finally:
            del tensor
            torch.cuda.empty_cache()
    successful = [row for row in rows if row["ok"]]
    best_realtime = max(successful, key=lambda row: float(row["images_per_second"]), default={"batch": 1})
    max_batch = max((int(row["batch"]) for row in successful), default=1)
    return {"model": str(model_path.relative_to(ROOT)), "imgsz": imgsz, "results": rows, "recommended_realtime_batch": int(best_realtime["batch"]), "max_tested_batch": max_batch, "recommended_training_batch": max(1, min(8, max_batch // 2))}


def calibrate_training(batch: int, imgsz: int) -> dict[str, object]:
    model = YOLO(str(ROOT / "models" / "yolo26n.pt"))
    started = time.perf_counter()
    result = model.train(
        data=str(runtime_data_yaml()), epochs=1, fraction=0.1, imgsz=imgsz, batch=batch,
        device=0, workers=2, amp=True, cache=False, val=False, plots=False, save=False,
        project=str(ROOT / "runs" / "spark_batch_calibration"), name=f"batch{batch}_{imgsz}", exist_ok=True, verbose=False,
    )
    return {"ok": True, "batch": batch, "imgsz": imgsz, "elapsed_s": round(time.perf_counter() - started, 2), "save_dir": str(result.save_dir), "truth_boundary": "只用于Spark batch和吞吐校准，标签是待人工复核预标注，不是量产精度训练。"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Tune inference/training batch on NVIDIA DGX Spark")
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--batches", default="1,4,8,16")
    parser.add_argument("--train-calibration", action="store_true")
    args = parser.parse_args()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA不可用，停止Spark batch调参")
    batches = [int(value) for value in args.batches.split(",") if value.strip()]
    model_paths = [ROOT / "models" / "yolo26n.pt", ROOT / "models" / "yoloe-26s-seg.pt", ROOT / "models" / "yolov8s-worldv2.pt"]
    profiles = [benchmark_model(path, batches, args.imgsz) for path in model_paths if path.exists()]
    training_batch = min((int(item["recommended_training_batch"]) for item in profiles), default=4)
    previous = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "gpu": torch.cuda.get_device_name(0),
        "cuda_memory_mb": round(torch.cuda.get_device_properties(0).total_memory / 1024 / 1024),
        "profiles": profiles,
        "recommended_training": {"batch": training_batch, "imgsz": args.imgsz, "workers": 2, "amp": True},
        "training_calibration": calibrate_training(training_batch, args.imgsz) if args.train_calibration else previous.get("training_calibration", {"ok": False, "reason": "未要求执行训练校准"}),
        "truth_boundary": "batch推荐基于当前Spark和已安装模型；PCB与模塑需导入各自产线真值后重新调参。",
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
