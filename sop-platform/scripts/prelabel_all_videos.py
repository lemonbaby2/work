from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

import cv2
from ultralytics import YOLOE


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
CATALOG_PATH = WEB_ROOT / "data" / "videos.json"
DB_PATH = ROOT / "runtime" / "sop_annotations.sqlite3"
STATUS_PATH = ROOT / "runtime" / "ai_prelabel_status.json"
DEFAULT_MODEL = Path("/home/xjai/sop-model-store/yoloe-26s-seg.pt")
PROMPTS = [
    ("printed circuit board", "PCB板"),
    ("human hand", "操作人员手部"),
    ("factory worker", "操作人员"),
    ("screwdriver", "螺丝刀"),
    ("screw", "螺丝"),
    ("soldering iron", "电烙铁"),
    ("precision tweezers", "镊子"),
    ("assembly fixture", "治具"),
    ("plastic parts bin", "物料盒"),
    ("electrical connector", "连接器"),
    ("electrical plug", "电源插头"),
    ("electrical cable", "线缆"),
    ("computer monitor", "测试电脑屏幕"),
    ("barcode label", "标签/条码"),
    ("electronic product housing", "产品/外壳"),
    ("electronic component", "电子物料"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prelabel every fifth frame in every SOP video")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--stride", type=int, default=5)
    parser.add_argument("--batch", type=int, default=12)
    parser.add_argument("--imgsz", type=int, default=960)
    return parser.parse_args()


def read_json(path: Path, default: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except (OSError, json.JSONDecodeError):
        return default


def update_status(**values: object) -> dict:
    current = read_json(STATUS_PATH, {})
    current.update(values)
    current["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATUS_PATH)
    return current


def should_pause() -> bool:
    return read_json(STATUS_PATH, {}).get("control") == "pause"


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=60)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=60000")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS ai_prelabel_frames (
               video_id TEXT NOT NULL,
               frame INTEGER NOT NULL,
               detection_count INTEGER NOT NULL,
               model_name TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               PRIMARY KEY(video_id, frame)
           )"""
    )
    return connection


def video_path(item: dict) -> Path:
    relative = str(item.get("source_video") or item.get("video") or "").lstrip("/")
    candidate = (WEB_ROOT / relative).resolve()
    if candidate.is_file() and WEB_ROOT.resolve() in candidate.parents:
        return candidate
    source = Path(str(item.get("source") or ""))
    if source.is_file():
        return source
    raise RuntimeError(f"视频文件不存在: {item.get('id')}")


def save_batch(
    connection: sqlite3.Connection,
    video: dict,
    jobs: list[tuple[int, float, int, int]],
    results: list,
    model_name: str,
) -> int:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    detection_total = 0
    with connection:
        for (frame_index, video_time, width, height), result in zip(jobs, results):
            detections = []
            for detection_index, box in enumerate(result.boxes or []):
                class_index = int(box.cls.item())
                if class_index < 0 or class_index >= len(PROMPTS):
                    continue
                confidence = float(box.conf.item())
                if confidence < 0.035:
                    continue
                x1, y1, x2, y2 = [float(value) for value in box.xyxy[0].tolist()]
                if x2 - x1 < 4 or y2 - y1 < 4:
                    continue
                normalized = [
                    round(max(0.0, x1 / width), 6),
                    round(max(0.0, y1 / height), 6),
                    round(min(1.0, x2 / width), 6),
                    round(min(1.0, y2 / height), 6),
                ]
                annotation_id = f"ai5:{video['id']}:{frame_index}:{detection_index}"
                payload = {
                    "annotation_id": annotation_id,
                    "video_id": video["id"],
                    "video": video.get("source_video"),
                    "frame": frame_index,
                    "video_time": round(video_time, 6),
                    "label": PROMPTS[class_index][1],
                    "class": PROMPTS[class_index][0],
                    "confidence": round(confidence, 5),
                    "box": normalized,
                    "box_pixels": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                    "box_format": "normalized_xyxy",
                    "source_kind": "prelabel",
                    "source": f"YOLOE开放词汇AI每5帧预标注/{model_name}",
                    "region": "AI待分区",
                    "track_id": None,
                    "review_status": "pending",
                    "recorded_at": now,
                }
                connection.execute(
                    """INSERT INTO annotations (
                           annotation_id, video_id, frame, video_time, label, region, track_id,
                           source_kind, review_status, payload_json, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(annotation_id) DO UPDATE SET
                           payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                    (
                        annotation_id, video["id"], frame_index, video_time, payload["label"],
                        payload["region"], None, "prelabel", "pending",
                        json.dumps(payload, ensure_ascii=False), now, now,
                    ),
                )
                detections.append(payload)
            connection.execute(
                "INSERT OR REPLACE INTO ai_prelabel_frames VALUES (?, ?, ?, ?, ?)",
                (video["id"], frame_index, len(detections), model_name, now),
            )
            detection_total += len(detections)
    return detection_total


def main() -> None:
    args = parse_args()
    if args.stride != 5:
        raise ValueError("本项目预标注帧间隔固定为5")
    if not args.model.is_file():
        raise RuntimeError(f"模型不存在: {args.model}")
    catalog = read_json(CATALOG_PATH, {})
    videos = list(catalog.get("videos") or [])
    if not videos:
        raise RuntimeError("视频目录为空")
    available_videos = []
    missing_video_ids = []
    for video in videos:
        try:
            available_videos.append((video, video_path(video)))
        except RuntimeError:
            missing_video_ids.append(str(video.get("id")))
    if not available_videos:
        raise RuntimeError("目录中的视频文件均不存在")
    total_frames = sum((max(0, int(video.get("frames") or 0) - 1) // args.stride) + 1 for video, _ in available_videos)
    connection = connect_db()
    completed_before = int(connection.execute("SELECT COUNT(*) FROM ai_prelabel_frames").fetchone()[0])
    model_name = args.model.name
    update_status(
        status="loading_model", control="run", stride=args.stride, model=model_name,
        videos_total=len(videos), videos_available=len(available_videos), missing_video_ids=missing_video_ids,
        sampled_frames_total=total_frames,
        sampled_frames_completed=completed_before, message="正在加载开放词汇模型",
    )
    previous = Path.cwd()
    os.chdir(args.model.parent)
    try:
        model = YOLOE(str(args.model))
        prompts = [item[0] for item in PROMPTS]
        model.set_classes(prompts, model.get_text_pe(prompts))
        completed = completed_before
        detections_total = int(connection.execute("SELECT COALESCE(SUM(detection_count), 0) FROM ai_prelabel_frames").fetchone()[0])
        started = time.time()
        for video_index, (video, source_path) in enumerate(available_videos, 1):
            video_id = str(video["id"])
            completed_frames = {int(row[0]) for row in connection.execute("SELECT frame FROM ai_prelabel_frames WHERE video_id = ?", (video_id,))}
            capture = cv2.VideoCapture(str(source_path))
            if not capture.isOpened():
                raise RuntimeError(f"无法打开视频: {video_id}")
            fps = float(capture.get(cv2.CAP_PROP_FPS) or video.get("fps") or 30)
            batch_frames = []
            batch_jobs: list[tuple[int, float, int, int]] = []

            def flush() -> None:
                nonlocal completed, detections_total
                if not batch_frames:
                    return
                results = model.predict(batch_frames, imgsz=args.imgsz, conf=0.025, iou=0.45, max_det=100, device=0, verbose=False)
                detections_total += save_batch(connection, video, batch_jobs, results, model_name)
                completed += len(batch_jobs)
                elapsed = max(0.001, time.time() - started)
                rate = max(0.001, (completed - completed_before) / elapsed)
                remaining = max(0, total_frames - completed)
                update_status(
                    status="running", current_video_id=video_id, current_video_index=video_index,
                    sampled_frames_completed=completed, detections_total=detections_total,
                    progress=round(min(100, completed * 100 / max(1, total_frames)), 2),
                    eta_seconds=round(remaining / rate),
                    message=f"正在处理第 {video_index}/{len(available_videos)} 个可用视频，每5帧预标注",
                )
                batch_frames.clear()
                batch_jobs.clear()

            frame_index = 0
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                if frame_index % args.stride == 0 and frame_index not in completed_frames:
                    height, width = frame.shape[:2]
                    batch_frames.append(frame)
                    batch_jobs.append((frame_index, frame_index / fps, width, height))
                    if len(batch_frames) >= args.batch:
                        flush()
                        if should_pause():
                            capture.release()
                            update_status(status="paused", message="任务已暂停，可从已完成帧继续")
                            return
                frame_index += 1
            flush()
            capture.release()
        update_status(status="completed", control="run", progress=100, sampled_frames_completed=completed, detections_total=detections_total, eta_seconds=0, message="全部视频每5帧AI预标注完成，等待人工复核")
    except Exception as exc:
        update_status(status="failed", message=str(exc))
        raise
    finally:
        os.chdir(previous)
        connection.close()


if __name__ == "__main__":
    main()
