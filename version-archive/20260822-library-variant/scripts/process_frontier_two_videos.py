from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from ultralytics import YOLO

from render_right_sop_panel import draw_panel


ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models"
MODEL_PATH = MODEL_DIR / "yoloe-26s-seg.pt"
PROFILE_PATH = ROOT / "config/frontier_two_video_profiles.json"
MEDIA_DIR = ROOT / "web/media"
DATA_DIR = ROOT / "web/data"
SNAPSHOT_ROOT = ROOT / "web/snapshots"
DATASET = ROOT / "datasets/新增两视频_YOLOE26_SAHI细粒度预标注_待人工复核"
QA_DIR = ROOT / "qa/frontier_two_videos"

FULL_CLASSES = [
    "car dashboard assembly",
    "dashboard frame",
    "plastic trim panel",
    "wire harness",
    "electrical connector",
    "power screwdriver",
    "hand",
]
SMALL_CLASSES = [
    "screw head",
    "bolt head",
    "plastic clip",
    "electrical connector",
    "wire harness connector",
    "fastener hole",
]
CLASS_ZH = {
    "car dashboard assembly": "仪表板总成",
    "dashboard frame": "仪表板骨架",
    "plastic trim panel": "饰板总成",
    "wire harness": "线束",
    "electrical connector": "电气接插件",
    "power screwdriver": "电动紧固工具",
    "hand": "操作人员手部",
    "screw head": "螺钉头候选",
    "bolt head": "螺栓头候选",
    "plastic clip": "塑料卡扣候选",
    "wire harness connector": "线束插头候选",
    "fastener hole": "紧固孔候选",
}
DATASET_CLASSES = [
    "仪表板总成",
    "仪表板骨架",
    "饰板总成",
    "线束",
    "电气接插件",
    "电动紧固工具",
    "操作人员手部",
    "螺钉头候选",
    "螺栓头候选",
    "塑料卡扣候选",
    "线束插头候选",
    "紧固孔候选",
]
CLASS_ID = {name: index for index, name in enumerate(DATASET_CLASSES)}
LARGE_CLASSES = {"car dashboard assembly", "dashboard frame", "plastic trim panel"}
SMALL_SET = set(SMALL_CLASSES)
COLORS = {
    "car dashboard assembly": (245, 182, 55),
    "dashboard frame": (66, 196, 233),
    "plastic trim panel": (181, 121, 255),
    "wire harness": (92, 220, 153),
    "electrical connector": (255, 157, 72),
    "power screwdriver": (255, 100, 90),
    "hand": (224, 105, 190),
    "screw head": (46, 73, 255),
    "bolt head": (36, 132, 255),
    "plastic clip": (225, 86, 227),
    "wire harness connector": (255, 184, 77),
    "fastener hole": (74, 214, 255),
}


def font(size: int, bold: bool = False):
    candidates = [
        Path(r"C:\Windows\Fonts\msyhbd.ttc") if bold else Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


FONT_13 = font(13)
FONT_16 = font(16, True)
FONT_22 = font(22, True)


def load_prompt_model(classes: list[str]) -> YOLO:
    previous = Path.cwd()
    os.chdir(MODEL_DIR)
    try:
        model = YOLO(str(MODEL_PATH))
        model.set_classes(classes)
    finally:
        os.chdir(previous)
    return model


def iou(a: list[float], b: list[float]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    return intersection / max(area_a + area_b - intersection, 1.0)


def nms(items: list[dict], threshold: float = 0.35, class_agnostic: bool = False) -> list[dict]:
    selected: list[dict] = []
    for item in sorted(items, key=lambda row: row["confidence"], reverse=True):
        if any(
            iou(item["xyxy"], kept["xyxy"]) >= threshold
            and (class_agnostic or item["class"] == kept["class"])
            for kept in selected
        ):
            continue
        selected.append(item)
    return selected


def detect_full(model: YOLO, frame: np.ndarray) -> list[dict]:
    height, width = frame.shape[:2]
    result = model.predict(frame, imgsz=960, conf=0.03, iou=0.5, device=0, verbose=False)[0]
    proposals: list[dict] = []
    if result.boxes is None:
        return proposals
    polygons = result.masks.xy if result.masks is not None else []
    for index, box in enumerate(result.boxes):
        cls = FULL_CLASSES[int(box.cls.item())]
        confidence = float(box.conf.item())
        large_thresholds = {"car dashboard assembly": 0.15, "dashboard frame": 0.18, "plastic trim panel": 0.30}
        minimum = large_thresholds.get(cls, 0.08)
        if confidence < minimum:
            continue
        xyxy = [float(value) for value in box.xyxy[0].tolist()]
        polygon = None
        if index < len(polygons) and len(polygons[index]) >= 3:
            polygon_array = np.asarray(polygons[index], dtype=np.float32)
            px, py, pw, ph = cv2.boundingRect(polygon_array.astype(np.int32))
            if pw >= 4 and ph >= 4:
                xyxy = [float(px), float(py), float(px + pw), float(py + ph)]
                polygon = polygon_array.round(1).tolist()
        box_width, box_height = xyxy[2] - xyxy[0], xyxy[3] - xyxy[1]
        area_ratio = box_width * box_height / max(width * height, 1)
        if cls in LARGE_CLASSES and not 0.06 <= area_ratio <= 0.96:
            continue
        if cls not in LARGE_CLASSES and (box_width < 8 or box_height < 8):
            continue
        proposal = {
                "class": cls,
                "label": CLASS_ZH[cls],
                "confidence": round(confidence, 4),
                "xyxy": [round(value, 1) for value in xyxy],
                "polygon": polygon,
                "source": "YOLOE-26S开放词汇实例分割",
                "review_status": "pending",
            }
        if plausible_detection(frame, proposal):
            proposals.append(proposal)
    proposals = nms(proposals, 0.45)
    output: list[dict] = []
    per_class: Counter[str] = Counter()
    limits = {name: 1 for name in LARGE_CLASSES}
    limits.update({"wire harness": 2, "electrical connector": 5, "power screwdriver": 2, "hand": 4})
    for item in proposals:
        if per_class[item["class"]] >= limits.get(item["class"], 3):
            continue
        if item["class"] in LARGE_CLASSES and any(
            kept["class"] in LARGE_CLASSES and iou(item["xyxy"], kept["xyxy"]) > 0.78 for kept in output
        ):
            continue
        output.append(item)
        per_class[item["class"]] += 1
    return output


def plausible_detection(frame: np.ndarray, item: dict) -> bool:
    """Use conservative industrial priors to reject obvious arm/skin false positives for large rigid parts."""
    if item["class"] not in LARGE_CLASSES:
        return True
    thresholds = {"car dashboard assembly": 0.15, "dashboard frame": 0.18, "plastic trim panel": 0.30}
    if float(item["confidence"]) < thresholds[item["class"]]:
        return False
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [int(value) for value in item["xyxy"]]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return False
    ycrcb = cv2.cvtColor(crop, cv2.COLOR_BGR2YCrCb)
    skin = cv2.inRange(ycrcb, np.array([35, 135, 85], np.uint8), np.array([255, 180, 135], np.uint8))
    skin_ratio = float(np.count_nonzero(skin)) / max(skin.size, 1)
    return skin_ratio < 0.38


def smooth_large(previous: list[dict], current: list[dict], alpha: float = 0.62) -> list[dict]:
    previous_by_class = {item["class"]: item for item in previous if item["class"] in LARGE_CLASSES}
    for item in current:
        prior = previous_by_class.get(item["class"])
        if prior and iou(item["xyxy"], prior["xyxy"]) > 0.16:
            item["xyxy"] = [
                round(alpha * new + (1.0 - alpha) * old, 1)
                for new, old in zip(item["xyxy"], prior["xyxy"])
            ]
    return current


def tile_specs(width: int, height: int) -> list[tuple[int, int, int, int]]:
    return [
        (0, 0, width * 2 // 3, height * 3 // 4),
        (width // 3, 0, width, height * 3 // 4),
        (0, height // 4, width * 2 // 3, height),
        (width // 3, height // 4, width, height),
    ]


def detect_small(model: YOLO, frame: np.ndarray) -> list[dict]:
    height, width = frame.shape[:2]
    specs = tile_specs(width, height)
    crops = [frame[y1:y2, x1:x2] for x1, y1, x2, y2 in specs]
    results = model.predict(crops, imgsz=960, conf=0.02, iou=0.5, device=0, verbose=False)
    proposals: list[dict] = []
    for (offset_x, offset_y, _, _), result in zip(specs, results):
        if result.boxes is None:
            continue
        for box in result.boxes:
            cls = SMALL_CLASSES[int(box.cls.item())]
            confidence = float(box.conf.item())
            x1, y1, x2, y2 = [float(value) for value in box.xyxy[0].tolist()]
            x1 += offset_x
            x2 += offset_x
            y1 += offset_y
            y2 += offset_y
            box_width, box_height = x2 - x1, y2 - y1
            aspect = box_width / max(box_height, 1.0)
            if confidence < 0.04 or not (4 <= box_width <= 180 and 4 <= box_height <= 180 and 0.23 <= aspect <= 4.3):
                continue
            proposals.append(
                {
                    "class": cls,
                    "label": CLASS_ZH[cls],
                    "confidence": round(confidence, 4),
                    "xyxy": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                    "source": "YOLOE-26S四窗口重叠切片",
                    "review_status": "pending",
                }
            )
    return nms(proposals, 0.34, class_agnostic=True)[:18]


def draw_tags(frame: np.ndarray, detections: list[dict]) -> np.ndarray:
    canvas = frame.copy()
    for item in detections:
        color = COLORS[item["class"]]
        x1, y1, x2, y2 = [int(value) for value in item["xyxy"]]
        if item.get("polygon") and item["class"] in LARGE_CLASSES:
            polygon = np.asarray(item["polygon"], dtype=np.int32)
            cv2.polylines(canvas, [polygon], True, color, 2, cv2.LINE_AA)
        thickness = 3 if item["class"] in LARGE_CLASSES else 2
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)

    image = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    small_counter: Counter[str] = Counter()
    for item in detections:
        x1, y1, x2, y2 = [int(value) for value in item["xyxy"]]
        rgb = tuple(reversed(COLORS[item["class"]]))
        if item["class"] in SMALL_SET:
            small_counter[item["class"]] += 1
            label = f"{item['label']}{small_counter[item['class']]}"
        else:
            label = f"{item['label']} {item['confidence']:.0%}"
        text_y = max(2, y1 - 20)
        box = draw.textbbox((x1 + 2, text_y), label, font=FONT_13)
        draw.rounded_rectangle((box[0] - 3, box[1] - 1, box[2] + 3, box[3] + 1), radius=3, fill=(*rgb, 218))
        draw.text((x1 + 2, text_y), label, font=FONT_13, fill="white")
    return cv2.cvtColor(np.asarray(Image.alpha_composite(image, overlay).convert("RGB")), cv2.COLOR_RGB2BGR)


def fit_canvas(frame: np.ndarray, width: int = 1280, height: int = 720) -> np.ndarray:
    source_height, source_width = frame.shape[:2]
    scale = min(width / source_width, height / source_height)
    new_width, new_height = int(source_width * scale), int(source_height * scale)
    resized = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    canvas = np.full((height, width, 3), (13, 25, 34), dtype=np.uint8)
    offset_x, offset_y = (width - new_width) // 2, (height - new_height) // 2
    canvas[offset_y : offset_y + new_height, offset_x : offset_x + new_width] = resized
    return canvas


def yolo_line(class_id: int, box: list[float], width: int, height: int) -> str:
    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) / 2 / width, (y1 + y2) / 2 / height
    bw, bh = (x2 - x1) / width, (y2 - y1) / height
    values = [max(0.0, min(1.0, value)) for value in (cx, cy, bw, bh)]
    return f"{class_id} " + " ".join(f"{value:.6f}" for value in values)


def split_for(elapsed: float, duration: float) -> str:
    ratio = elapsed / max(duration, 0.1)
    return "train" if ratio < 0.70 else "val" if ratio < 0.85 else "test"


def save_sample(profile: dict, frame: np.ndarray, frame_index: int, elapsed: float, duration: float, detections: list[dict], manifest_handle) -> None:
    split = split_for(elapsed, duration)
    stem = f"{profile['id']}_{frame_index:06d}"
    image_path = DATASET / f"images/{split}/{stem}.jpg"
    label_path = DATASET / f"labels/{split}/{stem}.txt"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    label_path.parent.mkdir(parents=True, exist_ok=True)
    height, width = frame.shape[:2]
    cv2.imwrite(str(image_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 94])
    lines, annotations = [], []
    for item in detections:
        class_name = item["label"]
        if class_name not in CLASS_ID:
            continue
        lines.append(yolo_line(CLASS_ID[class_name], item["xyxy"], width, height))
        annotations.append(
            {
                "class": class_name,
                "box": item["xyxy"],
                "confidence": item["confidence"],
                "source": item["source"],
                "review_status": "pending_human_review",
            }
        )
    label_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    manifest_handle.write(
        json.dumps(
            {
                "image": str(image_path.relative_to(DATASET)).replace("\\", "/"),
                "label": str(label_path.relative_to(DATASET)).replace("\\", "/"),
                "video_id": profile["id"],
                "frame": frame_index,
                "time_s": round(elapsed, 3),
                "split": split,
                "annotations": annotations,
                "overall_status": "pending_human_review",
            },
            ensure_ascii=False,
        )
        + "\n"
    )


def process_video(profile: dict, full_model: YOLO, small_model: YOLO, manifest_handle) -> dict:
    started = time.time()
    source = Path(profile["source"])
    source_copy = MEDIA_DIR / profile["source_copy"]
    shutil.copy2(source, source_copy)
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"无法读取视频：{source}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = frame_count / fps
    temp = MEDIA_DIR / f"{profile['id']}_frontier_temp.mp4"
    final = MEDIA_DIR / profile["output"]
    writer = cv2.VideoWriter(str(temp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (1620, 720))
    if not writer.isOpened():
        raise RuntimeError(f"无法创建视频：{temp}")
    snapshot_dir = SNAPSHOT_ROOT / profile["id"]
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_frames = {
        int(((float(step["start_s"]) + float(step["end_s"])) / 2) * fps): step["id"] for step in profile["steps"]
    }
    frame_log_path = DATA_DIR / f"{profile['id']}_frame_annotations.jsonl"
    candidate_log_path = DATA_DIR / f"{profile['id']}_fine_object_candidates.jsonl"
    counts: Counter[str] = Counter()
    sampled = 0
    last_full: list[dict] = []
    last_small: list[dict] = []
    previous_full: list[dict] = []
    with frame_log_path.open("w", encoding="utf-8") as frame_log, candidate_log_path.open("w", encoding="utf-8") as candidate_log:
        for frame_index in range(frame_count):
            ok, frame = capture.read()
            if not ok:
                break
            elapsed = frame_index / fps
            if frame_index % 6 == 0:
                current_full = detect_full(full_model, frame)
                last_full = smooth_large(previous_full, current_full)
                previous_full = last_full
            if frame_index % 15 == 0:
                last_small = detect_small(small_model, frame)
                combined_sample = nms(last_full + last_small, 0.62)
                save_sample(profile, frame, frame_index, elapsed, duration, combined_sample, manifest_handle)
                sampled += 1
                candidate_log.write(
                    json.dumps(
                        {"frame": frame_index, "time_s": round(elapsed, 3), "candidates": last_small, "review_status": "pending_human_review"},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            combined = nms(last_full + last_small, 0.62)
            for item in combined:
                counts[item["label"]] += 1
            step = next((item for item in profile["steps"] if float(item["start_s"]) <= elapsed < float(item["end_s"])), profile["steps"][-1])
            completed = sum(elapsed >= float(item["end_s"]) for item in profile["steps"])
            frame_log.write(
                json.dumps(
                    {
                        "frame": frame_index,
                        "time_s": round(elapsed, 3),
                        "step_id": step["id"],
                        "step_label": step["label"],
                        "completed_steps": completed,
                        "detections": combined,
                        "decision": {
                            "visual_state": "RUNNING" if completed < len(profile["steps"]) else "PASS",
                            "release": "HOLD",
                            "reason": "缺少真实扭矩、角度与MES回执",
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            annotated = draw_tags(frame, combined)
            left = fit_canvas(annotated)
            panel = draw_panel(profile, elapsed, duration)
            output_frame = np.concatenate([left, panel], axis=1)
            writer.write(output_frame)
            if frame_index in snapshot_frames:
                cv2.imwrite(str(snapshot_dir / f"{snapshot_frames[frame_index]}_{elapsed:.1f}秒.jpg"), output_frame)
    writer.release()
    capture.release()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(temp),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-an",
            str(final),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    temp.unlink(missing_ok=True)
    return {
        "id": profile["id"],
        "display_name": profile["display_name"],
        "source": str(source),
        "video": f"media/{profile['output']}",
        "enhanced_video": f"media/{profile['output']}",
        "presentation_video": f"media/{profile['output']}",
        "source_video": f"media/{profile['source_copy']}",
        "frames": frame_count,
        "fps": round(fps, 3),
        "resolution": f"{source_width}×{source_height}",
        "presentation_resolution": "1620×720",
        "duration_s": round(duration, 2),
        "algorithm": "YOLOE-26S开放词汇实例分割 + 四窗口重叠切片 + 跨帧平滑 + SOP状态机",
        "parts": [{"id": f"P{index + 1:02d}", "label": name, "mode": "模型紧边框"} for index, name in enumerate(DATASET_CLASSES[:7])],
        "steps": profile["steps"],
        "yolo_detection_counts": dict(counts),
        "visual_result": "PASS（演示时间基准）",
        "production_release": "HOLD",
        "release_reason": "缺少真实PSet、扭矩、角度报文与MES回执",
        "evidence_boundary": "所有新增框均为YOLOE-26S自动预标注，必须人工复核；当前结果不是量产精度验收",
        "snapshots": [f"snapshots/{profile['id']}/{path.name}" for path in sorted(snapshot_dir.glob("*.jpg"))],
        "sampled_images": sampled,
        "processing_seconds": round(time.time() - started, 1),
    }


def write_dataset_files() -> None:
    data_yaml = "\n".join(
        [
            f"path: {DATASET.as_posix()}",
            "train: images/train",
            "val: images/val",
            "test: images/test",
            "names:",
            *[f"  {index}: {name}" for index, name in enumerate(DATASET_CLASSES)],
            "",
        ]
    )
    (DATASET / "data.yaml").write_text(data_yaml, encoding="utf-8")
    (DATASET / "classes.json").write_text(
        json.dumps({"names": {index: name for index, name in enumerate(DATASET_CLASSES)}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def update_catalog(summaries: list[dict], processing_seconds: float) -> None:
    catalog_path = DATA_DIR / "videos.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    new_ids = {item["id"] for item in summaries}
    catalog["videos"] = [item for item in catalog["videos"] if item["id"] not in new_ids] + summaries
    catalog["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    catalog["totals"] = {
        "videos": len(catalog["videos"]),
        "frames": sum(int(item["frames"]) for item in catalog["videos"]),
        "duration_s": round(sum(float(item["duration_s"]) for item in catalog["videos"]), 2),
        "steps": sum(len(item["steps"]) for item in catalog["videos"]),
    }
    catalog["frontier_extension"] = {
        "version": "SOP-Frontier-2026.08.16",
        "videos_added": [item["id"] for item in summaries],
        "model": "YOLOE-26S-seg",
        "methods": ["开放词汇实例分割", "大零件掩膜紧边框", "四窗口重叠切片", "小目标候选NMS", "跨帧平滑", "无遮挡右侧SOP决策栏"],
        "dataset": "datasets/新增两视频_YOLOE26_SAHI细粒度预标注_待人工复核",
        "truth_policy": "自动预标注未经人工复核不得作为真实标签、模型精度或生产放行证据",
        "processing_seconds": round(processing_seconds, 1),
    }
    presentation = catalog.setdefault("presentation", {})
    presentation["layout"] = "左侧1280×720完整检测画面 + 右侧340×720 SOP看板；顶部工作栏不再遮挡视频"
    presentation["videos"] = [
        {
            "id": item["id"],
            "output": str(ROOT / "web" / item["presentation_video"]),
            "frames": item["frames"],
            "fps": item["fps"],
            "resolution": "1620×720",
            "duration_s": item["duration_s"],
        }
        for item in catalog["videos"]
    ]
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    started = time.time()
    for directory in (MEDIA_DIR, DATA_DIR, DATASET, QA_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    profiles = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    full_model = load_prompt_model(FULL_CLASSES)
    small_model = load_prompt_model(SMALL_CLASSES)
    manifest_path = DATASET / "manifest.jsonl"
    summaries = []
    with manifest_path.open("w", encoding="utf-8") as manifest_handle:
        for profile in profiles:
            summary = process_video(profile, full_model, small_model, manifest_handle)
            summaries.append(summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    write_dataset_files()
    elapsed = time.time() - started
    update_catalog(summaries, elapsed)
    split_counts = {
        split: len(list((DATASET / f"images/{split}").glob("*.jpg"))) for split in ("train", "val", "test")
    }
    stats = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": "YOLOE-26S-seg",
        "videos": summaries,
        "dataset_classes": DATASET_CLASSES,
        "split_counts": split_counts,
        "review_status": "全部待人工复核",
        "processing_seconds": round(elapsed, 1),
    }
    (DATASET / "dataset_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "qa/frontier_two_video_report.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
