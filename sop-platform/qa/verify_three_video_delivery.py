from __future__ import annotations

import json
import time
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]


def item(name: str, passed: bool, detail: object) -> dict:
    return {"name": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def main() -> None:
    catalog = json.loads((ROOT / "web/data/videos.json").read_text(encoding="utf-8"))
    results = []
    total_video_frames = 0
    total_presentation_frames = 0
    for video in catalog["videos"]:
        path = ROOT / "web" / video["enhanced_video"]
        capture = cv2.VideoCapture(str(path))
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        opened = capture.isOpened()
        capture.release()
        total_video_frames += frames
        results.append(item(f"{video['id']}增强视频", opened and frames == int(video["frames"]) and 29.9 <= fps <= 30.1, {"frames": frames, "fps": fps, "path": str(path)}))
        presentation_path = ROOT / "web" / video["presentation_video"]
        presentation_capture = cv2.VideoCapture(str(presentation_path))
        presentation_frames = int(presentation_capture.get(cv2.CAP_PROP_FRAME_COUNT))
        presentation_fps = float(presentation_capture.get(cv2.CAP_PROP_FPS))
        presentation_width = int(presentation_capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        presentation_height = int(presentation_capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        presentation_opened = presentation_capture.isOpened()
        presentation_capture.release()
        total_presentation_frames += presentation_frames
        results.append(item(
            f"{video['id']}右侧SOP步骤面板视频",
            presentation_opened
            and presentation_frames == int(video["frames"])
            and 29.9 <= presentation_fps <= 30.1
            and (presentation_width, presentation_height) == (1620, 720),
            {"frames": presentation_frames, "fps": presentation_fps, "resolution": f"{presentation_width}×{presentation_height}", "path": str(presentation_path)},
        ))
        candidate_path = ROOT / "web/data" / f"{video['id']}_fastener_candidates.jsonl"
        candidate_lines = candidate_path.read_text(encoding="utf-8").splitlines()
        results.append(item(f"{video['id']}紧固点逐帧记录", len(candidate_lines) == int(video["frames"]), len(candidate_lines)))

    dataset = ROOT / "datasets/三视频多物体预标注_待人工复核"
    images = sorted((dataset / "images/pending_review").glob("*.jpg"))
    labels = sorted((dataset / "labels/pending_review").glob("*.txt"))
    manifest = (dataset / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    valid_labels = True
    for path in labels:
        for line in path.read_text(encoding="utf-8").splitlines():
            values = line.split()
            valid_labels &= len(values) == 5 and 0 <= int(values[0]) <= 6 and all(0 <= float(value) <= 1 for value in values[1:])
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    js = (ROOT / "web/app.js").read_text(encoding="utf-8")
    results.extend([
        item("三视频汇总", catalog["totals"]["videos"] == 3 and catalog["totals"]["frames"] == 7007 and catalog["totals"]["steps"] == 18, catalog["totals"]),
        item("增强视频总帧数", total_video_frames == 7007, total_video_frames),
        item("右侧SOP步骤视频总帧数", total_presentation_frames == 7007, total_presentation_frames),
        item("预标注图片与标签", len(images) == len(labels) == len(manifest) == 469, {"images": len(images), "labels": len(labels), "manifest": len(manifest)}),
        item("YOLO标签格式", bool(labels) and valid_labels, "class 0-6，坐标0-1"),
        item("智能决策页面", all(text in html for text in ["智能决策中心", "决策链路", "为什么这样判断", "人机协同处置"]), "页面模块齐全"),
        item("三视频前端逻辑", all(text in js for text in ["/api/videos", "/api/decision", "selectVideo", "presentation_video"]), "网页优先播放右侧SOP步骤面板版"),
        item("真实性边界", "待人工复核" in html and "HOLD" in html, "候选框不冒充真值"),
    ])
    report = {"verified_at": time.strftime("%Y-%m-%d %H:%M:%S"), "overall": "PASS" if all(row["status"] == "PASS" for row in results) else "FAIL", "results": results}
    (ROOT / "qa/three_video_validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["overall"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
