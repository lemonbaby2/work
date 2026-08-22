from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "web/data"
MEDIA_DIR = ROOT / "web/media"
MODEL = Path(r"E:\宁波项目\lzp第三次代码\weights\yolov8s_lzp_v3_best.pt")
DATASET = ROOT / "datasets/三视频多物体预标注_待人工复核"
FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")
FONT = ImageFont.truetype(str(FONT_PATH), 14) if FONT_PATH.exists() else ImageFont.load_default()


def nms(items: list[dict], threshold: float = 0.32) -> list[dict]:
    if not items:
        return []
    boxes = []
    scores = []
    for item in items:
        x1, y1, x2, y2 = item["xyxy"]
        boxes.append([int(x1), int(y1), int(x2 - x1), int(y2 - y1)])
        scores.append(float(item["confidence"]))
    indexes = cv2.dnn.NMSBoxes(boxes, scores, 0.60, threshold)
    return [items[int(index)] for index in np.array(indexes).reshape(-1)] if len(indexes) else []


def candidate_boxes(model: YOLO, frame: np.ndarray) -> list[dict]:
    height, width = frame.shape[:2]
    tile_specs = [
        (0, 0, width * 2 // 3, height * 3 // 4),
        (width // 3, 0, width, height * 3 // 4),
        (0, height // 4, width * 2 // 3, height),
        (width // 3, height // 4, width, height),
    ]
    crops = [frame[y1:y2, x1:x2] for x1, y1, x2, y2 in tile_specs]
    results = model.predict(crops, imgsz=768, conf=0.60, iou=0.42, device=0, verbose=False)
    proposals = []
    for (offset_x, offset_y, _, _), result in zip(tile_specs, results):
        if result.boxes is None:
            continue
        for box in result.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            x1 += offset_x; x2 += offset_x; y1 += offset_y; y2 += offset_y
            box_width, box_height = x2 - x1, y2 - y1
            aspect = box_width / max(box_height, 1)
            if 5 <= box_width <= 110 and 5 <= box_height <= 110 and 0.40 <= aspect <= 2.50:
                proposals.append({
                    "label": "紧固点候选（待人工复核）",
                    "confidence": round(float(box.conf.item()), 4),
                    "xyxy": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                    "source": "YOLOv8切片小目标候选器",
                    "review_status": "pending",
                })
    return nms(proposals)


def draw_candidates(frame: np.ndarray, candidates: list[dict]) -> np.ndarray:
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for item in candidates:
        x1, y1, x2, y2 = [int(value) for value in item["xyxy"]]
        color = (228, 72, 64, 255)
        draw.rectangle((x1, y1, x2, y2), outline=color, width=3)
        text = f"紧固点候选 {item['confidence']:.0%}"
        text_box = draw.textbbox((x1, max(2, y1 - 21)), text, font=FONT)
        draw.rectangle((text_box[0] - 3, text_box[1] - 1, text_box[2] + 3, text_box[3] + 1), fill=(173, 40, 36, 230))
        draw.text((x1, max(2, y1 - 21)), text, font=FONT, fill="white")
    draw.rounded_rectangle((18, image.height - 50, 420, image.height - 14), radius=7, fill=(70, 19, 18, 210))
    draw.text((30, image.height - 42), f"小目标增强：{len(candidates)}个候选，须人工复核后才能训练/放行", font=FONT, fill=(255, 220, 215))
    return cv2.cvtColor(np.asarray(Image.alpha_composite(image, overlay).convert("RGB")), cv2.COLOR_RGB2BGR)


def load_records(video_id: str) -> list[dict]:
    filename = "frame_annotations.jsonl" if video_id == "video_de02" else f"{video_id}_frame_annotations.jsonl"
    return [json.loads(line) for line in (DATA_DIR / filename).read_text(encoding="utf-8").splitlines()]


def class_for_region(label: str) -> str:
    if "仪表板" in label:
        return "仪表板总成"
    if "线束" in label or "接插件" in label:
        return "线束/接插件区域"
    if "风道" in label:
        return "风道组件"
    return "骨架/模块组件"


def yolo_line(class_id: int, box: list[float], width: int, height: int, normalized: bool = False) -> str:
    if normalized:
        x1, y1, x2, y2 = box
    else:
        x1, y1, x2, y2 = box[0] / width, box[1] / height, box[2] / width, box[3] / height
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    bw, bh = x2 - x1, y2 - y1
    values = [max(0.0, min(1.0, value)) for value in (cx, cy, bw, bh)]
    return f"{class_id} " + " ".join(f"{value:.6f}" for value in values)


def main() -> None:
    started = time.time()
    aggregate = json.loads((DATA_DIR / "videos.json").read_text(encoding="utf-8"))
    class_names = ["仪表板总成", "风道组件", "骨架/模块组件", "线束/接插件区域", "操作人员手部", "电动紧固工具", "紧固点候选_待人工复核"]
    class_ids = {name: index for index, name in enumerate(class_names)}
    image_dir = DATASET / "images/pending_review"
    label_dir = DATASET / "labels/pending_review"
    image_dir.mkdir(parents=True, exist_ok=True); label_dir.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(MODEL))
    dataset_manifest = []
    enhanced_summaries = []
    candidate_total = 0
    for video in aggregate["videos"]:
        raw_path = ROOT / "web" / video["source_video"]
        base_path = ROOT / "web" / video["video"]
        records = load_records(video["id"])
        raw = cv2.VideoCapture(str(raw_path)); base = cv2.VideoCapture(str(base_path))
        fps = float(raw.get(cv2.CAP_PROP_FPS) or 30); frame_count = int(raw.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(raw.get(cv2.CAP_PROP_FRAME_WIDTH)); height = int(raw.get(cv2.CAP_PROP_FRAME_HEIGHT))
        enhanced_name = Path(video["video"]).stem + "_小目标增强版.mp4"
        temp = MEDIA_DIR / f"{video['id']}_small_temp.mp4"; final = MEDIA_DIR / enhanced_name
        writer = cv2.VideoWriter(str(temp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
        candidate_log = []; last_candidates = []; counts = Counter()
        for frame_index in range(frame_count):
            ok_raw, raw_frame = raw.read(); ok_base, base_frame = base.read()
            if not ok_raw or not ok_base: break
            if frame_index % 10 == 0:
                last_candidates = candidate_boxes(model, raw_frame)
            candidate_total += len(last_candidates); counts["fastener_candidate_frames"] += int(bool(last_candidates))
            writer.write(draw_candidates(base_frame, last_candidates))
            candidate_log.append({"frame": frame_index, "time_s": round(frame_index / fps, 3), "candidates": last_candidates})
            if frame_index % 15 == 0:
                stem = f"{video['id']}_{frame_index:06d}"
                image_path = image_dir / f"{stem}.jpg"; label_path = label_dir / f"{stem}.txt"
                cv2.imwrite(str(image_path), raw_frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
                lines = []
                annotations = []
                for region in video["parts"]:
                    class_name = class_for_region(region["label"])
                    lines.append(yolo_line(class_ids[class_name], region["roi"], width, height, normalized=True))
                    annotations.append({"class": class_name, "box": region["roi"], "source": "business_roi_seed", "review_status": "pending"})
                for detection in records[min(frame_index, len(records) - 1)].get("detections", []):
                    class_name = detection["label"]
                    if class_name in class_ids:
                        lines.append(yolo_line(class_ids[class_name], detection["xyxy"], width, height))
                        annotations.append({"class": class_name, "box": detection["xyxy"], "confidence": detection["confidence"], "source": "YOLOv8_pseudo_label", "review_status": "pending"})
                for candidate in last_candidates:
                    lines.append(yolo_line(class_ids["紧固点候选_待人工复核"], candidate["xyxy"], width, height))
                    annotations.append({"class": "紧固点候选_待人工复核", "box": candidate["xyxy"], "confidence": candidate["confidence"], "source": candidate["source"], "review_status": "pending"})
                label_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                dataset_manifest.append({"image": str(image_path.relative_to(DATASET)), "label": str(label_path.relative_to(DATASET)), "video_id": video["id"], "frame": frame_index, "time_s": round(frame_index / fps, 3), "annotations": annotations, "overall_status": "pending_human_review"})
        writer.release(); raw.release(); base.release()
        subprocess.run(["ffmpeg", "-y", "-i", str(temp), "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(final)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        temp.unlink(missing_ok=True)
        (DATA_DIR / f"{video['id']}_fastener_candidates.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in candidate_log) + "\n", encoding="utf-8")
        video["enhanced_video"] = f"media/{enhanced_name}"
        video["fastener_candidate_frames"] = counts["fastener_candidate_frames"]
        enhanced_summaries.append({"id": video["id"], "enhanced_video": video["enhanced_video"], "frames": len(candidate_log), "candidate_frames": counts["fastener_candidate_frames"]})
    aggregate["small_object_enhancement"] = {
        "method": "四窗口重叠切片 + YOLOv8紧固件候选模型 + NMS + 人工复核队列",
        "classes": class_names,
        "candidate_occurrences_propagated": candidate_total,
        "truth_policy": "紧固点候选未经人工复核不得作为真实螺钉标签、模型精度或生产放行证据",
        "dataset": "datasets/三视频多物体预标注_待人工复核",
        "sampled_images": len(dataset_manifest),
    }
    (DATA_DIR / "videos.json").write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
    with (DATASET / "manifest.jsonl").open("w", encoding="utf-8") as handle:
        for row in dataset_manifest: handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (DATASET / "classes.json").write_text(json.dumps({"names": {index: name for index, name in enumerate(class_names)}}, ensure_ascii=False, indent=2), encoding="utf-8")
    stats = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "videos": len(enhanced_summaries), "sampled_images": len(dataset_manifest), "class_names": class_names, "review_status": "全部待人工复核", "enhanced_videos": enhanced_summaries, "processing_seconds": round(time.time() - started, 1)}
    (DATASET / "dataset_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
