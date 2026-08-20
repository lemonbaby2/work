from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "web" / "data" / "videos.json"
DATASET = ROOT / "datasets" / "新增两视频_YOLOE26_SAHI细粒度预标注_待人工复核"
OUT = ROOT / "qa" / "five_video_frontier_delivery_report.json"


def check(condition: bool, name: str, detail: object, checks: list[dict]) -> None:
    checks.append({"name": name, "status": "PASS" if condition else "FAIL", "detail": detail})


def video_meta(path: Path) -> dict:
    cap = cv2.VideoCapture(str(path))
    result = {
        "opened": cap.isOpened(),
        "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        "fps": round(float(cap.get(cv2.CAP_PROP_FPS)), 3),
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fourcc": int(cap.get(cv2.CAP_PROP_FOURCC)).to_bytes(4, "little").decode("latin1"),
    }
    cap.release()
    return result


def main() -> None:
    checks: list[dict] = []
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    totals = catalog["totals"]
    check(totals == {"videos": 5, "frames": 15163, "duration_s": 505.43, "steps": 30}, "五视频目录汇总", totals, checks)

    expected = {
        "仪表板SOP_视频四_40b5_YOLOE26_SAHI_无遮挡版.mp4": (1620, 1620, 720),
        "仪表板SOP_视频五_ecc57_YOLOE26_SAHI_无遮挡版.mp4": (6536, 1620, 720),
    }
    for name, (frames, width, height) in expected.items():
        path = ROOT / "web" / "media" / name
        meta = video_meta(path)
        check(path.exists() and meta["opened"] and meta["frames"] == frames and meta["width"] == width and meta["height"] == height and abs(meta["fps"] - 30) < .02, f"成片验收：{name}", meta, checks)

    images = {split: list((DATASET / "images" / split).glob("*.jpg")) for split in ("train", "val", "test")}
    labels = {split: list((DATASET / "labels" / split).glob("*.txt")) for split in ("train", "val", "test")}
    split_counts = {split: len(images[split]) for split in images}
    check(split_counts == {"train": 382, "val": 81, "test": 81}, "数据集划分", split_counts, checks)
    check(all(len(images[s]) == len(labels[s]) for s in images), "图像与YOLO标签一一对应", {s: [len(images[s]), len(labels[s])] for s in images}, checks)

    sample_files = sum((labels[s][:5] + labels[s][-5:] for s in labels), [])
    valid_rows = True
    invalid_detail = ""
    for path in sample_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            values = line.split()
            if len(values) != 5 or not (0 <= int(values[0]) < 12) or any(not (0 <= float(value) <= 1) for value in values[1:]):
                valid_rows = False
                invalid_detail = f"{path.name}: {line}"
                break
    check(valid_rows, "YOLO标签抽样格式与归一化", invalid_detail or f"抽检{len(sample_files)}份标签", checks)

    model_files = [
        ROOT / "models" / "yoloe-26s-seg.pt",
        ROOT / "models" / "yolo26n_两视频小目标蒸馏_待人工验收.pt",
        ROOT / "models" / "yolo26n_两视频小目标蒸馏_待人工验收.onnx",
    ]
    check(all(path.exists() and path.stat().st_size > 1_000_000 for path in model_files), "模型文件", {p.name: p.stat().st_size if p.exists() else 0 for p in model_files}, checks)

    visuals = sorted((ROOT / "analysis" / "新增两视频_前沿算法可视化").glob("*.*"))
    visual_images = [path for path in visuals if path.suffix.lower() in {".jpg", ".png"}]
    check(len(visual_images) == 10 and all(path.stat().st_size > 30_000 for path in visual_images), "十张中文可视化", [p.name for p in visual_images], checks)

    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    check("五视频现场监控" in html and "5段视频已跑通" in html, "网页五视频文案", "已更新", checks)
    check(".topbar{position:relative!important" in css and "aspect-ratio:9/4!important" in css, "顶部工作栏不遮挡且完整视频比例", "工作栏正常流 + 9:4", checks)
    check("一\", \"二\", \"三\", \"四\", \"五" in js and "presentation_video" in js, "网页五视频切换逻辑", "已更新", checks)

    truth_text = (ROOT / "docs" / "08_新增两视频与前沿SOP架构说明.md").read_text(encoding="utf-8")
    check("不得直接量产下发" in truth_text and "缺任何关键证据一律HOLD" in truth_text, "真实性与放行边界", "文档已明确", checks)

    http_status = None
    try:
        request = urllib.request.Request("http://127.0.0.1:8096/api/videos")
        with urllib.request.urlopen(request, timeout=3) as response:
            online = json.loads(response.read().decode("utf-8"))
            http_status = response.status
        check(http_status == 200 and online["totals"]["videos"] == 5, "在线API", {"status": http_status, "videos": online["totals"]["videos"]}, checks)
    except Exception as exc:
        check(False, "在线API", str(exc), checks)

    report = {
        "overall": "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL",
        "checks": checks,
        "truth_boundary": "自动预标注和教师一致性指标不等于人工真值量产精度；缺扭矩/MES证据保持HOLD。",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["overall"] == "PASS" else 1)


if __name__ == "__main__":
    main()
