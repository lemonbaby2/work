from __future__ import annotations

import json
import time
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]


def check(name: str, condition: bool, detail: object) -> dict:
    return {"name": name, "status": "PASS" if condition else "FAIL", "detail": detail}


def main() -> None:
    summary = json.loads((ROOT / "web/data/dashboard.json").read_text(encoding="utf-8"))
    recipe = json.loads((ROOT / "config/sop_recipe.json").read_text(encoding="utf-8"))
    frame_path = ROOT / "web/data/frame_annotations.jsonl"
    lines = frame_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    last = json.loads(lines[-1])
    video_path = ROOT / "web/media/仪表板SOP_YOLOv8逐帧识别演示.mp4"
    capture = cv2.VideoCapture(str(video_path))
    video_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    video_fps = float(capture.get(cv2.CAP_PROP_FPS))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    opened = capture.isOpened()
    capture.release()
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")

    results = [
        check("演示视频可读取", opened, str(video_path)),
        check("视频帧数与摘要一致", video_frames == summary["frames"] == len(lines), {"video": video_frames, "jsonl": len(lines)}),
        check("视频分辨率正确", (width, height) == (1280, 720), f"{width}×{height}"),
        check("视频帧率正确", 29.9 <= video_fps <= 30.1, video_fps),
        check("逐帧编号连续", first["frame"] == 0 and last["frame"] == len(lines) - 1, {"first": first["frame"], "last": last["frame"]}),
        check("每帧含零件标签", all(len(json.loads(line)["parts"]) >= 1 for line in lines), f"共{len(lines)}帧"),
        check("SOP配方完整", len(recipe["steps"]) == 6 and len(recipe["part_regions"]) == 4, {"steps": len(recipe["steps"]), "parts": len(recipe["part_regions"])}),
        check("模型文件存在", (ROOT / "models/yolov8s-worldv2.pt").stat().st_size > 20_000_000, "yolov8s-worldv2.pt"),
        check("网页七大模块完整", all(text in html for text in ["老板总览", "现场监控", "SOP 编排", "数据标注", "训练与部署", "MES 对接", "建设方案"]), "7个模块"),
        check("生产边界明确", "HOLD 待确认" in html and summary["production_release"] == "HOLD", "HOLD"),
    ]
    report = {
        "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "environment": "D:/Anaconda/envs/dl/python.exe",
        "overall": "PASS" if all(item["status"] == "PASS" for item in results) else "FAIL",
        "results": results,
    }
    output = ROOT / "qa/validation_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["overall"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
