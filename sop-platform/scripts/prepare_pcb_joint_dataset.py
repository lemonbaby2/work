from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "datasets"
SOURCE_0264 = DATASETS / "PCB插装0264_YOLOE_ROI增强_待人工复核"
SOURCE_0265 = DATASETS / "PCB插装0265_YOLOE_ROI增强_待人工复核"
OUTPUT = DATASETS / "PCB插装0264_0265联合_YOLOE_ROI增强_待人工复核"


def main() -> None:
    for source in (SOURCE_0264, SOURCE_0265):
        if not (source / "data.yaml").exists():
            raise RuntimeError(f"Missing prepared dataset: {source}")
    classes_0264 = json.loads((SOURCE_0264 / "classes.json").read_text(encoding="utf-8"))
    classes = json.loads((SOURCE_0265 / "classes.json").read_text(encoding="utf-8"))
    if classes_0264 != classes:
        raise RuntimeError("0264 and 0265 class mappings differ; refusing to mix label IDs")
    names = classes["names"]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    yaml = [
        f"path: {OUTPUT}",
        "train:",
        f"  - {SOURCE_0264 / 'images/train'}",
        f"  - {SOURCE_0265 / 'images/train'}",
        f"val: {SOURCE_0265 / 'images/val'}",
        f"test: {SOURCE_0265 / 'images/test'}",
        "names:",
    ]
    yaml.extend(f"  {index}: {name}" for index, name in names.items())
    (OUTPUT / "data.yaml").write_text("\n".join(yaml) + "\n", encoding="utf-8")
    (OUTPUT / "classes.json").write_text(json.dumps(classes, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "dataset": str(OUTPUT),
        "train_sources": [str(SOURCE_0264), str(SOURCE_0265)],
        "validation_source": "station4/1.mp4 and station4/2.mp4",
        "test_source": "station4/3.mp4 and station4/6.mp4",
        "split_policy": "Complete source videos are isolated by split; no adjacent-frame leakage.",
        "truth_boundary": "All source labels are teacher candidates pending human review.",
    }
    (OUTPUT / "dataset_stats.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
