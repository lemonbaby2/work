from __future__ import annotations

import json
from pathlib import Path

import cv2
from ultralytics import YOLOWorld


VIDEO = Path(r"E:\宁波项目\测试验视频\de02e8d2b5e46b5f804650c4db9d3b98.mp4")
MODEL = Path(r"E:\宁波项目\SOP分析平台_老板汇报版\models\yolov8s-worldv2.pt")
CLASSES = [
    "car dashboard",
    "plastic car part",
    "air vent",
    "cable",
    "electrical plug",
    "power drill",
    "hand",
]


def main() -> None:
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    model = YOLOWorld(str(MODEL) if MODEL.exists() else "yolov8s-worldv2.pt")
    if not MODEL.exists():
        Path("yolov8s-worldv2.pt").replace(MODEL)
        model = YOLOWorld(str(MODEL))
    model.set_classes(CLASSES)
    capture = cv2.VideoCapture(str(VIDEO))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    samples = []
    for index in [round(i * (frame_count - 1) / 15) for i in range(16)]:
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if ok:
            samples.append((index, frame))
    capture.release()
    results = model.predict([frame for _, frame in samples], imgsz=960, conf=0.01, device=0, verbose=False)
    summary = []
    for (index, _), result in zip(samples, results):
        items = []
        if result.boxes is not None:
            for box in result.boxes:
                cls = int(box.cls.item())
                items.append({"class": CLASSES[cls], "confidence": round(float(box.conf.item()), 3)})
        summary.append({"frame": index, "items": items})
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
