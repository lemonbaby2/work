from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
FONT_CANDIDATES = (
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)
COLORS = (
    "#13b8a6", "#f0a83a", "#3687d9", "#e35d6a", "#9b6ad6", "#2aa86b",
    "#e57a2d", "#d64ea1", "#63a63f", "#7971d8", "#1f9bb8", "#c49b23",
    "#8a6b38", "#e26340", "#45a0de", "#ef6c91",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render real-frame previews from PCB pseudo-label manifests")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = next((candidate for candidate in FONT_CANDIDATES if candidate.exists()), None)
    return ImageFont.truetype(str(path), size) if path else ImageFont.load_default()


def evenly_spaced(records: list[dict], count: int) -> list[dict]:
    if len(records) <= count:
        return records
    return [records[round(index * (len(records) - 1) / (count - 1))] for index in range(count)]


def main() -> None:
    args = parse_args()
    dataset = args.dataset.resolve()
    records = [
        json.loads(line)
        for line in (dataset / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected = evenly_spaced([record for record in records if record["source_id"] == args.source_id], args.count)
    if not selected:
        raise RuntimeError(f"No records for source {args.source_id} in {dataset}")
    names = json.loads((dataset / "classes.json").read_text(encoding="utf-8"))["names"]
    name_to_id = {name: int(class_id) for class_id, name in names.items()}
    args.output.mkdir(parents=True, exist_ok=True)
    title_font, label_font = font(28), font(18)
    index = []
    for record in selected:
        image = Image.open(dataset / record["image"]).convert("RGB")
        draw = ImageDraw.Draw(image, "RGBA")
        for annotation in record["annotations"]:
            class_id = name_to_id[annotation["label"]]
            color = COLORS[class_id % len(COLORS)]
            x1, y1, x2, y2 = [int(round(value)) for value in annotation["xyxy"]]
            width = 4 if annotation["source"].startswith("固定") else 2
            draw.rectangle((x1, y1, x2, y2), outline=color, width=width)
            caption = f"{class_id:02d} {annotation['label']}"
            text_box = draw.textbbox((x1, y1), caption, font=label_font)
            text_height = text_box[3] - text_box[1] + 8
            text_top = max(0, y1 - text_height)
            draw.rectangle((x1, text_top, min(image.width, text_box[2] + 8), y1), fill="#071018dd")
            draw.text((x1 + 4, text_top + 2), caption, fill="#ffffff", font=label_font)
        header = f"{args.source_id}  {record['time_s']:.3f}s  frame {record['frame']}  {len(record['annotations'])} boxes"
        draw.rectangle((0, 0, image.width, 42), fill="#071018dd")
        draw.text((12, 5), header, fill="#ffffff", font=title_font)
        output = args.output / f"{args.source_id}_{record['frame']:06d}.jpg"
        image.save(output, quality=92)
        index.append({
            "frame": record["frame"],
            "time_s": record["time_s"],
            "preview": output.name,
            "box_count": len(record["annotations"]),
            "class_counts": {
                name: sum(1 for item in record["annotations"] if item["label"] == name)
                for name in sorted({item["label"] for item in record["annotations"]})
            },
            "truth_status": "automatic_candidates_pending_human_review",
        })
    (args.output / "preview_index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "previews": len(index)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
