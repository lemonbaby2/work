from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import time
from bisect import bisect_left
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLOE


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parents[1]
DEFAULT_SOURCES = [
    PROJECT_ROOT / "视频数据/宁波SoP项目/8月19号（工位4漏焊）",
    PROJECT_ROOT / "视频数据/宁波SoP项目/8月20号",
]
DATASET_ROOT = ROOT / "datasets/宁波SOP_0819_0820_YOLOE关键帧预标注_待人工复核"
MEDIA_ROOT = ROOT / "web/media/local/ningbo_sop"
DATA_ROOT = ROOT / "web/data"
SNAPSHOT_ROOT = ROOT / "web/snapshots"
MODEL_PATH = ROOT / "models/yoloe-26s-seg.pt"

FULL_PROMPTS = [
    ("printed circuit board", "PCB板"),
    ("human hand", "操作人员手部"),
    ("factory worker", "操作人员"),
    ("soldering iron", "电烙铁"),
    ("precision tweezers", "镊子"),
    ("cleaning brush", "刷子"),
    ("assembly fixture", "PCB夹具"),
    ("plastic parts bin", "物料盒"),
    ("electrical connector", "连接器"),
    ("solder wire spool", "焊锡丝/焊料"),
    ("electronic product housing", "产品/外壳"),
    ("electronic component", "电子物料"),
]
SMALL_PROMPTS = [
    ("screw", "螺钉候选"),
    ("electrolytic capacitor", "电容候选"),
    ("inductor coil", "电感候选"),
    ("resistor electronic component", "电阻候选"),
    ("electrical connector", "连接器候选"),
]
ACTION_LABELS = ["取放物料候选", "PCB持取/翻面候选", "镊子夹取/对位候选", "焊接操作候选", "刷涂/清洁候选", "完成复检候选"]
ROI_LABELS = ["工人工位ROI", "PCB/夹具ROI", "工具ROI", "物料ROI", "成品/外壳ROI"]
CLASS_NAMES = [label for _, label in FULL_PROMPTS + SMALL_PROMPTS] + ROI_LABELS + ACTION_LABELS
CLASS_IDS = {name: index for index, name in enumerate(CLASS_NAMES)}

MIN_CONFIDENCE = {
    "printed circuit board": 0.045,
    "human hand": 0.06,
    "factory worker": 0.07,
    "soldering iron": 0.15,
    "precision tweezers": 0.04,
    "cleaning brush": 0.05,
    "assembly fixture": 0.08,
    "plastic parts bin": 0.08,
    "electrical connector": 0.035,
    "solder wire spool": 0.08,
    "electronic product housing": 0.08,
    "electronic component": 0.06,
    "screw": 0.035,
    "electrolytic capacitor": 0.025,
    "inductor coil": 0.025,
    "resistor electronic component": 0.025,
}

PROFILES = {
    "near_soldering": [
        ("工人工位区", "工人工位ROI", [0.03, 0.00, 0.97, 1.00]),
        ("PCB/夹具操作区", "PCB/夹具ROI", [0.22, 0.18, 0.78, 0.92]),
        ("烙铁与手工具区", "工具ROI", [0.02, 0.18, 0.50, 1.00]),
        ("焊料与电子物料区", "物料ROI", [0.00, 0.42, 0.43, 1.00]),
        ("成品与料盒区", "成品/外壳ROI", [0.64, 0.12, 1.00, 1.00]),
    ],
    "overhead_assembly": [
        ("工人工位区", "工人工位ROI", [0.00, 0.08, 1.00, 1.00]),
        ("PCB/夹具操作区", "PCB/夹具ROI", [0.14, 0.25, 0.70, 0.97]),
        ("工具与操作区", "工具ROI", [0.52, 0.18, 0.98, 0.92]),
        ("料盒与电子物料区", "物料ROI", [0.15, 0.00, 0.70, 0.43]),
        ("产品外壳区", "成品/外壳ROI", [0.00, 0.22, 0.42, 1.00]),
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare Ningbo station videos with open-vocabulary keyframe candidates")
    parser.add_argument("--source", action="append", type=Path, help="Video directory; repeatable")
    parser.add_argument("--model", type=Path, default=MODEL_PATH)
    parser.add_argument("--sample-seconds", type=float, default=1.0)
    parser.add_argument("--small-seconds", type=float, default=5.0)
    parser.add_argument("--batch", type=int, default=12)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--max-videos", type=int, default=0)
    parser.add_argument("--max-keyframes", type=int, default=0)
    parser.add_argument("--skip-proxy", action="store_true")
    parser.add_argument("--skip-catalog", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def profile_for(video: Path) -> str:
    return "near_soldering" if "工位4漏焊" in str(video.parent) else "overhead_assembly"


def video_id(video: Path) -> str:
    if "工位4漏焊" in str(video.parent):
        return f"video_nb0819_{video.stem.zfill(2)}"
    return f"video_nb0820_{video.stem[:10].lower()}"


def iou(left: list[float], right: list[float]) -> float:
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_left = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    area_right = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    return intersection / max(area_left + area_right - intersection, 1.0)


def nms(items: list[dict], threshold: float = 0.45) -> list[dict]:
    selected: list[dict] = []
    counts: Counter[str] = Counter()
    limits = {"PCB板": 4, "操作人员手部": 4, "操作人员": 3, "电子物料": 6}
    for item in sorted(items, key=lambda value: float(value.get("confidence") or 0), reverse=True):
        if counts[item["label"]] >= limits.get(item["label"], 3):
            continue
        if any(item["label"] == kept["label"] and iou(item["xyxy"], kept["xyxy"]) >= threshold for kept in selected):
            continue
        selected.append(item)
        counts[item["label"]] += 1
    return selected


def region_for(box: list[float], profile_name: str, width: int, height: int) -> str:
    center_x = (box[0] + box[2]) / (2 * width)
    center_y = (box[1] + box[3]) / (2 * height)
    matches = []
    for name, _, roi in PROFILES[profile_name]:
        if roi[0] <= center_x <= roi[2] and roi[1] <= center_y <= roi[3]:
            matches.append((name, (roi[2] - roi[0]) * (roi[3] - roi[1])))
    return min(matches, key=lambda item: item[1])[0] if matches else "工人工位区"


def detections_from_result(result, prompts: list[tuple[str, str]], width: int, height: int, profile_name: str, source: str) -> list[dict]:
    output = []
    for box in result.boxes or []:
        prompt, label = prompts[int(box.cls.item())]
        confidence = float(box.conf.item())
        if confidence < MIN_CONFIDENCE.get(prompt, 0.05):
            continue
        xyxy = [float(value) for value in box.xyxy[0].tolist()]
        box_width, box_height = xyxy[2] - xyxy[0], xyxy[3] - xyxy[1]
        if box_width < 5 or box_height < 5:
            continue
        area_ratio = box_width * box_height / max(width * height, 1)
        if label.endswith("候选") and area_ratio > 0.16:
            continue
        if label in {"操作人员手部", "电烙铁", "镊子", "刷子", "连接器"} and area_ratio > 0.45:
            continue
        output.append({
            "label": label,
            "region": region_for(xyxy, profile_name, width, height),
            "confidence": round(confidence, 4),
            "xyxy": [round(value, 1) for value in xyxy],
            "source": source,
            "review_status": "pending",
        })
    return nms(output)


def fixed_regions(profile_name: str, width: int, height: int) -> list[dict]:
    return [{
        "label": label,
        "region": name,
        "confidence": None,
        "xyxy": [round(roi[0] * width, 1), round(roi[1] * height, 1), round(roi[2] * width, 1), round(roi[3] * height, 1)],
        "source": "固定机位工位ROI候选",
        "review_status": "pending",
    } for name, label, roi in PROFILES[profile_name]]


def union_box(left: list[float], right: list[float]) -> list[float]:
    return [min(left[0], right[0]), min(left[1], right[1]), max(left[2], right[2]), max(left[3], right[3])]


def center_distance(left: list[float], right: list[float], width: int, height: int) -> float:
    lx, ly = (left[0] + left[2]) / 2, (left[1] + left[3]) / 2
    rx, ry = (right[0] + right[2]) / 2, (right[1] + right[3]) / 2
    return math.hypot((lx - rx) / width, (ly - ry) / height)


def action_candidates(items: list[dict], width: int, height: int, profile_name: str) -> list[dict]:
    hands = [item for item in items if item["label"] == "操作人员手部"]
    targets = [item for item in items if item["label"] in {"电烙铁", "镊子", "刷子", "PCB板", "电子物料", "连接器"}]
    label_map = {"电烙铁": "焊接操作候选", "镊子": "镊子夹取/对位候选", "刷子": "刷涂/清洁候选", "PCB板": "PCB持取/翻面候选"}
    output = []
    for hand in hands:
        nearby = sorted(targets, key=lambda target: center_distance(hand["xyxy"], target["xyxy"], width, height))
        target = nearby[0] if nearby and center_distance(hand["xyxy"], nearby[0]["xyxy"], width, height) <= 0.24 else None
        label = label_map.get(target["label"], "取放物料候选") if target else "取放物料候选"
        box = union_box(hand["xyxy"], target["xyxy"]) if target else hand["xyxy"]
        output.append({
            "label": label,
            "region": region_for(box, profile_name, width, height),
            "confidence": hand["confidence"],
            "xyxy": box,
            "source": "手部与工具/工件空间关系动作候选",
            "review_status": "pending",
        })
    return nms(output, 0.5)


def crop_specs(profile_name: str, width: int, height: int) -> list[tuple[int, int, int, int]]:
    rois = [roi for _, label, roi in PROFILES[profile_name] if label in {"PCB/夹具ROI", "物料ROI"}]
    return [(max(0, int(x1 * width)), max(0, int(y1 * height)), min(width, int(x2 * width)), min(height, int(y2 * height))) for x1, y1, x2, y2 in rois]


def yolo_line(label: str, xyxy: list[float], width: int, height: int) -> str:
    x1, y1, x2, y2 = xyxy
    return f"{CLASS_IDS[label]} {(x1 + x2) / (2 * width):.6f} {(y1 + y2) / (2 * height):.6f} {(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f}"


def clarity_score(frame: np.ndarray) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def proxy_video(source: Path, destination: Path, sharpen: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    filters = "scale='min(1280,iw)':-2:flags=lanczos"
    if sharpen:
        filters += ",unsharp=5:5:0.65:5:5:0"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(source), "-vf", filters,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-an", str(destination),
    ], check=True)


def steps_for(duration: float, profile_name: str) -> list[dict]:
    labels = (["取板/物料准备候选", "PCB夹持与元器件处理候选", "焊接/返修操作候选", "完成复检候选"]
              if profile_name == "near_soldering" else
              ["取料与工位准备候选", "PCB/夹具对位候选", "装配/工具操作候选", "完成复检候选"])
    roi = next(roi for _, label, roi in PROFILES[profile_name] if label == "PCB/夹具ROI")
    boundaries = [duration * index / 4 for index in range(5)]
    return [{
        "id": f"S{index + 1:02d}", "label": label,
        "start_s": round(boundaries[index], 3), "end_s": round(boundaries[index + 1], 3),
        "duration_s": round(boundaries[index + 1] - boundaries[index], 3), "roi": roi,
        "source": "四等分候选时间段，待工艺人员按真实工序修订",
    } for index, label in enumerate(labels)]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def nearest_candidates(records: list[dict], frame: int, max_gap: int) -> tuple[int, list[dict]] | None:
    available = [(int(row["frame"]), row["candidates"]) for row in records if row["candidates"]]
    if not available:
        return None
    positions = [item[0] for item in available]
    index = bisect_left(positions, frame)
    choices = available[max(0, index - 1):min(len(available), index + 1)]
    nearest = min(choices, key=lambda item: abs(item[0] - frame))
    return nearest if abs(nearest[0] - frame) <= max_gap else None


def process_video(model: YOLOE, embeddings: dict[str, object], video: Path, args: argparse.Namespace) -> dict:
    identifier = video_id(video)
    profile_name = profile_for(video)
    summary_path = DATA_ROOT / f"{identifier}_summary.json"
    if summary_path.exists() and not args.overwrite:
        return json.loads(summary_path.read_text(encoding="utf-8"))

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"无法打开视频: {video}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 15)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 1)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720)
    duration = frame_count / fps
    sample_count = math.ceil(duration / args.sample_seconds)
    if args.max_keyframes:
        sample_count = min(sample_count, args.max_keyframes)
    small_stride = max(1, round(args.small_seconds / args.sample_seconds))
    image_dir = DATASET_ROOT / identifier / "images"
    label_dir = DATASET_ROOT / identifier / "labels"
    image_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)
    pending: list[dict] = []
    records: list[dict] = []
    candidate_records: list[dict] = []
    counts: Counter[str] = Counter()
    clarity_values = []
    started = time.time()

    def flush() -> None:
        if not pending:
            return
        model.set_classes([item[0] for item in FULL_PROMPTS], embeddings["full"])
        results = model.predict([item["frame"] for item in pending], imgsz=args.imgsz, conf=0.02, iou=0.45, max_det=60, device=0, verbose=False)
        small_jobs = []
        for pending_index, item in enumerate(pending):
            if item["sample_index"] % small_stride:
                continue
            for x1, y1, x2, y2 in crop_specs(profile_name, width, height):
                crop = item["frame"][y1:y2, x1:x2]
                if crop.size:
                    small_jobs.append((pending_index, crop, x1, y1))
        small_by_index: dict[int, list[dict]] = {index: [] for index in range(len(pending))}
        if small_jobs:
            model.set_classes([item[0] for item in SMALL_PROMPTS], embeddings["small"])
            small_results = model.predict([job[1] for job in small_jobs], imgsz=args.imgsz, conf=0.02, iou=0.45, max_det=40, device=0, verbose=False)
            for (pending_index, crop, offset_x, offset_y), result in zip(small_jobs, small_results):
                items = detections_from_result(result, SMALL_PROMPTS, crop.shape[1], crop.shape[0], profile_name, "YOLOE ROI切片小目标候选")
                for item in items:
                    item["xyxy"] = [round(value + (offset_x if axis % 2 == 0 else offset_y), 1) for axis, value in enumerate(item["xyxy"])]
                    item["region"] = region_for(item["xyxy"], profile_name, width, height)
                small_by_index[pending_index].extend(items)
        for pending_index, (item, result) in enumerate(zip(pending, results)):
            detections = detections_from_result(result, FULL_PROMPTS, width, height, profile_name, "YOLOE开放词汇关键帧预标注")
            detections = fixed_regions(profile_name, width, height) + detections + action_candidates(detections, width, height, profile_name)
            candidates = nms(small_by_index[pending_index], 0.4)
            for detection in detections + candidates:
                counts[detection["label"]] += 1
            image_path = image_dir / f"{identifier}_{item['frame_index']:08d}.jpg"
            label_path = label_dir / f"{identifier}_{item['frame_index']:08d}.txt"
            cv2.imwrite(str(image_path), item["frame"], [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            label_path.write_text("\n".join(yolo_line(row["label"], row["xyxy"], width, height) for row in detections + candidates) + "\n", encoding="utf-8")
            records.append({
                "frame": item["frame_index"], "time_s": item["time_s"], "detections": detections,
                "parts": [name for name, _, _ in PROFILES[profile_name]], "review_status": "pending",
            })
            candidate_records.append({"frame": item["frame_index"], "time_s": item["time_s"], "candidates": candidates})
        pending.clear()

    for sample_index in range(sample_count):
        second = min(duration, sample_index * args.sample_seconds)
        capture.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        actual_frame = int(round(second * fps))
        if len(clarity_values) < 24:
            clarity_values.append(clarity_score(frame))
        pending.append({"sample_index": sample_index, "frame_index": actual_frame, "time_s": round(second, 3), "frame": frame})
        if len(pending) >= args.batch:
            flush()
    flush()
    capture.release()

    max_gap = round(args.small_seconds * fps)
    for row in candidate_records:
        if row["candidates"]:
            continue
        propagated = nearest_candidates(candidate_records, int(row["frame"]), max_gap)
        if propagated is None:
            continue
        source_frame, candidates = propagated
        row["candidates"] = [{**item, "confidence": round(float(item["confidence"]) * 0.82, 4), "source": "固定机位小目标关键帧邻近传播", "propagated_from_frame": source_frame} for item in candidates]

    write_jsonl(DATA_ROOT / f"{identifier}_frame_annotations.jsonl", records)
    write_jsonl(DATA_ROOT / f"{identifier}_fine_object_candidates.jsonl", candidate_records)
    clarity = round(float(np.median(clarity_values)), 2) if clarity_values else 0.0
    sharpened = clarity < 110.0
    proxy_name = f"{identifier}_720p.mp4"
    proxy_path = MEDIA_ROOT / proxy_name
    if not args.skip_proxy:
        proxy_video(video, proxy_path, sharpened)

    snapshot_dir = SNAPSHOT_ROOT / identifier
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshots = []
    for step in steps_for(duration, profile_name):
        target_frame = round(((step["start_s"] + step["end_s"]) / 2) * fps)
        nearest = min(records, key=lambda row: abs(int(row["frame"]) - target_frame)) if records else None
        if nearest:
            source_image = image_dir / f"{identifier}_{int(nearest['frame']):08d}.jpg"
            target_image = snapshot_dir / f"{step['id']}_{int(nearest['time_s']):04d}秒.jpg"
            shutil.copy2(source_image, target_image)
            snapshots.append(f"snapshots/{identifier}/{target_image.name}")

    summary = {
        "id": identifier,
        "display_name": f"{'8月19日漏焊返修' if profile_name == 'near_soldering' else '8月20日俯视装配'}｜{video.name}",
        "source": str(video), "video": f"media/local/ningbo_sop/{proxy_name}", "source_video": f"media/local/ningbo_sop/{proxy_name}",
        "frames": frame_count, "fps": round(fps, 4), "resolution": f"{width}x{height}", "duration_s": round(duration, 3),
        "algorithm": "YOLOE开放词汇关键帧 + ROI切片小目标 + 固定机位邻近传播 + 动作关系候选",
        "parts": [{"label": name, "class": label, "roi": roi} for name, label, roi in PROFILES[profile_name]],
        "steps": steps_for(duration, profile_name), "snapshots": snapshots,
        "annotation_source": f"data/{identifier}_frame_annotations.jsonl",
        "candidate_source": f"data/{identifier}_fine_object_candidates.jsonl",
        "keyframes": len(records), "small_object_interval_s": args.small_seconds,
        "clarity_score": clarity, "sharpened_proxy": sharpened,
        "candidate_counts": dict(counts), "production_release": "HOLD",
        "truth_policy": "自动框、ROI、动作和传播结果均为待人工复核候选，不是生产真值。",
        "processing_seconds": round(time.time() - started, 2),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"id": identifier, "keyframes": len(records), "clarity": clarity, "counts": dict(counts), "seconds": summary["processing_seconds"]}, ensure_ascii=False))
    return summary


def update_catalog(summaries: list[dict]) -> None:
    path = DATA_ROOT / "videos.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    identifiers = {item["id"] for item in summaries}
    catalog["videos"] = [item for item in catalog.get("videos", []) if item.get("id") not in identifiers] + summaries
    catalog["totals"] = {
        "videos": len(catalog["videos"]),
        "frames": sum(int(item.get("frames", 0)) for item in catalog["videos"]),
        "duration_s": round(sum(float(item.get("duration_s", 0)) for item in catalog["videos"]), 2),
        "steps": sum(len(item.get("steps", [])) for item in catalog["videos"]),
    }
    catalog["ningbo_station_extension"] = {
        "videos": len(summaries), "keyframes": sum(int(item.get("keyframes", 0)) for item in summaries),
        "method": "YOLOE开放词汇关键帧 + ROI切片小目标 + 固定机位邻近传播",
        "classes": CLASS_NAMES, "truth_policy": "全部为待人工复核候选",
    }
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")


def write_dataset_yaml(summaries: list[dict]) -> None:
    identifiers = [str(item["id"]) for item in summaries]
    # Split by complete videos, never by neighboring frames from the same source.
    test_ids = identifiers[::8]
    val_ids = identifiers[4::8]
    held_out = set(test_ids + val_ids)
    train_ids = [identifier for identifier in identifiers if identifier not in held_out]

    def section(name: str, values: list[str]) -> list[str]:
        return [f"{name}:"] + [f"  - {identifier}/images" for identifier in values]

    lines = ["path: ."]
    lines.extend(section("train", train_ids))
    lines.extend(section("val", val_ids))
    lines.extend(section("test", test_ids))
    lines.extend([
        "review_required: true",
        "truth_status: pending_human_review",
        "split_policy: complete_video_no_adjacent_frame_leakage",
        "names:",
    ])
    lines.extend(f"  {index}: {name}" for index, name in enumerate(CLASS_NAMES))
    (DATASET_ROOT / "data.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    sources = args.source or DEFAULT_SOURCES
    videos = sorted(video for source in sources for video in source.glob("*.mp4"))
    if args.max_videos:
        videos = videos[:args.max_videos]
    if not videos:
        raise RuntimeError("没有找到待处理视频")
    if not args.model.is_file():
        raise RuntimeError(f"YOLOE模型不存在: {args.model}")
    DATASET_ROOT.mkdir(parents=True, exist_ok=True)
    previous = Path.cwd()
    os.chdir(args.model.parent)
    try:
        model = YOLOE(str(args.model))
        embeddings = {
            "full": model.get_text_pe([item[0] for item in FULL_PROMPTS]),
            "small": model.get_text_pe([item[0] for item in SMALL_PROMPTS]),
        }
        summaries = [process_video(model, embeddings, video, args) for video in videos]
    finally:
        os.chdir(previous)
    classes = {"names": {str(index): name for index, name in enumerate(CLASS_NAMES)}, "truth_status": "pending_human_review"}
    (DATASET_ROOT / "classes.json").write_text(json.dumps(classes, ensure_ascii=False, indent=2), encoding="utf-8")
    write_dataset_yaml(summaries)
    (DATASET_ROOT / "dataset_stats.json").write_text(json.dumps({"videos": summaries, "truth_policy": "pending_human_review"}, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.skip_catalog:
        update_catalog(summaries)
    print(json.dumps({"videos": len(summaries), "keyframes": sum(item["keyframes"] for item in summaries)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
