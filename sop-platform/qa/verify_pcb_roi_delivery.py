from __future__ import annotations

import json
import math
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATASETS = {
    "video_0264": ROOT / "datasets/PCB插装0264_YOLOE_ROI增强_待人工复核",
    "video_0265": ROOT / "datasets/PCB插装0265_YOLOE_ROI增强_待人工复核",
}
REQUIRED_CLASSES = {
    "PCB板", "电容", "电感", "电阻", "电子元器件/物料候选",
    "电容物料区", "电感物料区", "混合物料区",
}


def result(name: str, passed: bool, detail: object) -> dict:
    return {"name": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def validate_dataset(video_id: str, dataset: Path) -> list[dict]:
    report = json.loads((dataset / "dataset_stats.json").read_text(encoding="utf-8"))
    classes = json.loads((dataset / "classes.json").read_text(encoding="utf-8"))["names"]
    class_count = len(classes)
    counts = report.get("class_counts", {})
    invalid = []
    label_files = list((dataset / "labels").rglob("*.txt"))
    for path in label_files:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            fields = line.split()
            try:
                class_id = int(fields[0])
                values = [float(value) for value in fields[1:]]
            except (IndexError, ValueError):
                invalid.append(f"{path.name}:{line_number}:parse")
                continue
            if len(values) != 4 or not 0 <= class_id < class_count or not all(math.isfinite(value) and 0 <= value <= 1 for value in values) or values[2] <= 0 or values[3] <= 0:
                invalid.append(f"{path.name}:{line_number}:range")
    source = next(item for item in report["sources"] if item["source_id"] == video_id)
    return [
        result(f"{video_id}增强数据存在", dataset.is_dir(), str(dataset)),
        result(f"{video_id}关键类别非空", not (missing := sorted(name for name in REQUIRED_CLASSES if int(counts.get(name, 0)) <= 0)), {"missing": missing, "counts": counts}),
        result(f"{video_id}YOLO标签有效", not invalid, {"files": len(label_files), "invalid_first_20": invalid[:20]}),
        result(f"{video_id}完整视频已解码", int(source["decoded_frames"]) > 10_000 and float(source["duration_s"]) > 600, source),
        result(f"{video_id}保持人工复核边界", "human review" in report.get("truth_boundary", "").lower(), report.get("truth_boundary")),
    ]


def validate_steps() -> list[dict]:
    catalog = json.loads((ROOT / "web/data/videos.json").read_text(encoding="utf-8"))
    output = []
    for video_id in DATASETS:
        video = next(item for item in catalog["videos"] if item["id"] == video_id)
        steps = video.get("steps", [])
        continuous = len(steps) == 4 and abs(float(steps[0]["start_s"])) < .001 and abs(float(steps[-1]["end_s"]) - float(video["duration_s"])) < .05
        continuous = continuous and all(abs(float(left["end_s"]) - float(right["start_s"])) < .001 for left, right in zip(steps, steps[1:]))
        complete = all(step.get("roi") and float(step.get("duration_s", float(step["end_s"]) - float(step["start_s"]))) > 0 for step in steps)
        output.append(result(f"{video_id}阶段连续", continuous and complete, steps))
    return output


def main() -> None:
    checks = []
    for video_id, dataset in DATASETS.items():
        if not dataset.is_dir():
            checks.append(result(f"{video_id}增强数据存在", False, str(dataset)))
            continue
        checks.extend(validate_dataset(video_id, dataset))
    checks.extend(validate_steps())
    report = {
        "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "overall": "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL",
        "checks": checks,
        "truth_boundary": "Automated structural QA cannot confirm semantic ground truth. Every NG, rare class and process boundary still requires human review.",
    }
    output = ROOT / "qa/pcb_roi_delivery_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["overall"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
