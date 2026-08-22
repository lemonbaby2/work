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
SOURCE = Path(r"E:\宁波项目\测试验视频\de02e8d2b5e46b5f804650c4db9d3b98.mp4")
MODEL_PATH = ROOT / "models" / "yolov8s-worldv2.pt"
RECIPE_PATH = ROOT / "config" / "sop_recipe.json"
MEDIA_DIR = ROOT / "web" / "media"
DATA_DIR = ROOT / "web" / "data"
SNAPSHOT_DIR = ROOT / "web" / "snapshots"
TEMP_VIDEO = MEDIA_DIR / "仪表板SOP_YOLOv8逐帧识别_temp.mp4"
FINAL_VIDEO = MEDIA_DIR / "仪表板SOP_YOLOv8逐帧识别演示.mp4"
SOURCE_COPY = MEDIA_DIR / "原始测试视频_de02.mp4"

PROMPTS = ["hand", "power drill"]
CLASS_ZH = {"hand": "操作人员手部", "power drill": "电动紧固工具"}
COLORS = {
    "hand": (42, 203, 118),
    "power drill": (53, 145, 255),
    "part": (235, 174, 52),
    "step": (70, 215, 255),
}


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\simhei.ttf")):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


FONT_16 = font(16)
FONT_20 = font(20)
FONT_26 = font(26)
FONT_34 = font(34)


def box_px(roi: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    return tuple(int(v * (width if i % 2 == 0 else height)) for i, v in enumerate(roi))  # type: ignore[return-value]


def draw_label(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill: tuple[int, int, int]) -> None:
    x, y = xy
    bbox = draw.textbbox((x, y), text, font=FONT_16)
    draw.rounded_rectangle((bbox[0] - 5, bbox[1] - 3, bbox[2] + 5, bbox[3] + 3), radius=4, fill=fill)
    draw.text((x, y), text, font=FONT_16, fill="white")


def annotate_frame(
    frame: np.ndarray,
    elapsed: float,
    recipe: dict,
    detections: list[dict],
    current_step: dict,
    completed: int,
) -> np.ndarray:
    height, width = frame.shape[:2]
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Fixed-camera business ROIs are calibrated once and remain visible on every frame.
    for index, region in enumerate(recipe["part_regions"]):
        x1, y1, x2, y2 = box_px(region["roi"], width, height)
        color = (52, 174, 235, 185)
        draw.rectangle((x1, y1, x2, y2), outline=color, width=2)
        if index < 3:
            draw_label(draw, (x1 + 5, y1 + 4), region["label"], (31, 105, 142))

    sx1, sy1, sx2, sy2 = box_px(current_step["roi"], width, height)
    draw.rectangle((sx1, sy1, sx2, sy2), outline=(70, 215, 255, 255), width=5)
    draw_label(draw, (sx1 + 6, min(height - 30, sy2 - 28)), f"当前SOP区域：{current_step['label']}", (173, 118, 18))

    for detection in detections:
        x1, y1, x2, y2 = [int(v) for v in detection["xyxy"]]
        color_bgr = COLORS[detection["class"]]
        color = (color_bgr[2], color_bgr[1], color_bgr[0], 255)
        draw.rectangle((x1, y1, x2, y2), outline=color, width=4)
        draw_label(
            draw,
            (x1 + 4, max(3, y1 - 26)),
            f"{CLASS_ZH[detection['class']]} {detection['confidence']:.0%}",
            color[:3],
        )

    panel_height = 116
    draw.rounded_rectangle((18, 18, width - 18, 18 + panel_height), radius=12, fill=(10, 22, 36, 218))
    draw.text((36, 31), "仪表板装配 SOP 在线分析", font=FONT_26, fill="white")
    draw.text((36, 69), f"{current_step['id']}  {current_step['label']}", font=FONT_20, fill=(93, 225, 177))
    draw.text((36, 99), f"已完成 {completed}/6 · 视频 {elapsed:05.1f} 秒", font=FONT_16, fill=(200, 213, 225))
    right_text = "视觉顺序：运行中" if completed < 6 else "视觉顺序：已完成"
    right_fill = (255, 212, 88) if completed < 6 else (78, 223, 144)
    draw.text((width - 235, 44), right_text, font=FONT_20, fill=right_fill)
    draw.text((width - 235, 82), "生产放行：待扭矩/MES确认", font=FONT_16, fill=(255, 164, 106))
    image = Image.alpha_composite(image, overlay).convert("RGB")
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def main() -> None:
    started = time.time()
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    recipe = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    shutil.copy2(SOURCE, SOURCE_COPY)

    model = YOLOWorld(str(MODEL_PATH))
    model.set_classes(PROMPTS)
    capture = cv2.VideoCapture(str(SOURCE))
    if not capture.isOpened():
        raise RuntimeError(f"无法打开视频：{SOURCE}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(str(TEMP_VIDEO), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))

    frame_records: list[dict] = []
    detection_counts: Counter[str] = Counter()
    last_detections: list[dict] = []
    snapshot_seconds = {8, 18, 29, 41, 55, 69}
    saved_snapshots: set[int] = set()

    for frame_index in range(frame_count):
        ok, frame = capture.read()
        if not ok:
            break
        elapsed = frame_index / fps
        current_step = next((s for s in recipe["steps"] if s["start_s"] <= elapsed < s["end_s"]), recipe["steps"][-1])
        completed = sum(elapsed >= float(step["end_s"]) for step in recipe["steps"])

        # Inference every second frame; the intervening frame reuses the prior result.
        if frame_index % 2 == 0:
            result = model.predict(frame, imgsz=640, conf=0.05, iou=0.50, device=0, verbose=False)[0]
            last_detections = []
            if result.boxes is not None:
                for box in result.boxes:
                    cls_index = int(box.cls.item())
                    cls_name = PROMPTS[cls_index]
                    confidence = float(box.conf.item())
                    record = {
                        "class": cls_name,
                        "label": CLASS_ZH[cls_name],
                        "confidence": confidence,
                        "xyxy": [round(float(v), 1) for v in box.xyxy[0].tolist()],
                    }
                    last_detections.append(record)
                    detection_counts[cls_name] += 1

        output_frame = annotate_frame(frame, elapsed, recipe, last_detections, current_step, completed)
        writer.write(output_frame)
        second = int(round(elapsed))
        if second in snapshot_seconds and second not in saved_snapshots:
            cv2.imwrite(str(SNAPSHOT_DIR / f"{current_step['id']}_{second:02d}秒.jpg"), output_frame)
            saved_snapshots.add(second)

        frame_records.append(
            {
                "frame": frame_index,
                "time_s": round(elapsed, 3),
                "step_id": current_step["id"],
                "step_label": current_step["label"],
                "completed_steps": completed,
                "parts": [r["label"] for r in recipe["part_regions"]],
                "detections": last_detections,
            }
        )

    writer.release()
    capture.release()

    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(TEMP_VIDEO), "-c:v", "libx264", "-preset", "medium",
            "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(FINAL_VIDEO),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    TEMP_VIDEO.unlink(missing_ok=True)

    events = [
        {
            "time_s": step["end_s"],
            "step_id": step["id"],
            "step_label": step["label"],
            "status": "视觉复核通过",
            "evidence": "演示视频人工复核时间段 + YOLOv8手部/工具辅助框",
        }
        for step in recipe["steps"]
    ]
    summary = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(SOURCE),
        "video": "media/仪表板SOP_YOLOv8逐帧识别演示.mp4",
        "source_video": "media/原始测试视频_de02.mp4",
        "model": str(MODEL_PATH),
        "algorithm": "YOLOv8-World + 固定相机ROI + SOP顺序状态机",
        "gpu": "NVIDIA GeForce RTX 4060",
        "frames": len(frame_records),
        "fps": fps,
        "resolution": f"{width}×{height}",
        "duration_s": round(len(frame_records) / fps, 2),
        "part_labels_per_frame": len(recipe["part_regions"]),
        "yolo_detection_counts": {CLASS_ZH[k]: v for k, v in detection_counts.items()},
        "sequence_result": "视觉演示通过",
        "production_release": "HOLD",
        "production_release_reason": "测试视频不含拧紧控制器的PSet、扭矩、角度原始报文，也未收到MES应答",
        "evidence_boundary": "零件区域为固定相机方案的初始ROI标定；步骤时间为本段视频人工复核演示基准，不作为量产精度结论",
        "events": events,
        "processing_seconds": round(time.time() - started, 1),
    }
    (DATA_DIR / "dashboard.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (DATA_DIR / "frame_annotations.jsonl").open("w", encoding="utf-8") as handle:
        for row in frame_records:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
