from __future__ import annotations

import json
from pathlib import Path

import cv2
from ultralytics import YOLO


MODELS = [
    Path(r"E:\宁波项目\lzp第三次代码\weights\yolov8s_lzp_v3_best.pt"),
    Path(r"E:\宁波项目\三模型VSCode_GPU交付_2026-08-12\weights_public\yolov8s_public_best.pt"),
    Path(r"E:\宁波项目\ai-fastener-inspection\weights\yolov8s_best.pt"),
]
VIDEO = Path(r"E:\宁波项目\测试验视频\de02e8d2b5e46b5f804650c4db9d3b98.mp4")


def main() -> None:
    capture = cv2.VideoCapture(str(VIDEO))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    sample_indices = [round(i * (frame_count - 1) / 11) for i in range(12)]
    frames = []
    for index in sample_indices:
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if ok:
            frames.append(frame)
    capture.release()

    report = []
    for path in MODELS:
        if not path.exists():
            report.append({"path": str(path), "error": "missing"})
            continue
        model = YOLO(str(path))
        results = model.predict(frames, imgsz=640, conf=0.10, device=0, verbose=False)
        counts: dict[str, int] = {}
        confidences: list[float] = []
        for result in results:
            if result.boxes is None:
                continue
            for cls, conf in zip(result.boxes.cls.tolist(), result.boxes.conf.tolist()):
                name = str(model.names[int(cls)])
                counts[name] = counts.get(name, 0) + 1
                confidences.append(float(conf))
        report.append(
            {
                "path": str(path),
                "names": model.names,
                "sample_frames": len(frames),
                "detections_conf_0.10": counts,
                "max_confidence": round(max(confidences), 4) if confidences else None,
                "mean_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
            }
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
