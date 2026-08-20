from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, timeout: int = 12) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def parse_modes(text: str) -> list[dict[str, object]]:
    modes = []
    pixel_format = ""
    width = height = 0
    for raw in text.splitlines():
        line = raw.strip()
        format_match = re.match(r"\[\d+\]: '([^']+)'", line)
        size_match = re.match(r"Size: Discrete (\d+)x(\d+)", line)
        fps_match = re.search(r"\((\d+(?:\.\d+)?) fps\)", line)
        if format_match:
            pixel_format = format_match.group(1)
        elif size_match:
            width, height = map(int, size_match.groups())
        elif fps_match and pixel_format and width and height:
            modes.append({"pixel_format": pixel_format, "width": width, "height": height, "fps": float(fps_match.group(1))})
    return modes


def parse_controls(text: str) -> list[dict[str, str]]:
    controls = []
    for raw in text.splitlines():
        line = raw.strip()
        match = re.match(r"([a-zA-Z0-9_]+)\s+0x[0-9a-f]+\s+\(([^)]+)\)\s+:\s+(.*)", line)
        if match:
            controls.append({"name": match.group(1), "type": match.group(2), "details": match.group(3)})
    return controls


def test_mode(device: str, mode: dict[str, object], frames: int) -> dict[str, object]:
    spec = f"width={mode['width']},height={mode['height']},pixelformat={mode['pixel_format']}"
    started = time.perf_counter()
    result = run(
        "v4l2-ctl", "-d", device, f"--set-fmt-video={spec}", f"--set-parm={int(float(mode['fps']))}",
        "--stream-mmap=4", f"--stream-count={frames}", "--stream-to=/dev/null", timeout=20,
    )
    elapsed = time.perf_counter() - started
    return {**mode, "ok": result.returncode == 0, "frames": frames, "elapsed_s": round(elapsed, 3), "effective_fps": round(frames / elapsed, 2) if elapsed else None, "error": result.stderr.strip()[-500:] if result.returncode else None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe UVC camera modes and controls")
    parser.add_argument("--device", default="/dev/v4l/by-id/usb-Insta360_Insta360_Link_2C-video-index0")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--frames", type=int, default=30)
    args = parser.parse_args()
    device = str(Path(args.device).resolve())
    all_info = run("v4l2-ctl", "-d", device, "--all").stdout
    formats = run("v4l2-ctl", "-d", device, "--list-formats-ext").stdout
    controls_text = run("v4l2-ctl", "-d", device, "--list-ctrls-menus").stdout
    modes = parse_modes(formats)
    selected = [mode for mode in modes if mode["fps"] == 30 and (mode["width"], mode["height"]) in {(3840, 2160), (1920, 1080), (1280, 720)}]
    tests = [test_mode(device, mode, args.frames) for mode in selected] if args.test else []
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "device": device,
        "stable_path": args.device,
        "identity": next((line.strip() for line in all_info.splitlines() if "Card type" in line), "Insta360 Link 2C"),
        "modes": modes,
        "controls": parse_controls(controls_text),
        "tested_modes": tests,
        "recommended_sop_mode": {"pixel_format": "MJPG", "width": 1920, "height": 1080, "fps": 30, "reason": "低延迟和小目标细节平衡；4K用于离线采集或小目标特写，不建议四路同时解码。"},
        "notes": ["video-index1是UVC元数据节点，不可作为独立摄像头", "Link 2C为固定镜头机型，驱动暴露的pan/tilt数值不作为工业标定依据", "固定工位建议关闭持续自动对焦并锁定白平衡/曝光，参数需在现场补光确定后保存"],
    }
    output = ROOT / "qa" / "insta360_link2c_capabilities.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
