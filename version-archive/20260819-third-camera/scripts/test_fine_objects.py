from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import cv2
from ultralytics import YOLOWorld


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models/yolov8s-worldv2.pt"
CLASSES = ["screw head", "bolt head", "nut", "plastic clip", "electrical connector", "wire harness", "air duct", "power drill", "hand"]
VIDEOS = [
    (Path(r"E:\宁波项目\测试验视频\de02e8d2b5e46b5f804650c4db9d3b98.mp4"), [18, 41, 55]),
    (Path(r"E:\宁波项目\测试验视频\d66dc6ba16fbda15ad503011839aca9f.mp4"), [20, 45, 60]),
    (Path(r"E:\宁波项目\测试验视频\e34460640936a0f224a42ac1abf265b0.mp4"), [16, 42, 70]),
]


def main() -> None:
    model = YOLOWorld(str(MODEL))
    model.set_classes(CLASSES)
    counts = defaultdict(int)
    max_conf = defaultdict(float)
    examples = defaultdict(list)
    for video, seconds_list in VIDEOS:
        capture = cv2.VideoCapture(str(video))
        fps = capture.get(cv2.CAP_PROP_FPS) or 30
        for seconds in seconds_list:
            capture.set(cv2.CAP_PROP_POS_FRAMES, round(seconds * fps))
            ok, frame = capture.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            tiles = [
                (0, 0, width * 2 // 3, height * 3 // 4),
                (width // 3, 0, width, height * 3 // 4),
                (0, height // 4, width * 2 // 3, height),
                (width // 3, height // 4, width, height),
            ]
            crops = [frame[y1:y2, x1:x2] for x1, y1, x2, y2 in tiles]
            results = model.predict(crops, imgsz=800, conf=0.008, iou=0.45, device=0, verbose=False)
            for tile_index, result in enumerate(results):
                if result.boxes is None:
                    continue
                for box in result.boxes:
                    name = CLASSES[int(box.cls.item())]
                    conf = float(box.conf.item())
                    counts[name] += 1
                    max_conf[name] = max(max_conf[name], conf)
                    if len(examples[name]) < 5:
                        examples[name].append({"video": video.stem, "second": seconds, "tile": tile_index, "confidence": round(conf, 4)})
        capture.release()
    report = {name: {"count_at_0.008": counts[name], "max_confidence": round(max_conf[name], 4), "examples": examples[name]} for name in CLASSES}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
