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

PROMPT_SPECS = [
    ("printed circuit board", "PCB板"),
    ("human hand", "操作人员手部"),
    ("electronic connector", "连接器/插件"),
    ("integrated circuit chip", "芯片"),
    ("electrolytic capacitor electronic component", "电容"),
    ("inductor coil electronic component", "电感"),
    ("resistor electronic component", "电阻"),
    ("small through-hole electronic component", "电子元器件/物料候选"),
    ("plastic parts bin", "料盒"),
    ("assembly fixture", "装配治具"),
    ("soldering iron", "烙铁/焊枪"),
]
PROMPTS = [prompt for prompt, _ in PROMPT_SPECS]
CLASS_NAMES = [
    "PCB板",
    "操作人员手部",
    "连接器/插件",
    "芯片",
    "电容",
    "电感",
    "电阻",
    "电子元器件/物料候选",
    "料盒",
    "装配治具",
    "烙铁/焊枪",
    "电容物料区",
    "电感物料区",
    "混合物料区",
    "取料动作候选",
    "插装/按压动作候选",
]
PROMPT_TO_CLASS = dict(PROMPT_SPECS)
CLASS_IDS = {name: index for index, name in enumerate(CLASS_NAMES)}
MIN_CONFIDENCE = {
    "printed circuit board": 0.10,
    "human hand": 0.05,
    "electronic connector": 0.03,
    "integrated circuit chip": 0.03,
    "electrolytic capacitor electronic component": 0.025,
    "inductor coil electronic component": 0.025,
    "resistor electronic component": 0.025,
    "small through-hole electronic component": 0.02,
    "plastic parts bin": 0.14,
    "assembly fixture": 0.10,
    "soldering iron": 0.24,
}
COMPONENT_CLASSES = {"连接器/插件", "芯片", "电容", "电感", "电阻", "电子元器件/物料候选"}

# Target video uses a fixed overhead camera. These are coarse business regions,
# not component truth, and remain pending until the operator checks the setup.
TARGET_REGION_PROFILES = {
    "video_0264": [
        ("左侧PCB区", "PCB板", [0.000, 0.105, 0.345, 0.735]),
        ("中间PCB区", "PCB板", [0.310, 0.105, 0.680, 0.750]),
        ("右侧PCB区", "PCB板", [0.645, 0.105, 0.985, 0.750]),
        ("电容物料区", "电容物料区", [0.000, 0.650, 0.505, 1.000]),
        ("电感物料区", "电感物料区", [0.470, 0.645, 0.875, 1.000]),
        ("混合物料区", "混合物料区", [0.840, 0.350, 1.000, 0.950]),
    ],
    "video_0265": [
        ("左侧PCB区", "PCB板", [0.000, 0.105, 0.360, 0.770]),
        ("中间PCB区", "PCB板", [0.315, 0.105, 0.680, 0.790]),
        ("右侧PCB区", "PCB板", [0.635, 0.105, 0.965, 0.790]),
        ("电容物料区", "电容物料区", [0.000, 0.690, 0.365, 1.000]),
        ("电感物料区", "电感物料区", [0.285, 0.690, 0.735, 1.000]),
        ("混合物料区", "混合物料区", [0.850, 0.350, 1.000, 0.950]),
    ],
}


def target_regions() -> list[tuple[str, str, list[float]]]:
    return TARGET_REGION_PROFILES[TARGET_ID]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build isolated PCB train/val/test pseudo-label data")
    parser.add_argument("--max-frames", type=int, default=0, help="Per-video decode limit for smoke tests")
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--video-id", default="0265", choices=("0264", "0265"))
    parser.add_argument("--source-video", type=Path)
    parser.add_argument("--proxy", type=Path)
    parser.add_argument("--dataset-name")
    parser.add_argument("--skip-catalog", action="store_true", help="Do not update web video data during smoke tests")
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
    DATASET = ROOT / "datasets" / (args.dataset_name or f"PCB插装{args.video_id}_YOLOE_ROI增强_待人工复核")
    DATA_LOG = ROOT / "web/data" / f"video_{args.video_id}_frame_annotations.jsonl"
    CANDIDATE_LOG = ROOT / "web/data" / f"video_{args.video_id}_fine_object_candidates.jsonl"


def yolo_line(class_name: str, box: list[float]) -> str:
    x1, y1, x2, y2 = [min(1.0, max(0.0, float(value))) for value in box]
    return f"{CLASS_IDS[class_name]} {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} {x2 - x1:.6f} {y2 - y1:.6f}"


def normalized_box(xyxy: list[float], width: int, height: int) -> list[float]:
    return [xyxy[0] / width, xyxy[1] / height, xyxy[2] / width, xyxy[3] / height]


def region_for_center(x: float, y: float) -> str:
    matches = [
        (region, (box[2] - box[0]) * (box[3] - box[1]))
        for region, _, box in target_regions()
        if box[0] <= x <= box[2] and box[1] <= y <= box[3]
    ]
    if matches:
        return min(matches, key=lambda item: item[1])[0]
    return "人员操作区"


def box_iou(left: list[float], right: list[float]) -> float:
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    return intersection / max(left_area + right_area - intersection, 1e-9)


def nms_annotations(items: list[dict], threshold: float = 0.45) -> list[dict]:
    kept: list[dict] = []
    for item in sorted(items, key=lambda value: float(value.get("confidence") or 0), reverse=True):
        if any(item["label"] == other["label"] and box_iou(item["xyxy"], other["xyxy"]) >= threshold for other in kept):
            continue
        kept.append(item)
    specific = [item for item in kept if item["label"] in COMPONENT_CLASSES and item["label"] != "电子元器件/物料候选"]
    return [
        item for item in kept
        if item["label"] != "电子元器件/物料候选"
        or not any(box_iou(item["xyxy"], other["xyxy"]) >= 0.35 for other in specific)
    ]


def component_tiles(frame: np.ndarray, target: bool) -> list[tuple[np.ndarray, int, int]]:
    height, width = frame.shape[:2]
    if target:
        boxes = [box for _, class_name, box in target_regions() if class_name == "PCB板" or "物料区" in class_name]
    else:
        boxes = [
            [0.00, 0.00, 0.62, 0.72], [0.38, 0.00, 1.00, 0.72],
            [0.00, 0.28, 0.62, 1.00], [0.38, 0.28, 1.00, 1.00],
        ]
    tiles = []
    for box in boxes:
        x1, y1 = max(0, int(box[0] * width)), max(0, int(box[1] * height))
        x2, y2 = min(width, int(box[2] * width)), min(height, int(box[3] * height))
        if x2 - x1 >= 32 and y2 - y1 >= 32:
            tiles.append((frame[y1:y2, x1:x2], x1, y1))
    return tiles


def detection_annotation(detection: object, prompt: str, width: int, height: int, offset_x: int = 0, offset_y: int = 0, source: str = "YOLOE-26S开放词汇预标注") -> dict | None:
    confidence = float(detection.conf.item())
    if confidence < MIN_CONFIDENCE[prompt]:
        return None
    pixels = [float(value) for value in detection.xyxy[0].tolist()]
    pixels = [pixels[0] + offset_x, pixels[1] + offset_y, pixels[2] + offset_x, pixels[3] + offset_y]
    pixels = [max(0.0, min(float(width if index % 2 == 0 else height), value)) for index, value in enumerate(pixels)]
    if pixels[2] - pixels[0] < 3 or pixels[3] - pixels[1] < 3:
        return None
    class_name = PROMPT_TO_CLASS[prompt]
    box = normalized_box(pixels, width, height)
    if class_name in COMPONENT_CLASSES and ((box[2] - box[0]) > 0.30 or (box[3] - box[1]) > 0.30):
        return None
    return {
        "label": class_name,
        "region": region_for_center((box[0] + box[2]) / 2, (box[1] + box[3]) / 2),
        "confidence": round(confidence, 4),
        "xyxy": [round(value, 1) for value in pixels],
        "source": source,
        "review_status": "pending",
    }


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
    results = model.predict([item["frame"] for item in pending], imgsz=imgsz, conf=0.02, iou=0.45, device=0, verbose=False)
    tile_jobs = []
    tile_annotations: list[list[dict]] = [[] for _ in pending]
    for item_index, item in enumerate(pending):
        for tile, offset_x, offset_y in component_tiles(item["frame"], item["target"]):
            tile_jobs.append({"item_index": item_index, "tile": tile, "offset_x": offset_x, "offset_y": offset_y})
    # Chunked cross-frame batching removes thousands of tiny sequential GPU calls
    # while bounding peak memory for high-resolution ROI crops.
    for offset in range(0, len(tile_jobs), 16):
        chunk = tile_jobs[offset:offset + 16]
        chunk_results = model.predict([job["tile"] for job in chunk], imgsz=imgsz, conf=0.02, iou=0.45, device=0, verbose=False)
        for job, tile_result in zip(chunk, chunk_results):
            item = pending[job["item_index"]]
            height, width = item["frame"].shape[:2]
            for detection in tile_result.boxes or []:
                prompt = PROMPTS[int(detection.cls.item())]
                if PROMPT_TO_CLASS[prompt] not in COMPONENT_CLASSES:
                    continue
                annotation = detection_annotation(
                    detection, prompt, width, height, job["offset_x"], job["offset_y"],
                    "YOLOE-26S ROI切片放大小目标预标注",
                )
                if annotation is not None:
                    if not item["target"]:
                        annotation["region"] = "独立PCB验证工位"
                    tile_annotations[job["item_index"]].append(annotation)

    for item_index, (item, result) in enumerate(zip(pending, results)):
        frame = item["frame"]
        height, width = frame.shape[:2]
        fixed_annotations: list[dict] = []
        dynamic_annotations: list[dict] = []
        if item["target"]:
            for region, class_name, box in target_regions():
                fixed_annotations.append({
                    "label": class_name,
                    "region": region,
                    "confidence": None,
                    "xyxy": [round(box[0] * width, 1), round(box[1] * height, 1), round(box[2] * width, 1), round(box[3] * height, 1)],
                    "source": "固定工位业务区域候选",
                    "review_status": "pending",
                })
        boxes = result.boxes or []
        for detection in boxes:
            prompt = PROMPTS[int(detection.cls.item())]
            annotation = detection_annotation(detection, prompt, width, height)
            if annotation is None:
                continue
            if not item["target"]:
                annotation["region"] = "独立PCB验证工位"
            dynamic_annotations.append(annotation)
            if prompt == "human hand" and item["target"]:
                box = normalized_box(annotation["xyxy"], width, height)
                action_name, action_region = action_for_hand(box, item["motion"])
                dynamic_annotations.append({
                    "label": action_name,
                    "region": action_region,
                    "confidence": round(min(0.75, float(annotation["confidence"]) + 0.20), 4),
                    "xyxy": annotation["xyxy"],
                    "source": "手部位置与运动强度动作候选",
                    "review_status": "pending",
                })

        dynamic_annotations.extend(tile_annotations[item_index])

        annotations = fixed_annotations + nms_annotations(dynamic_annotations)
        label_lines: list[str] = []
        for annotation in annotations:
            box = normalized_box(annotation["xyxy"], width, height)
            label_lines.append(yolo_line(annotation["label"], box))
            counts[annotation["label"]] += 1
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
        "algorithm": "YOLOE-26S开放词汇 + PCB/物料ROI切片增强 + 运动关键帧 + 人工复核",
        "parts": [{"id": f"R{index:02d}", "label": region, "roi": box} for index, (region, _, box) in enumerate(target_regions(), 1)],
        "steps": build_candidate_steps(summary["duration_s"]),
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


def build_candidate_steps(duration_s: float) -> list[dict]:
    boundaries = [round(duration_s * ratio, 3) for ratio in (0.0, 0.25, 0.50, 0.75, 1.0)]
    pcb_roi = [0.0, 0.10, 0.985, 0.80]
    specs = [
        ("S01", "取料候选", [0.0, 0.64, 1.0, 1.0]),
        ("S02", "元器件对位候选", [0.0, 0.10, 1.0, 1.0]),
        ("S03", "插装/按压候选", pcb_roi),
        ("S04", "完成复检候选", pcb_roi),
    ]
    return [
        {
            "id": step_id,
            "label": label,
            "start_s": boundaries[index],
            "end_s": boundaries[index + 1],
            "duration_s": round(boundaries[index + 1] - boundaries[index], 3),
            "roi": roi,
            "boundary_status": "automatic_candidate_pending_human_review",
        }
        for index, (step_id, label, roi) in enumerate(specs)
    ]


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
    if not args.skip_catalog:
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
