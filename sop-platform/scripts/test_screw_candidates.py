from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[1]
MODEL = Path(r"E:\宁波项目\lzp第三次代码\weights\yolov8s_lzp_v3_best.pt")
VIDEOS = [
    (Path(r"E:\宁波项目\测试验视频\de02e8d2b5e46b5f804650c4db9d3b98.mp4"), [18, 41, 55]),
    (Path(r"E:\宁波项目\测试验视频\d66dc6ba16fbda15ad503011839aca9f.mp4"), [20, 45, 60]),
    (Path(r"E:\宁波项目\测试验视频\e34460640936a0f224a42ac1abf265b0.mp4"), [16, 42, 70]),
]
OUTPUT = ROOT / "analysis/screw_candidate_test"


def nms(items: list[tuple[list[float], float]], threshold: float = 0.35):
    if not items:
        return []
    boxes = [[int(x1), int(y1), int(x2 - x1), int(y2 - y1)] for (x1, y1, x2, y2), _ in items]
    scores = [score for _, score in items]
    indexes = cv2.dnn.NMSBoxes(boxes, scores, 0.45, threshold)
    return [items[int(index)] for index in np.array(indexes).reshape(-1)] if len(indexes) else []


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(MODEL))
    tiles_per_frame = []
    for video, seconds_list in VIDEOS:
        capture = cv2.VideoCapture(str(video)); fps = capture.get(cv2.CAP_PROP_FPS) or 30
        for seconds in seconds_list:
            capture.set(cv2.CAP_PROP_POS_FRAMES, round(seconds * fps)); ok, frame = capture.read()
            if not ok: continue
            h, w = frame.shape[:2]
            tile_specs = [(0, 0, w * 2 // 3, h * 3 // 4), (w // 3, 0, w, h * 3 // 4), (0, h // 4, w * 2 // 3, h), (w // 3, h // 4, w, h)]
            crops = [frame[y1:y2, x1:x2] for x1, y1, x2, y2 in tile_specs]
            results = model.predict(crops, imgsz=960, conf=0.45, iou=0.45, device=0, verbose=False)
            proposals = []
            for (ox1, oy1, _, _), result in zip(tile_specs, results):
                if result.boxes is None: continue
                for box in result.boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist(); x1 += ox1; x2 += ox1; y1 += oy1; y2 += oy1
                    bw, bh = x2 - x1, y2 - y1
                    if 4 <= bw <= 180 and 4 <= bh <= 180:
                        proposals.append(([x1, y1, x2, y2], float(box.conf.item())))
            kept = nms(proposals)
            for (x1, y1, x2, y2), score in kept:
                cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 0, 255), 2)
                cv2.putText(frame, f"candidate {score:.2f}", (int(x1), max(18, int(y1) - 4)), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 255), 1, cv2.LINE_AA)
            cv2.putText(frame, f"{video.stem[:5]} {seconds}s | candidates={len(kept)}", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2, cv2.LINE_AA)
            frame = cv2.resize(frame, (640, 360))
            tiles_per_frame.append(frame)
        capture.release()
    sheet = np.zeros((1080, 1920, 3), dtype=np.uint8)
    for i, tile in enumerate(tiles_per_frame): sheet[(i // 3) * 360:(i // 3 + 1) * 360, (i % 3) * 640:(i % 3 + 1) * 640] = tile
    cv2.imwrite(str(OUTPUT / "螺钉候选点切片检测审查.jpg"), sheet)
    print(OUTPUT / "螺钉候选点切片检测审查.jpg")


if __name__ == "__main__":
    main()
