from __future__ import annotations

import csv
import json
import statistics
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch
from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config/pcb_model_registry.json"
DATA_YAML = ROOT / "datasets/PCB插装0264_0265联合_YOLOE关键帧预标注_待人工复核/data.yaml"
OUTPUT = ROOT / "web/analysis/pcb_model_comparison"
REPORT = ROOT / "qa/pcb_model_comparison_report.json"


def metric(metrics: dict, name: str) -> float:
    value = metrics.get(name)
    return round(float(value), 6) if value is not None else 0.0


def training_curves(models: list[dict]) -> str:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    for item in models:
        with (ROOT / item["results_csv"]).open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        epochs = [int(float(row["epoch"])) + 1 for row in rows]
        axes[0].plot(epochs, [float(row["metrics/mAP50(B)"]) for row in rows], label=item["name"])
        axes[1].plot(epochs, [float(row["metrics/mAP50-95(B)"]) for row in rows], label=item["name"])
    for axis, title in zip(axes, ("mAP50 by epoch", "mAP50-95 by epoch")):
        axis.set(title=title, xlabel="Epoch", ylabel="Score", xlim=(1, 50), ylim=(0, 1))
        axis.grid(alpha=.25)
        axis.legend(fontsize=8)
    fig.tight_layout()
    path = OUTPUT / "01_三模型50轮精度曲线.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path.name


def metric_chart(results: list[dict]) -> str:
    keys = ["precision", "recall", "map50", "map50_95"]
    labels = ["Precision", "Recall", "mAP50", "mAP50-95"]
    positions = np.arange(len(keys))
    width = .24
    fig, axis = plt.subplots(figsize=(11, 5.2))
    for index, result in enumerate(results):
        values = [result["metrics"][key] for key in keys]
        axis.bar(positions + (index - 1) * width, values, width, label=result["name"])
    axis.set_xticks(positions, labels)
    axis.set_ylim(0, 1)
    axis.set_ylabel("Score")
    axis.set_title("Same held-out videos / same settings")
    axis.grid(axis="y", alpha=.25)
    axis.legend(fontsize=8)
    fig.tight_layout()
    path = OUTPUT / "02_三模型统一测试精度对比.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path.name


def deployment_chart(results: list[dict]) -> str:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8))
    names = [item["name"].replace("YOLO26N · ", "") for item in results]
    axes[0].bar(names, [item["latency_p95_ms"] for item in results], color="#297ba5")
    axes[0].set(title="P95 latency", ylabel="ms")
    axes[1].bar(names, [item["peak_vram_mb"] for item in results], color="#0d8f79")
    axes[1].set(title="Peak CUDA memory", ylabel="MB")
    for axis in axes:
        axis.tick_params(axis="x", rotation=12)
        axis.grid(axis="y", alpha=.25)
    fig.tight_layout()
    path = OUTPUT / "03_三模型延迟显存对比.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path.name


def main() -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required")
    configured = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["models"]
    missing = [str(ROOT / item["weight"]) for item in configured if not (ROOT / item["weight"]).is_file()]
    if missing:
        raise RuntimeError(f"Models are not complete: {missing}")
    test_images = sorted((ROOT / "datasets/PCB插装0265_YOLOE关键帧预标注_待人工复核/images/test").glob("*.jpg"))
    if not test_images:
        raise RuntimeError("Held-out test images are missing")
    samples = test_images[::max(1, len(test_images) // 120)][:120]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results = []
    for item in configured:
        model = YOLO(str(ROOT / item["weight"]))
        validation = model.val(data=str(DATA_YAML), split="test", imgsz=960, batch=16, device=0, workers=6, plots=True, verbose=False, project=str(OUTPUT), name=item["id"], exist_ok=True)
        for image in samples[:8]:
            model.predict(str(image), imgsz=960, conf=.25, device=0, verbose=False)
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
        latencies = []
        for image in samples:
            started = time.perf_counter()
            model.predict(str(image), imgsz=960, conf=.25, device=0, verbose=False)
            torch.cuda.synchronize()
            latencies.append((time.perf_counter() - started) * 1000)
        metrics = validation.results_dict
        result = {
            "id": item["id"], "name": item["name"], "weight": item["weight"], "epochs": item["epochs"],
            "test_images": len(test_images), "latency_samples": len(samples),
            "metrics": {
                "precision": metric(metrics, "metrics/precision(B)"),
                "recall": metric(metrics, "metrics/recall(B)"),
                "map50": metric(metrics, "metrics/mAP50(B)"),
                "map50_95": metric(metrics, "metrics/mAP50-95(B)"),
            },
            "latency_mean_ms": round(statistics.mean(latencies), 3),
            "latency_p95_ms": round(float(np.percentile(latencies, 95)), 3),
            "fps_from_mean": round(1000 / statistics.mean(latencies), 2),
            "peak_vram_mb": round(torch.cuda.max_memory_allocated() / 1024 / 1024, 1),
            "release": "HOLD",
        }
        results.append(result)
        del model
        torch.cuda.empty_cache()
    chart_names = [training_curves(configured), metric_chart(results), deployment_chart(results)]
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "gpu": torch.cuda.get_device_name(0), "imgsz": 960, "confidence": .25,
        "test_protocol": "Same held-out complete station4 videos, same image size, confidence and DGX Spark GPU.",
        "models": results,
        "charts": [{"title": name.removesuffix(".png").split("_", 1)[-1], "path": f"analysis/pcb_model_comparison/{name}"} for name in chart_names],
        "truth_boundary": "Metrics measure agreement with automatic YOLOE teacher candidates. All models remain HOLD until a locked human-labelled test set and NG recall gate are passed.",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
