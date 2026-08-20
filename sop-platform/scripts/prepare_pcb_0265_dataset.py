from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLOE


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parents[1]
DATASET = ROOT / "datasets/PCB插装0265_YOLOE关键帧预标注_待人工复核"
PROXY = ROOT / "web/media/local/DJI_20260819151908_0265_D_720p.mp4"
SOURCE_VIDEO = PROJECT_ROOT / "视频数据/8月19号/DJI_20260819151908_0265_D.MP4"
STATION4 = PROJECT_ROOT / "视频数据/宁波SoP项目/8月19号（工位4漏焊）"
MODEL_PATH = ROOT / "models/yoloe-26s-seg.pt"
DATA_LOG = ROOT / "web/data/video_0265_frame_annotations.jsonl"
CANDIDATE_LOG = ROOT / "web/data/video_0265_fine_object_candidates.jsonl"
TARGET_ID = "video_0265"

PROMPTS = [
    "printed circuit board",
    "human hand",
    "electronic connector",
    "integrated circuit chip",
    "capacitor",
    "plastic parts bin",
    "assembly fixture",
    "soldering iron",
]
CLASS_NAMES = [
    "PCB板",
    "操作人员手部",
    "连接器/插件",
    "芯片",
    "电容",
    "料盒",
    "装配治具",
    "烙铁/焊枪",
    "取料动作候选",
    "插装/按压动作候选",
]
PROMPT_TO_CLASS = dict(zip(PROMPTS, CLASS_NAMES))
CLASS_IDS = {name: index for index, name in enumerate(CLASS_NAMES)}
MIN_CONFIDENCE = {
    "printed circuit board": 0.14,
    "human hand": 0.065,
    "electronic connector": 0.07,
    "integrated circuit chip": 0.07,
    "capacitor": 0.07,
    "plastic parts bin": 0.18,
    "assembly fixture": 0.10,
    "soldering iron": 0.24,
}

# Target video uses a fixed overhead camera. These are coarse business regions,
# not component truth, and remain pending until the operator checks the setup.
TARGET_REGIONS = [
    ("PCB治具区", "装配治具", [0.035, 0.075, 0.915, 0.965]),
    ("左侧PCB区", "PCB板", [0.055, 0.105, 0.385, 0.785]),
    ("中间PCB区", "PCB板", [0.315, 0.105, 0.655, 0.800]),
    ("右侧PCB区", "PCB板", [0.575, 0.105, 0.900, 0.800]),
    ("左料盒区", "料盒", [0.000, 0.650, 0.285, 1.000]),
    ("中料盒区", "料盒", [0.230, 0.700, 0.590, 1.000]),
    ("右料盒区", "料盒", [0.825, 0.330, 1.000, 0.950]),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build isolated PCB train/val/test pseudo-label data")
    parser.add_argument("--max-frames", type=int, default=0, help="Per-video decode limit for smoke tests")
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--video-id", default="0265", choices=("0264", "0265"))
    parser.add_argument("--source-video", type=Path)
    parser.add_argument("--proxy", type=Path)
    parser.add_argument("--dataset-name")
    return parser.parse_args()


def configure_target(args: argparse.Namespace) -> None:
    global DATASET, PROXY, SOURCE_VIDEO, DATA_LOG, CANDIDATE_LOG, TARGET_ID
    TARGET_ID = f"video_{args.video_id}"
    default_filename = {
        "0264": "DJI_20260819145537_0264_D.MP4",
        "0265": "DJI_20260819151908_0265_D.MP4",
    }[args.video_id]
    SOURCE_VIDEO = args.source_video or PROJECT_ROOT / "视频数据/8月19号" / default_filename
    PROXY = args.proxy or ROOT / "web/media/local" / default_filename.replace(".MP4", "_720p.mp4")
    DATASET = ROOT / "datasets" / (args.dataset_name or f"PCB插装{args.video_id}_YOLOE关键帧预标注_待人工复核")
    DATA_LOG = ROOT / "web/data" / f"video_{args.video_id}_frame_annotations.jsonl"
    CANDIDATE_LOG = ROOT / "web/data" / f"video_{args.video_id}_fine_object_candidates.jsonl"


def yolo_line(class_name: str, box: list[float]) -> str:
    x1, y1, x2, y2 = [min(1.0, max(0.0, float(value))) for value in box]
    return f"{CLASS_IDS[class_name]} {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} {x2 - x1:.6f} {y2 - y1:.6f}"


def normalized_box(xyxy: list[float], width: int, height: int) -> list[float]:
    return [xyxy[0] / width, xyxy[1] / height, xyxy[2] / width, xyxy[3] / height]


def region_for_center(x: float, y: float) -> str:
    for region, _, box in TARGET_REGIONS:
        if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
            return region
    return "人员操作区"


def action_for_hand(box: list[float], motion_score: float) -> tuple[str, str]:
    center_x = (box[0] + box[2]) / 2
    center_y = (box[1] + box[3]) / 2
    region = region_for_center(center_x, center_y)
    if "料盒" in region:
        return "取料动作候选", region
    return ("插装/按压动作候选" if motion_score >= 4.0 else "插装/按压动作候选"), region


def load_model() -> YOLOE:
    previous = Path.cwd()
    os.chdir(ROOT / "models")
    try:
        model = YOLOE(str(MODEL_PATH))
        embeddings = model.get_text_pe(PROMPTS)
        model.set_classes(PROMPTS, embeddings)
    finally:
        os.chdir(previous)
    return model


def flush_batch(model: YOLOE, pending: list[dict], imgsz: int, manifest: list[dict], target_log: list[dict], counts: Counter) -> None:
    if not pending:
        return
    results = model.predict([item["frame"] for item in pending], imgsz=imgsz, conf=0.055, iou=0.45, device=0, verbose=False)
    for item, result in zip(pending, results):
        frame = item["frame"]
        height, width = frame.shape[:2]
        annotations: list[dict] = []
        label_lines: list[str] = []
        if item["target"]:
            for region, class_name, box in TARGET_REGIONS:
                annotations.append({
                    "label": class_name,
                    "region": region,
                    "confidence": None,
                    "xyxy": [round(box[0] * width, 1), round(box[1] * height, 1), round(box[2] * width, 1), round(box[3] * height, 1)],
                    "source": "固定工位业务区域候选",
                    "review_status": "pending",
                })
                label_lines.append(yolo_line(class_name, box))
                counts[class_name] += 1
        boxes = result.boxes or []
        for detection in boxes:
            prompt = PROMPTS[int(detection.cls.item())]
            confidence = float(detection.conf.item())
            if confidence < MIN_CONFIDENCE[prompt]:
                continue
            pixels = [float(value) for value in detection.xyxy[0].tolist()]
            box = normalized_box(pixels, width, height)
            class_name = PROMPT_TO_CLASS[prompt]
            region = region_for_center((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) if item["target"] else "独立PCB验证工位"
            annotation = {
                "label": class_name,
                "region": region,
                "confidence": round(confidence, 4),
                "xyxy": [round(value, 1) for value in pixels],
                "source": "YOLOE-26S开放词汇预标注",
                "review_status": "pending",
            }
            annotations.append(annotation)
            label_lines.append(yolo_line(class_name, box))
            counts[class_name] += 1
            if prompt == "human hand" and item["target"]:
                action_name, action_region = action_for_hand(box, item["motion"])
                annotations.append({
                    "label": action_name,
                    "region": action_region,
                    "confidence": round(min(0.75, confidence + 0.20), 4),
                    "xyxy": [round(value, 1) for value in pixels],
                    "source": "手部位置与运动强度动作候选",
                    "review_status": "pending",
                })
                label_lines.append(yolo_line(action_name, box))
                counts[action_name] += 1
        stem = f"{item['source_id']}_{item['frame_index']:06d}"
        image_path = DATASET / "images" / item["split"] / f"{stem}.jpg"
        label_path = DATASET / "labels" / item["split"] / f"{stem}.txt"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(image_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        label_path.write_text("\n".join(label_lines) + ("\n" if label_lines else ""), encoding="utf-8")
        record = {
            "source_id": item["source_id"], "source_video": str(item["path"]), "split": item["split"],
            "frame": item["frame_index"], "time_s": round(item["time_s"], 3), "motion_score": round(item["motion"], 3),
            "image": str(image_path.relative_to(DATASET)), "label": str(label_path.relative_to(DATASET)),
            "annotations": annotations, "overall_status": "pending_human_review",
        }
        manifest.append(record)
        if item["target"]:
            target_log.append({"frame": item["frame_index"], "time_s": round(item["time_s"], 3), "detections": annotations})


def process_video(model: YOLOE, source: dict, args: argparse.Namespace, manifest: list[dict], target_log: list[dict], counts: Counter) -> dict:
    capture = cv2.VideoCapture(str(source["path"]))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open video: {source['path']}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30)
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    pending: list[dict] = []
    previous_gray = None
    selected = 0
    decoded = 0
    while True:
        ok, frame = capture.read()
        if not ok or (args.max_frames and decoded >= args.max_frames):
            break
        gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 90))
        motion = float(np.mean(cv2.absdiff(gray, previous_gray))) if previous_gray is not None else 0.0
        previous_gray = gray
        stable_interval = max(1, round(fps))
        dynamic_interval = max(1, round(fps / 6))
        should_sample = decoded % stable_interval == 0 or (motion >= 2.6 and decoded % dynamic_interval == 0)
        if should_sample:
            pending.append({
                **source, "frame": frame.copy(), "frame_index": decoded, "time_s": decoded / fps,
                "motion": motion, "target": source["source_id"] == TARGET_ID,
            })
            selected += 1
            if len(pending) >= args.batch:
                flush_batch(model, pending, args.imgsz, manifest, target_log, counts)
                pending.clear()
        decoded += 1
        if decoded % max(1, round(fps * 30)) == 0:
            print(json.dumps({"video": source["source_id"], "decoded": decoded, "total": total, "selected": selected}, ensure_ascii=False), flush=True)
    flush_batch(model, pending, args.imgsz, manifest, target_log, counts)
    capture.release()
    return {"source_id": source["source_id"], "split": source["split"], "decoded_frames": decoded, "selected_keyframes": selected, "fps": fps, "duration_s": round(decoded / fps, 3)}


def update_video_catalog(summary: dict) -> None:
    path = ROOT / "web/data/videos.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    entry = {
        "id": TARGET_ID,
        "display_name": f"视频{'五' if TARGET_ID == 'video_0264' else '六'}｜PCB元器件人工插装作业（{TARGET_ID[-4:]}）",
        "source": str(SOURCE_VIDEO),
        "video": f"media/local/{PROXY.name}",
        "source_video": f"media/local/{PROXY.name}",
        "frames": summary["decoded_frames"], "fps": 30.0, "resolution": "1280×720", "duration_s": summary["duration_s"],
        "algorithm": "YOLOE-26S开放词汇 + 固定工位区域 + 运动关键帧 + 人工复核",
        "parts": [{"id": f"R{index:02d}", "label": region, "roi": box} for index, (region, _, box) in enumerate(TARGET_REGIONS, 1)],
        "steps": [
            {"id": "S01", "label": "取料候选", "start_s": 0.0, "end_s": summary["duration_s"] * 0.25, "roi": [0.0, 0.3, 1.0, 1.0]},
            {"id": "S02", "label": "元器件对位候选", "start_s": summary["duration_s"] * 0.25, "end_s": summary["duration_s"] * 0.50, "roi": [0.03, 0.07, 0.92, 0.97]},
            {"id": "S03", "label": "插装/按压候选", "start_s": summary["duration_s"] * 0.50, "end_s": summary["duration_s"] * 0.75, "roi": [0.03, 0.07, 0.92, 0.97]},
            {"id": "S04", "label": "完成复检候选", "start_s": summary["duration_s"] * 0.75, "end_s": summary["duration_s"], "roi": [0.03, 0.07, 0.92, 0.97]},
        ],
        "visual_result": "自动预标注待人工复核", "production_release": "HOLD",
        "release_reason": "开放词汇框、动作区间和NG均未完成人工复核，禁止作为量产放行依据",
        "evidence_boundary": "固定区域与YOLOE候选仅用于提高标注效率；所有NG、稀有类别、遮挡和工序边界必须人工复核",
    }
    catalog["videos"] = [item for item in catalog["videos"] if item.get("id") != TARGET_ID] + [entry]
    catalog["totals"] = {
        "videos": len(catalog["videos"]),
        "frames": sum(int(item.get("frames", 0)) for item in catalog["videos"]),
        "duration_s": round(sum(float(item.get("duration_s", 0)) for item in catalog["videos"]), 2),
        "steps": sum(len(item.get("steps", [])) for item in catalog["videos"]),
    }
    catalog[f"pcb_{TARGET_ID[-4:]}_extension"] = {"dataset": str(DATASET), "split_policy": f"{TARGET_ID[-4:]}完整视频仅训练；工位4的1/2完整视频验证；3/6完整视频测试", "truth_status": "全部自动预标注待人工复核"}
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    configure_target(args)
    if not PROXY.exists() or PROXY.stat().st_size == 0:
        raise RuntimeError(f"Target proxy is missing: {PROXY}")
    sources = [
        {"source_id": TARGET_ID, "path": PROXY, "split": "train"},
        {"source_id": "station4_1", "path": STATION4 / "1.mp4", "split": "val"},
        {"source_id": "station4_2", "path": STATION4 / "2.mp4", "split": "val"},
        {"source_id": "station4_3", "path": STATION4 / "3.mp4", "split": "test"},
        {"source_id": "station4_6", "path": STATION4 / "6.mp4", "split": "test"},
    ]
    started = time.time()
    model = load_model()
    manifest: list[dict] = []
    target_log: list[dict] = []
    counts: Counter = Counter()
    summaries = [process_video(model, source, args, manifest, target_log, counts) for source in sources]
    DATASET.mkdir(parents=True, exist_ok=True)
    (DATASET / "manifest.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in manifest) + "\n", encoding="utf-8")
    (DATASET / "classes.json").write_text(json.dumps({"names": {index: name for index, name in enumerate(CLASS_NAMES)}}, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATASET / "data.yaml").write_text(
        f"path: {DATASET}\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n" + "".join(f"  {index}: {name}\n" for index, name in enumerate(CLASS_NAMES)), encoding="utf-8"
    )
    DATA_LOG.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in target_log) + "\n", encoding="utf-8")
    CANDIDATE_LOG.write_text("\n".join(json.dumps({"frame": item["frame"], "time_s": item["time_s"], "candidates": [annotation for annotation in item["detections"] if "候选" in annotation["label"]]}, ensure_ascii=False) for item in target_log) + "\n", encoding="utf-8")
    target_summary = next(item for item in summaries if item["source_id"] == TARGET_ID)
    update_video_catalog(target_summary)
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "model": str(MODEL_PATH),
        "sources": summaries, "sampled_images": len(manifest), "annotations": sum(counts.values()),
        "class_counts": counts, "dataset": str(DATASET), "processing_seconds": round(time.time() - started, 1),
        "split_policy": "No adjacent-frame leakage: each source video belongs to exactly one split.",
        "truth_boundary": "All boxes and action labels are automatic candidates. NG, rare classes, occlusion, direction changes and process boundaries require human review.",
    }
    (DATASET / "dataset_stats.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
