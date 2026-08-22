from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from process_frontier_two_videos import (
    CLASS_ID,
    DATASET,
    DATA_DIR,
    MEDIA_DIR,
    PROFILE_PATH,
    ROOT,
    SNAPSHOT_ROOT,
    draw_panel,
    draw_tags,
    fit_canvas,
    nms,
    plausible_detection,
    split_for,
    yolo_line,
)


def refine(profile: dict, manifest_handle) -> dict:
    source = Path(profile["source"])
    final = MEDIA_DIR / profile["output"]
    temp_raw = MEDIA_DIR / f"{profile['id']}_refined_temp.mp4"
    temp_h264 = MEDIA_DIR / f"{profile['id']}_refined_h264.mp4"
    log_path = DATA_DIR / f"{profile['id']}_frame_annotations.jsonl"
    temp_log = DATA_DIR / f"{profile['id']}_frame_annotations.refined.tmp.jsonl"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    capture = cv2.VideoCapture(str(source))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30)
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = frames / fps
    writer = cv2.VideoWriter(str(temp_raw), cv2.VideoWriter_fourcc(*"mp4v"), fps, (1620, 720))
    snapshot_dir = SNAPSHOT_ROOT / profile["id"]
    snapshot_frames = {
        int(((float(step["start_s"]) + float(step["end_s"])) / 2) * fps): step["id"] for step in profile["steps"]
    }
    counts: Counter[str] = Counter()
    with temp_log.open("w", encoding="utf-8") as log_handle:
        for frame_index in range(frames):
            ok, frame = capture.read()
            if not ok:
                break
            record = records[min(frame_index, len(records) - 1)]
            detections = [item for item in record["detections"] if plausible_detection(frame, item)]
            detections = nms(detections, 0.62)
            record["detections"] = detections
            record["refinement"] = "大零件类别阈值+肤色排除+形状约束，2026-08-16"
            log_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            for item in detections:
                counts[item["label"]] += 1
            elapsed = frame_index / fps
            if frame_index % 15 == 0:
                split = split_for(elapsed, duration)
                stem = f"{profile['id']}_{frame_index:06d}"
                label_path = DATASET / f"labels/{split}/{stem}.txt"
                lines, annotations = [], []
                for item in detections:
                    if item["label"] not in CLASS_ID:
                        continue
                    lines.append(yolo_line(CLASS_ID[item["label"]], item["xyxy"], width, height))
                    annotations.append(
                        {
                            "class": item["label"],
                            "box": item["xyxy"],
                            "confidence": item["confidence"],
                            "source": item["source"],
                            "review_status": "pending_human_review",
                        }
                    )
                label_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
                manifest_handle.write(
                    json.dumps(
                        {
                            "image": f"images/{split}/{stem}.jpg",
                            "label": f"labels/{split}/{stem}.txt",
                            "video_id": profile["id"],
                            "frame": frame_index,
                            "time_s": round(elapsed, 3),
                            "split": split,
                            "annotations": annotations,
                            "overall_status": "pending_human_review",
                            "refinement": "大零件误检二次门控",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            annotated = draw_tags(frame, detections)
            left = fit_canvas(annotated)
            panel = draw_panel(profile, elapsed, duration)
            output = np.concatenate([left, panel], axis=1)
            writer.write(output)
            if frame_index in snapshot_frames:
                cv2.imwrite(str(snapshot_dir / f"{snapshot_frames[frame_index]}_{elapsed:.1f}秒.jpg"), output)
    capture.release()
    writer.release()
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(temp_raw), "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(temp_h264)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    temp_raw.unlink(missing_ok=True)
    os.replace(temp_h264, final)
    os.replace(temp_log, log_path)
    return {"id": profile["id"], "frames": frames, "counts": dict(counts), "output": str(final)}


def main() -> None:
    profiles = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    temp_manifest = DATASET / "manifest.refined.tmp.jsonl"
    with temp_manifest.open("w", encoding="utf-8") as manifest_handle:
        results = [refine(profile, manifest_handle) for profile in profiles]
    os.replace(temp_manifest, DATASET / "manifest.jsonl")
    catalog_path = DATA_DIR / "videos.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    result_map = {item["id"]: item for item in results}
    for video in catalog["videos"]:
        if video["id"] in result_map:
            video["yolo_detection_counts"] = result_map[video["id"]]["counts"]
            video["box_refinement"] = "大零件类别阈值+肤色排除+形状约束；顶部工作栏移至右侧"
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"status": "PASS", "results": results, "truth_policy": "全部标签仍待人工复核"}
    (ROOT / "qa/frontier_box_refinement_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
