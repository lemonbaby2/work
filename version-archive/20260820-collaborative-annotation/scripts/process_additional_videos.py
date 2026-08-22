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
from ultralytics import YOLOWorld


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "models/yolov8s-worldv2.pt"
PROFILE_PATH = ROOT / "config/additional_video_profiles.json"
MEDIA_DIR = ROOT / "web/media"
DATA_DIR = ROOT / "web/data"
SNAPSHOT_ROOT = ROOT / "web/snapshots"
PROMPTS = ["hand", "power drill"]
CLASS_ZH = {"hand": "操作人员手部", "power drill": "电动紧固工具"}


def load_font(size: int):
    for candidate in (Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\simhei.ttf")):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


FONT_15 = load_font(15)
FONT_18 = load_font(18)
FONT_25 = load_font(25)


def pixels(roi: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    return tuple(int(value * (width if index % 2 == 0 else height)) for index, value in enumerate(roi))  # type: ignore[return-value]


def text_tag(draw, point, text, color):
    box = draw.textbbox(point, text, font=FONT_15)
    draw.rounded_rectangle((box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2), radius=3, fill=color)
    draw.text(point, text, font=FONT_15, fill="white")


def render(frame, elapsed, profile, detections, step, completed):
    height, width = frame.shape[:2]
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for index, region in enumerate(profile["part_regions"]):
        x1, y1, x2, y2 = pixels(region["roi"], width, height)
        draw.rectangle((x1, y1, x2, y2), outline=(48, 171, 224, 185), width=2)
        if index < 3:
            text_tag(draw, (x1 + 4, y1 + 3), region["label"], (28, 106, 142))
    sx1, sy1, sx2, sy2 = pixels(step["roi"], width, height)
    draw.rectangle((sx1, sy1, sx2, sy2), outline=(77, 224, 244, 255), width=5)
    text_tag(draw, (sx1 + 5, max(4, sy2 - 26)), f"当前区域：{step['label']}", (174, 116, 17))
    for item in detections:
        x1, y1, x2, y2 = [int(value) for value in item["xyxy"]]
        color = (61, 201, 126) if item["class"] == "hand" else (255, 149, 62)
        draw.rectangle((x1, y1, x2, y2), outline=color, width=4)
        track = f" #{item['track_id']}" if item.get("track_id") is not None else ""
        text_tag(draw, (x1 + 3, max(3, y1 - 24)), f"{item['label']}{track} {item['confidence']:.0%}", color)
    draw.rounded_rectangle((18, 18, width - 18, 130), radius=12, fill=(9, 24, 37, 222))
    draw.text((35, 30), "仪表板装配 SOP · 智能决策端", font=FONT_25, fill="white")
    draw.text((35, 66), f"{step['id']}  {step['label']}  ·  已完成 {completed}/6", font=FONT_18, fill=(88, 225, 182))
    draw.text((35, 96), f"YOLOv8 + BoT-SORT跟踪  |  {elapsed:05.1f}秒", font=FONT_15, fill=(193, 208, 217))
    draw.text((width - 325, 38), "决策：视觉步骤运行中", font=FONT_18, fill=(255, 215, 91))
    draw.text((width - 325, 70), "建议：完成当前步骤后再流转", font=FONT_15, fill=(195, 217, 228))
    draw.text((width - 325, 98), "放行：HOLD（缺扭矩/MES）", font=FONT_15, fill=(255, 160, 100))
    return cv2.cvtColor(np.asarray(Image.alpha_composite(image, overlay).convert("RGB")), cv2.COLOR_RGB2BGR)


def process(profile: dict) -> dict:
    started = time.time()
    source = Path(profile["source"])
    output = MEDIA_DIR / profile["output"]
    temp = MEDIA_DIR / f"{profile['id']}_temp.mp4"
    source_copy = MEDIA_DIR / profile["source_copy"]
    snapshot_dir = SNAPSHOT_ROOT / profile["id"]
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, source_copy)
    model = YOLOWorld(str(MODEL_PATH))
    model.set_classes(PROMPTS)
    capture = cv2.VideoCapture(str(source))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(str(temp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    records, last_detections = [], []
    counts: Counter[str] = Counter()
    unique_tracks: dict[str, set[int]] = {name: set() for name in PROMPTS}
    snapshot_targets = {round((float(step["start_s"]) + float(step["end_s"])) / 2) for step in profile["steps"]}
    snapshots_done: set[int] = set()
    for frame_index in range(frame_count):
        ok, frame = capture.read()
        if not ok:
            break
        elapsed = frame_index / fps
        step = next((item for item in profile["steps"] if item["start_s"] <= elapsed < item["end_s"]), profile["steps"][-1])
        completed = sum(elapsed >= float(item["end_s"]) for item in profile["steps"])
        if frame_index % 2 == 0:
            result = model.track(frame, persist=True, tracker="botsort.yaml", imgsz=640, conf=0.05, iou=0.5, device=0, verbose=False)[0]
            last_detections = []
            if result.boxes is not None:
                ids = result.boxes.id.tolist() if result.boxes.id is not None else [None] * len(result.boxes)
                for box, track_id in zip(result.boxes, ids):
                    cls_name = PROMPTS[int(box.cls.item())]
                    track_value = int(track_id) if track_id is not None else None
                    record = {"class": cls_name, "label": CLASS_ZH[cls_name], "confidence": float(box.conf.item()), "track_id": track_value, "xyxy": [round(float(v), 1) for v in box.xyxy[0].tolist()]}
                    last_detections.append(record)
                    counts[cls_name] += 1
                    if track_value is not None:
                        unique_tracks[cls_name].add(track_value)
        decision = {
            "visual_state": "RUNNING" if completed < len(profile["steps"]) else "PASS",
            "release": "HOLD",
            "risk_level": "中",
            "reasons": ["当前步骤按人工复核时间基准运行", "已执行YOLOv8目标检测与跨帧跟踪", "缺少拧紧控制器和MES实时证据"],
            "recommended_action": f"继续完成{step['id']}：{step['label']}" if completed < 6 else "等待扭矩和MES确认后放行",
        }
        output_frame = render(frame, elapsed, profile, last_detections, step, completed)
        writer.write(output_frame)
        second = int(round(elapsed))
        if second in snapshot_targets and second not in snapshots_done:
            cv2.imwrite(str(snapshot_dir / f"{step['id']}_{second:02d}秒.jpg"), output_frame)
            snapshots_done.add(second)
        records.append({"frame": frame_index, "time_s": round(elapsed, 3), "step_id": step["id"], "step_label": step["label"], "completed_steps": completed, "parts": [item["label"] for item in profile["part_regions"]], "detections": last_detections, "decision": decision})
    writer.release(); capture.release()
    subprocess.run(["ffmpeg", "-y", "-i", str(temp), "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(output)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    temp.unlink(missing_ok=True)
    jsonl = DATA_DIR / f"{profile['id']}_frame_annotations.jsonl"
    with jsonl.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary = {
        "id": profile["id"], "display_name": profile["display_name"], "source": str(source),
        "video": f"media/{profile['output']}", "source_video": f"media/{profile['source_copy']}",
        "frames": len(records), "fps": round(fps, 3), "resolution": f"{width}×{height}", "duration_s": round(len(records) / fps, 2),
        "algorithm": "YOLOv8-World + BoT-SORT跨帧跟踪 + 固定相机ROI + SOP状态机 + 可解释决策门",
        "parts": profile["part_regions"], "steps": profile["steps"],
        "yolo_detection_counts": {CLASS_ZH[key]: value for key, value in counts.items()},
        "unique_track_counts": {CLASS_ZH[key]: len(value) for key, value in unique_tracks.items()},
        "visual_result": "PASS（演示基准）", "production_release": "HOLD",
        "release_reason": "缺少真实PSet/扭矩/角度报文与MES回执",
        "evidence_boundary": "步骤区间由视频人工复核建立；零件ROI用于固定相机方案演示，不能代替量产专用模型精度验收",
        "snapshots": [f"snapshots/{profile['id']}/{path.name}" for path in sorted(snapshot_dir.glob("*.jpg"))],
        "processing_seconds": round(time.time() - started, 1),
    }
    (DATA_DIR / f"{profile['id']}_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main():
    MEDIA_DIR.mkdir(parents=True, exist_ok=True); DATA_DIR.mkdir(parents=True, exist_ok=True)
    profiles = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    summaries = [process(profile) for profile in profiles]
    first = json.loads((DATA_DIR / "dashboard.json").read_text(encoding="utf-8"))
    original_recipe = json.loads((ROOT / "config/sop_recipe.json").read_text(encoding="utf-8"))
    first_summary = {
        "id": "video_de02", "display_name": "视频一｜仪表板风道装配与紧固", "source": first["source"],
        "video": first["video"], "source_video": first["source_video"], "frames": first["frames"], "fps": round(first["fps"], 3),
        "resolution": first["resolution"], "duration_s": first["duration_s"], "algorithm": first["algorithm"],
        "parts": original_recipe["part_regions"], "steps": original_recipe["steps"], "yolo_detection_counts": first["yolo_detection_counts"],
        "unique_track_counts": {}, "visual_result": first["sequence_result"], "production_release": first["production_release"],
        "release_reason": first["production_release_reason"], "evidence_boundary": first["evidence_boundary"],
        "snapshots": [f"snapshots/{path.name}" for path in sorted((SNAPSHOT_ROOT).glob("S*.jpg"))]
    }
    all_videos = [first_summary, *summaries]
    aggregate = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "videos": all_videos,
        "totals": {"videos": len(all_videos), "frames": sum(item["frames"] for item in all_videos), "duration_s": round(sum(item["duration_s"] for item in all_videos), 2), "steps": sum(len(item["steps"]) for item in all_videos)},
        "decision_engine": {"version": "SOP-Decision-2026.08", "layers": ["YOLOv8目标检测", "BoT-SORT跨帧跟踪", "固定相机ROI", "连续帧去抖", "SOP顺序状态机", "扭矩/MES放行门", "可解释原因与人工复核"], "safety_policy": "AI提供证据和建议；确定性规则负责生产放行，缺失证据一律HOLD"}
    }
    (DATA_DIR / "videos.json").write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(aggregate["totals"], ensure_ascii=False))


if __name__ == "__main__":
    main()
