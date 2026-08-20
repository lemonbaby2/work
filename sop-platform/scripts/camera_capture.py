#!/usr/bin/env python3
"""现场摄像头检测与 5 分钟视频证据采集。

默认输入为 OpenCV 支持的设备编号，也支持 RTSP/GigE URL。每个分段同时
生成带检测框的 MP4 和 JSONL 检测日志；完成后复制到 DGX 挂载目录，或
通过 SFTP 上传。程序故意不把检测结果当作 MES 放行结论。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOG = logging.getLogger("camera_capture")
STOP = False


@dataclass
class Segment:
    segment_id: str
    started_at: str
    started_mono: float
    video_path: Path
    log_path: Path
    writer: Any
    frames: int = 0
    detections: int = 0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="milliseconds")


def parse_source(value: str) -> int | str:
    return int(value) if value.isdigit() else value


def json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def load_detector(model_path: Path, confidence: float, device: str):
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError(
            "缺少 ultralytics。请先执行: python -m pip install -r requirements.txt"
        ) from exc
    if not model_path.exists():
        raise FileNotFoundError(f"模型文件不存在: {model_path}")
    model = YOLO(str(model_path))
    return model, confidence, device


def detect(model_bundle: Any, frame: Any) -> tuple[Any, list[dict[str, Any]]]:
    if model_bundle is None:
        return frame, []
    model, confidence, device = model_bundle
    result = model.predict(source=frame, conf=confidence, device=device, verbose=False)[0]
    annotated = result.plot()
    names = result.names if hasattr(result, "names") else {}
    records: list[dict[str, Any]] = []
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return annotated, records
    xyxy = boxes.xyxy.cpu().tolist()
    confs = boxes.conf.cpu().tolist()
    classes = boxes.cls.cpu().tolist()
    for box, score, class_id in zip(xyxy, confs, classes):
        class_id = int(class_id)
        records.append(
            {
                "label": str(names.get(class_id, class_id)),
                "class_id": class_id,
                "confidence": round(float(score), 5),
                "xyxy": [round(float(value), 2) for value in box],
            }
        )
    return annotated, records


def open_writer(cv2: Any, path: Path, fps: float, size: tuple[int, int]):
    path.parent.mkdir(parents=True, exist_ok=True)
    for codec in ("mp4v", "avc1"):
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec), fps, size)
        if writer.isOpened():
            return writer
        writer.release()
    raise RuntimeError("无法创建 MP4 写入器，请检查 OpenCV/FFmpeg 编码器")


def create_segment(cv2: Any, root: Path, fps: float, size: tuple[int, int], camera: str) -> Segment:
    now = utc_now()
    segment_id = f"{camera}_{now.strftime('%Y%m%dT%H%M%S.%fZ')}"
    staging = root / "staging"
    video_path = staging / f"{segment_id}.mp4"
    log_path = staging / f"{segment_id}.jsonl"
    writer = open_writer(cv2, video_path, fps, size)
    return Segment(segment_id, now.isoformat(timespec="milliseconds"), time.monotonic(), video_path, log_path, writer)


def finalize_segment(segment: Segment, root: Path, dgx_dir: Path | None, sftp: argparse.Namespace | None) -> None:
    segment.writer.release()
    final_dir = root / "segments"
    final_dir.mkdir(parents=True, exist_ok=True)
    final_video = final_dir / segment.video_path.name
    final_log = final_dir / segment.log_path.name
    segment.video_path.replace(final_video)
    segment.log_path.replace(final_log)
    metadata = {
        "segment_id": segment.segment_id,
        "started_at": segment.started_at,
        "finished_at": iso_now(),
        "frames": segment.frames,
        "detections": segment.detections,
        "video": final_video.name,
        "detections_log": final_log.name,
    }
    json_dump(final_video.with_suffix(".metadata.json"), metadata)
    if dgx_dir:
        dgx_dir.mkdir(parents=True, exist_ok=True)
        for path in (final_video, final_log, final_video.with_suffix(".metadata.json")):
            destination = dgx_dir / path.name
            if path.resolve() != destination.resolve():
                shutil.copy2(path, destination)
        LOG.info("已复制到 DGX 目录: %s", dgx_dir)
    if sftp and sftp.host:
        upload_sftp((final_video, final_log, final_video.with_suffix(".metadata.json")), sftp)
    LOG.info("分段完成: %s, frames=%d, detections=%d", segment.segment_id, segment.frames, segment.detections)


def upload_sftp(paths: tuple[Path, ...], options: argparse.Namespace) -> None:
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("使用 SFTP 需要安装 paramiko: python -m pip install paramiko") from exc
    transport = paramiko.Transport((options.host, options.port))
    transport.connect(username=options.user, password=options.password)
    client = paramiko.SFTPClient.from_transport(transport)
    try:
        try:
            client.stat(options.remote_dir)
        except IOError:
            client.mkdir(options.remote_dir)
        for path in paths:
            client.put(str(path), f"{options.remote_dir.rstrip('/')}/{path.name}")
        LOG.info("已通过 SFTP 上传到 DGX: %s", options.remote_dir)
    finally:
        client.close()
        transport.close()


def stop_handler(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="摄像头目标检测 + 每 5 分钟视频分段 + DGX 归档")
    parser.add_argument("--source", default="0", help="摄像头编号、视频文件或 RTSP/GigE URL；默认 0")
    parser.add_argument("--model", type=Path, default=Path("models/yolo26n.pt"), help="Ultralytics .pt 模型")
    parser.add_argument("--output-dir", type=Path, default=Path("runtime/camera_capture"))
    parser.add_argument("--dgx-dir", type=Path, default=None, help="DGX/NAS 已挂载目录；优先使用此方式")
    parser.add_argument("--segment-seconds", type=float, default=300.0, help="视频分段时长，默认 300 秒")
    parser.add_argument("--confidence", type=float, default=0.35)
    parser.add_argument("--device", default="0", help="Ultralytics 推理设备，如 0 或 cpu")
    parser.add_argument("--camera-name", default="CAM_TOP")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=float, default=25.0, help="摄像头不返回 FPS 时使用的值")
    parser.add_argument("--preview", action="store_true", help="打开实时预览窗口，按 q 退出")
    parser.add_argument("--no-preview", dest="preview", action="store_false", help=argparse.SUPPRESS)
    parser.add_argument("--no-detect", action="store_true", help="只采集视频，用于无模型/编码器验证")
    parser.add_argument("--test-seconds", type=float, default=0, help="演练时在指定秒数后退出，0 表示持续运行")
    parser.add_argument("--sftp-host", default="")
    parser.add_argument("--sftp-port", type=int, default=22)
    parser.add_argument("--sftp-user", default="")
    parser.add_argument("--sftp-password", default="")
    parser.add_argument("--sftp-remote-dir", default="/data/sop/camera_segments")
    return parser


def run(args: argparse.Namespace) -> int:
    try:
        import cv2
    except ImportError:
        LOG.error("缺少 opencv-python，请先执行: python -m pip install -r requirements.txt")
        return 2
    model_bundle = None if args.no_detect else load_detector(args.model, args.confidence, args.device)
    capture = cv2.VideoCapture(parse_source(args.source))
    if not capture.isOpened():
        LOG.error("无法打开摄像头/视频源: %s", args.source)
        return 3
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0) or args.fps
    sftp = argparse.Namespace(host=args.sftp_host, port=args.sftp_port, user=args.sftp_user, password=args.sftp_password, remote_dir=args.sftp_remote_dir) if args.sftp_host else None
    root = args.output_dir
    root.mkdir(parents=True, exist_ok=True)
    segment: Segment | None = None
    started = time.monotonic()
    try:
        while not STOP:
            ok, frame = capture.read()
            if not ok:
                LOG.warning("视频源读取结束或断开")
                break
            if segment is None:
                segment = create_segment(cv2, root, fps, (frame.shape[1], frame.shape[0]), args.camera_name)
                LOG.info("开始分段: %s", segment.segment_id)
            annotated, detections = detect(model_bundle, frame)
            observed_at = iso_now()
            segment.writer.write(annotated)
            with segment.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"timestamp": observed_at, "frame": segment.frames, "detections": detections}, ensure_ascii=False) + "\n")
            segment.frames += 1
            segment.detections += len(detections)
            if args.preview:
                cv2.imshow("Ningbo SOP camera", annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            if time.monotonic() - segment.started_mono >= args.segment_seconds:
                finalize_segment(segment, root, args.dgx_dir, sftp)
                segment = None
            if args.test_seconds and time.monotonic() - started >= args.test_seconds:
                break
    finally:
        capture.release()
        if args.preview:
            cv2.destroyAllWindows()
        if segment and segment.frames:
            finalize_segment(segment, root, args.dgx_dir, sftp)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    return run(build_parser().parse_args())


if __name__ == "__main__":
    sys.exit(main())
