from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]


def request_json(url: str, method: str = "GET") -> dict:
    request = Request(url, data=b"" if method == "POST" else None, method=method)
    with build_opener(ProxyHandler({})).open(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * ratio)))
    return ordered[position]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure latest-frame camera pipeline latency")
    parser.add_argument("--url", default="http://127.0.0.1:8096")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--seconds", type=int, default=15)
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "qa/camera_latency_report.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base = args.url.rstrip("/")
    if args.start:
        request_json(f"{base}/api/camera/start?camera={args.camera}", "POST")
    samples = []
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        status = request_json(f"{base}/api/camera/status?camera={args.camera}")
        if status.get("running") and status.get("frame", 0) > 0:
            samples.append(status)
        time.sleep(1)
    if args.stop:
        request_json(f"{base}/api/camera/stop?camera={args.camera}", "POST")
    if not samples:
        raise RuntimeError("No running camera samples were collected")
    pipeline = [float(item.get("pipeline_ms") or 0) for item in samples]
    inference = [float(item.get("inference_ms") or 0) for item in samples]
    output_fps = [float(item.get("output_fps") or 0) for item in samples]
    capture_fps = [float(item.get("capture_fps_actual") or 0) for item in samples]
    gpu_wait = [float(item.get("gpu_wait_ms") or 0) for item in samples]
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "endpoint": base,
        "camera": args.camera,
        "source": samples[-1].get("source"),
        "model": samples[-1].get("model"),
        "device": samples[-1].get("device"),
        "samples": len(samples),
        "buffering_strategy": samples[-1].get("buffering_strategy"),
        "queue_capacity": samples[-1].get("queue_capacity"),
        "queue_depth_max": max(int(item.get("queue_depth") or 0) for item in samples),
        "pipeline_ms_mean": round(statistics.mean(pipeline), 2),
        "pipeline_ms_p95": round(percentile(pipeline, .95), 2),
        "inference_ms_mean": round(statistics.mean(inference), 2),
        "gpu_wait_ms_p95": round(percentile(gpu_wait, .95), 2),
        "capture_fps_mean": round(statistics.mean(capture_fps), 2),
        "output_fps_mean": round(statistics.mean(output_fps), 2),
        "dropped_frames": int(samples[-1].get("dropped_frames") or 0),
        "pass": samples[-1].get("buffering_strategy") == "latest-frame-mailbox" and max(int(item.get("queue_depth") or 0) for item in samples) <= 1,
        "note": "Dropped stale frames are intentional. The pass gate verifies bounded latency structure, not model accuracy.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
