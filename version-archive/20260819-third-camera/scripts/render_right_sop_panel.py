from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "web/data/videos.json"
WEB_ROOT = ROOT / "web"
MEDIA_DIR = WEB_ROOT / "media"
PANEL_WIDTH = 340


def font(size: int, bold: bool = False):
    candidates = [
        Path(r"C:\Windows\Fonts\msyhbd.ttc") if bold else Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


FONT_12 = font(12)
FONT_14 = font(14)
FONT_16 = font(16)
FONT_18 = font(18, True)
FONT_24 = font(24, True)
FONT_34 = font(34, True)


def current_step(steps: list[dict], elapsed: float) -> tuple[dict, int]:
    step = next((item for item in steps if float(item["start_s"]) <= elapsed < float(item["end_s"])), steps[-1])
    completed = sum(elapsed >= float(item["end_s"]) for item in steps)
    return step, completed


def draw_panel(video: dict, elapsed: float, total: float) -> np.ndarray:
    steps = video["steps"]
    active, completed = current_step(steps, elapsed)
    panel = Image.new("RGB", (PANEL_WIDTH, 720), "#0b1721")
    draw = ImageDraw.Draw(panel)
    draw.rectangle((0, 0, 5, 720), fill="#2fc7a5")
    draw.text((26, 22), "SOP 步骤看板", font=FONT_24, fill="white")
    draw.text((26, 57), video["display_name"], font=FONT_12, fill="#9fb3bf")
    draw.text((26, 82), f"{elapsed:05.1f}s / {total:.1f}s", font=FONT_14, fill="#d8e4ea")
    draw.text((250, 77), f"{completed}/6", font=FONT_24, fill="#5adbbd")
    progress = min(1.0, elapsed / max(total, 0.1))
    draw.rounded_rectangle((26, 111, 314, 119), radius=4, fill="#263d49")
    draw.rounded_rectangle((26, 111, 26 + int(288 * progress), 119), radius=4, fill="#35c7a7")

    y = 140
    for index, step in enumerate(steps):
        is_done = index < completed
        is_active = step["id"] == active["id"] and completed < len(steps)
        if is_done:
            bg, border, accent, marker = "#12392f", "#247b66", "#5be0bd", "完"
            state_text = "已完成"
        elif is_active:
            bg, border, accent, marker = "#3b2b16", "#e0a43e", "#ffc766", "当"
            state_text = "当前步骤"
        else:
            bg, border, accent, marker = "#142632", "#29414e", "#78909c", str(index + 1)
            state_text = "待执行"
        draw.rounded_rectangle((20, y, 320, y + 69), radius=9, fill=bg, outline=border, width=2 if is_active else 1)
        draw.ellipse((31, y + 17, 65, y + 51), fill=border)
        marker_box = draw.textbbox((0, 0), marker, font=FONT_16)
        marker_width = marker_box[2] - marker_box[0]
        draw.text((48 - marker_width / 2, y + 23), marker, font=FONT_16, fill="white")
        draw.text((78, y + 12), f"{step['id']}  {step['label']}", font=FONT_16, fill="white" if is_active else "#d9e5ea")
        draw.text((78, y + 42), f"{state_text}  ·  {float(step['start_s']):.0f}–{float(step['end_s']):.0f}秒", font=FONT_12, fill=accent)
        y += 78

    draw.rounded_rectangle((20, 618, 320, 698), radius=10, fill="#2b1c17", outline="#8f4b32", width=1)
    draw.text((34, 631), "生产放行", font=FONT_12, fill="#d4a189")
    draw.text((34, 650), "HOLD", font=FONT_34, fill="#ff9d72")
    draw.text((143, 652), "等待扭矩与 MES 回执", font=FONT_14, fill="#ffd4c2")
    draw.text((143, 678), "视觉通过 ≠ 最终放行", font=FONT_12, fill="#bd8e7a")
    return cv2.cvtColor(np.asarray(panel), cv2.COLOR_RGB2BGR)


def render_video(video: dict) -> dict:
    source = WEB_ROOT / video["enhanced_video"]
    output_name = f"{Path(video['enhanced_video']).stem}_右侧SOP步骤面板版.mp4"
    output = MEDIA_DIR / output_name
    temp = MEDIA_DIR / f"{video['id']}_right_panel_temp.mp4"
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"无法读取：{source}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30)
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = frames / fps
    if height != 720:
        raise RuntimeError(f"当前脚本要求720P输入，实际为{width}×{height}")
    writer = cv2.VideoWriter(str(temp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width + PANEL_WIDTH, height))
    for index in range(frames):
        ok, frame = capture.read()
        if not ok:
            break
        panel = draw_panel(video, index / fps, total)
        canvas = np.concatenate([frame, panel], axis=1)
        writer.write(canvas)
    writer.release()
    capture.release()
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(temp), "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(output)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    temp.unlink(missing_ok=True)
    video["presentation_video"] = f"media/{output_name}"
    return {"id": video["id"], "source": str(source), "output": str(output), "frames": frames, "fps": fps, "resolution": f"{width + PANEL_WIDTH}×{height}", "duration_s": round(total, 2)}


def main() -> None:
    started = time.time()
    catalog = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    results = [render_video(video) for video in catalog["videos"]]
    catalog["presentation"] = {
        "layout": "左侧1280×720检测画面 + 右侧340×720 SOP六步看板",
        "status_colors": {"已完成": "绿色", "当前步骤": "橙色", "待执行": "灰色", "生产放行": "HOLD橙红色"},
        "videos": results,
    }
    DATA_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "processing_seconds": round(time.time() - started, 1), "videos": results}
    (ROOT / "qa/right_panel_video_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
