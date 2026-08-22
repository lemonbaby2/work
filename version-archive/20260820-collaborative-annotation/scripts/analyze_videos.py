from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


SOURCE = Path(r"E:\宁波项目\测试验视频")
OUTPUT = Path(r"E:\宁波项目\SOP分析平台_老板汇报版\analysis\contact_sheets")


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\simhei.ttf")):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    title_font = load_font(22)
    label_font = load_font(16)
    metadata: list[dict[str, object]] = []

    for video in sorted(SOURCE.glob("*.mp4")):
        capture = cv2.VideoCapture(str(video))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = frame_count / fps if fps else 0
        indices = np.linspace(0, max(frame_count - 1, 0), 9, dtype=int) if frame_count else []
        tiles: list[Image.Image] = []

        for index in indices:
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
            ok, frame = capture.read()
            if not ok:
                continue
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(frame)
            image.thumbnail((480, 270))
            tile = Image.new("RGB", (480, 300), "#0b1220")
            tile.paste(image, ((480 - image.width) // 2, 0))
            drawer = ImageDraw.Draw(tile)
            drawer.text(
                (10, 274),
                f"{index / fps:.1f} 秒  |  第 {index} 帧",
                font=label_font,
                fill="white",
            )
            tiles.append(tile)

        sheet = Image.new("RGB", (1440, 950), "#e8edf3")
        drawer = ImageDraw.Draw(sheet)
        drawer.text(
            (25, 14),
            f"{video.name}｜{width}×{height}｜{fps:.1f} FPS｜{duration:.1f} 秒",
            font=title_font,
            fill="#111827",
        )
        for i, tile in enumerate(tiles):
            sheet.paste(tile, ((i % 3) * 480, 50 + (i // 3) * 300))

        target = OUTPUT / f"{video.stem}_关键帧.jpg"
        sheet.save(target, quality=90)
        metadata.append(
            {
                "file": str(video),
                "frames": frame_count,
                "fps": round(fps, 3),
                "width": width,
                "height": height,
                "duration_s": round(duration, 2),
                "contact_sheet": str(target),
            }
        )
        capture.release()

    (OUTPUT / "video_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
