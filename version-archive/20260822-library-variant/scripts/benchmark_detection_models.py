#!/usr/bin/env python3
"""在项目验证图上比较工业检测模型，并生成中文论文式图表。

注意：当前验证集标签仍标记为“待人工复核”，因此 custom 模型的
precision/recall/F1 是预标注一致性代理指标，不是量产真值精度。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "datasets" / "新增两视频_YOLOE26_SAHI细粒度预标注_待人工复核"
DEFAULT_OUT = ROOT / "web" / "analysis" / "model_benchmark"


def configure_font() -> str:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            font_manager.fontManager.addfont(candidate)
            name = font_manager.FontProperties(fname=candidate).get_name()
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False
            return name
    plt.rcParams["axes.unicode_minus"] = False
    return "sans-serif"


def load_ground_truth(label_path: Path) -> list[tuple[int, float, float, float, float]]:
    if not label_path.exists():
        return []
    rows = []
    for line in label_path.read_text(encoding="utf-8").splitlines():
        values = line.split()
        if len(values) != 5:
            continue
        rows.append(tuple([int(values[0]), *map(float, values[1:])]))
    return rows


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1, ix2, iy2 = max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    return inter / max(area_a + area_b - inter, 1e-9)


def evaluate_proxy(gt: list[tuple[int, float, float, float, float]], predictions: list[dict[str, Any]]) -> tuple[int, int, int]:
    matched: set[int] = set()
    tp = 0
    for pred in sorted(predictions, key=lambda item: item["confidence"], reverse=True):
        best_index, best_iou = -1, 0.0
        box = tuple(pred["xyxy_norm"])
        for index, item in enumerate(gt):
            if index in matched or item[0] != pred["class_id"]:
                continue
            candidate_iou = iou(box, tuple(item[1:]))
            if candidate_iou > best_iou:
                best_index, best_iou = index, candidate_iou
        if best_iou >= 0.5:
            matched.add(best_index)
            tp += 1
    return tp, len(predictions) - tp, len(gt) - tp


def predict_one(model: Any, image: Path, device: str, custom: bool) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    result = model.predict(source=str(image), imgsz=640, conf=0.25, device=device, half=device != "cpu", verbose=False)[0]
    elapsed = (time.perf_counter() - started) * 1000
    height, width = result.orig_shape
    predictions = []
    if result.boxes is not None:
        for box, confidence, class_id in zip(result.boxes.xyxy.cpu().tolist(), result.boxes.conf.cpu().tolist(), result.boxes.cls.cpu().tolist()):
            x1, y1, x2, y2 = box
            predictions.append({
                "class_id": int(class_id),
                "confidence": float(confidence),
                "xyxy_norm": [x1 / width, y1 / height, x2 / width, y2 / height],
            })
    return predictions, elapsed


def draw_charts(rows: list[dict[str, Any]], output: Path) -> list[str]:
    output.mkdir(parents=True, exist_ok=True)
    names = [row["name"] for row in rows]
    colors = ["#0d8f79", "#297ba5", "#d48716", "#7a5aa6", "#c94842"][: len(rows)]
    generated = []

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.8), dpi=160)
    axes[0].bar(names, [row["latency_ms"] for row in rows], color=colors)
    axes[0].set_title("单帧平均推理延迟（越低越好）")
    axes[0].set_ylabel("毫秒 / 帧")
    axes[0].tick_params(axis="x", rotation=22)
    axes[1].bar(names, [row["fps"] for row in rows], color=colors)
    axes[1].set_title("理论推理吞吐（越高越好）")
    axes[1].set_ylabel("帧 / 秒")
    axes[1].tick_params(axis="x", rotation=22)
    fig.suptitle("宁波工业视觉模型性能对比：延迟与吞吐", fontsize=16, fontweight="bold")
    fig.tight_layout()
    path = output / "01_模型延迟与FPS对比.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    generated.append(path.name)

    fig, ax = plt.subplots(figsize=(11, 5.8), dpi=160)
    x = np.arange(len(rows))
    width = 0.25
    ax.bar(x - width, [row["mean_confidence"] * 100 for row in rows], width, label="平均置信度", color="#0d8f79")
    ax.bar(x, [row["non_empty_ratio"] * 100 for row in rows], width, label="有目标帧比例", color="#297ba5")
    ax.bar(x + width, [row["detections_per_image"] for row in rows], width, label="平均目标数（原值）", color="#d48716")
    ax.set_xticks(x, names, rotation=22)
    ax.set_ylabel("百分比 / 数量")
    ax.set_title("检测稳定性与置信度对比（运行数据）")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    path = output / "02_模型稳定性与置信度.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    generated.append(path.name)

    sample_names = [Path(item).stem for item in rows[0]["sample_images"]]
    matrix = np.array([[row["sample_detection_counts"][index] for index in range(len(sample_names))] for row in rows])
    fig, ax = plt.subplots(figsize=(14, 5.8), dpi=160)
    image = ax.imshow(matrix, aspect="auto", cmap="YlGnBu")
    ax.set_yticks(np.arange(len(names)), names)
    ax.set_xticks(np.arange(len(sample_names)), sample_names, rotation=45, ha="right")
    ax.set_title("逐图检测数量热力图（用于发现模型输出差异）")
    ax.set_xlabel("验证图像")
    ax.set_ylabel("模型")
    fig.colorbar(image, ax=ax, label="检测框数量")
    fig.tight_layout()
    path = output / "03_逐图检测数量热力图.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    generated.append(path.name)

    fig, ax = plt.subplots(figsize=(10.5, 6), dpi=160)
    score_latency = max(row["latency_ms"] for row in rows)
    score_det = max(row["detections_per_image"] for row in rows) or 1
    for row, color in zip(rows, colors):
        speed = max(0, 100 * (1 - row["latency_ms"] / max(score_latency, 1)))
        stability = row["non_empty_ratio"] * 100
        signal = min(100, 100 * row["mean_confidence"])
        industrial = (speed * 0.4 + stability * 0.3 + signal * 0.3)
        ax.scatter(row["latency_ms"], row["mean_confidence"] * 100, s=max(80, row["detections_per_image"] * 80), color=color, label=f"{row['name']} 综合参考分 {industrial:.1f}")
        ax.annotate(row["name"], (row["latency_ms"], row["mean_confidence"] * 100), xytext=(6, 6), textcoords="offset points")
    ax.set_xlabel("平均推理延迟（毫秒，越低越好）")
    ax.set_ylabel("平均置信度（%，仅运行信号）")
    ax.set_title("工业部署选型散点图：速度 × 置信度")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, loc="best")
    fig.tight_layout()
    path = output / "04_工业部署选型散点图.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    generated.append(path.name)
    return generated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--device", default="0")
    parser.add_argument("--limit", type=int, default=24)
    args = parser.parse_args()
    configure_font()
    from ultralytics import YOLO

    image_paths = sorted((args.dataset / "images" / "val").glob("*.jpg"))[: args.limit]
    if not image_paths:
        raise SystemExit(f"验证图像不存在: {args.dataset / 'images' / 'val'}")
    model_specs = [
        ("YOLOv11n", ROOT / "models" / "yolo11n.pt", False),
        ("YOLO26n", ROOT / "models" / "yolo26n.pt", False),
        ("YOLO26n工业学生", ROOT / "models" / "yolo26n_两视频小目标蒸馏_待人工验收.pt", True),
        ("YOLOv8-World", ROOT / "models" / "yolov8s-worldv2.pt", False),
    ]
    rows = []
    for name, path, custom in model_specs:
        print(f"loading {name}: {path.name}")
        model = YOLO(str(path))
        for image in image_paths[:2]:
            model.predict(source=str(image), imgsz=640, device=args.device, half=args.device != "cpu", verbose=False)
        latencies, all_predictions, counts = [], [], []
        tp = fp = fn = 0
        for image in image_paths:
            predictions, elapsed = predict_one(model, image, args.device, custom)
            latencies.append(elapsed)
            counts.append(len(predictions))
            all_predictions.extend(predictions)
            if custom:
                current_tp, current_fp, current_fn = evaluate_proxy(load_ground_truth(args.dataset / "labels" / "val" / f"{image.stem}.txt"), predictions)
                tp += current_tp
                fp += current_fp
                fn += current_fn
        mean_latency = float(np.mean(latencies))
        confidences = [item["confidence"] for item in all_predictions]
        precision = tp / max(tp + fp, 1) if custom else None
        recall = tp / max(tp + fn, 1) if custom else None
        f1 = 2 * precision * recall / max(precision + recall, 1e-9) if custom and precision + recall else None
        rows.append({
            "name": name,
            "model": str(path.relative_to(ROOT)),
            "custom_label_space": custom,
            "images": len(image_paths),
            "latency_ms": round(mean_latency, 3),
            "fps": round(1000 / max(mean_latency, 1e-6), 2),
            "detections_total": len(all_predictions),
            "detections_per_image": round(float(np.mean(counts)), 3),
            "mean_confidence": round(float(np.mean(confidences)) if confidences else 0, 5),
            "non_empty_ratio": round(sum(count > 0 for count in counts) / len(counts), 5),
            "sample_images": [str(item) for item in image_paths],
            "sample_detection_counts": counts,
            "proxy_precision": precision,
            "proxy_recall": recall,
            "proxy_f1": f1,
        })
        del model
    generated = draw_charts(rows, args.output)
    payload = {
        "title": "宁波工业视觉模型性能对比",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset": str(args.dataset.relative_to(ROOT)),
        "label_status": "待人工复核预标注",
        "truth_boundary": "YOLO26n工业学生的precision/recall/F1是预标注一致性代理指标；其他模型未在同一类别空间上计算精度，不得作为量产精度结论。",
        "models": rows,
        "charts": generated,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "benchmark_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
