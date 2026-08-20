from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

import requests


VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}


def append_state(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"), **record}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()


def latest_state(path: Path) -> dict[str, dict]:
    records: dict[str, dict] = {}
    if not path.is_file():
        return records
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("source"):
            records[str(item["source"])] = item
    return records


def probe_video(path: Path) -> dict | None:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height,r_frame_rate,duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=25, check=False,
    )
    if result.returncode != 0:
        return None
    try:
        streams = json.loads(result.stdout).get("streams", [])
        return streams[0] if streams else None
    except json.JSONDecodeError:
        return None


def proxy_path(source: Path, source_root: Path, proxy_root: Path) -> Path:
    relative = str(source.relative_to(source_root))
    key = hashlib.sha256(f"{relative}|{source.stat().st_size}|{source.stat().st_mtime_ns}".encode()).hexdigest()[:16]
    return proxy_root / f"{key}_{source.stem[:60]}.mp4"


def annotation_source(source: Path, source_root: Path, proxy_root: Path, stream: dict) -> Path:
    codec = str(stream.get("codec_name") or "")
    if source.suffix.lower() == ".mp4" and codec == "h264" and source.stat().st_size <= 2 * 1024**3:
        return source
    destination = proxy_path(source, source_root, proxy_root)
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    proxy_root.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-vf", "scale='min(1280,iw)':-2,fps=15", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(temporary)],
        check=True,
    )
    temporary.replace(destination)
    return destination


def create_task(session: requests.Session, api_url: str, project_id: int, name: str) -> int:
    response = session.post(f"{api_url}/api/tasks", json={"name": name[:255], "project_id": project_id}, timeout=30)
    response.raise_for_status()
    return int(response.json()["id"])


def task_has_data(session: requests.Session, api_url: str, task_id: int) -> bool:
    response = session.get(f"{api_url}/api/tasks/{task_id}/data/meta", timeout=30)
    if response.status_code == 400:
        return False
    response.raise_for_status()
    return int(response.json().get("size") or 0) > 0


def reconcile_existing_task(session: requests.Session, api_url: str, task_id: int) -> bool:
    if task_has_data(session, api_url, task_id):
        return True
    request_id = f"action=create&target=task&target_id={task_id}"
    response = session.get(f"{api_url}/api/requests/{requests.utils.quote(request_id, safe='')}", timeout=30)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    if response.json().get("status") in {"queued", "started"}:
        wait_until_processed(session, api_url, task_id)
    return task_has_data(session, api_url, task_id)


def upload_video(session: requests.Session, api_url: str, task_id: int, video: Path) -> None:
    with video.open("rb") as handle:
        response = session.post(
            f"{api_url}/api/tasks/{task_id}/data",
            data={"image_quality": "90", "use_zip_chunks": "true", "copy_data": "false"},
            files={"client_files[0]": (video.name, handle, "video/mp4")},
            timeout=(30, 7200),
        )
    response.raise_for_status()


def wait_until_processed(session: requests.Session, api_url: str, task_id: int, timeout: int = 7200) -> None:
    deadline = time.monotonic() + timeout
    request_id = f"action=create&target=task&target_id={task_id}"
    while time.monotonic() < deadline:
        response = session.get(f"{api_url}/api/requests/{requests.utils.quote(request_id, safe='')}", timeout=30)
        if response.status_code == 404:
            time.sleep(3)
            continue
        response.raise_for_status()
        status = response.json().get("status")
        if status == "finished":
            return
        if status == "failed":
            raise RuntimeError(response.json().get("message") or "CVAT 视频解码失败")
        time.sleep(5)
    raise TimeoutError(f"CVAT task {task_id} processing timeout")


def main() -> None:
    parser = argparse.ArgumentParser(description="Resumable one-video-per-task CVAT uploader")
    parser.add_argument("--source-root", type=Path, default=Path("/home/xjai/Desktop/sop xjai/视频数据"))
    parser.add_argument("--project-id", type=int, default=5)
    parser.add_argument("--organization", default="lan-team")
    parser.add_argument("--api-url", default="http://localhost:8081")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--proxy-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0, help="Maximum successful uploads in this run; 0 means all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = args.token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("CVAT team token is empty")
    session = requests.Session()
    session.headers.update({"Authorization": f"Token {token}", "X-Organization": args.organization})
    api_url = args.api_url.rstrip("/")
    project = session.get(f"{api_url}/api/projects/{args.project_id}", timeout=20)
    project.raise_for_status()
    if int(project.json().get("organization") or 0) <= 0:
        raise RuntimeError("Target CVAT project is not in a shared organization")

    videos = sorted(
        (path for path in args.source_root.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES),
        key=lambda path: (path.stat().st_size, str(path)),
    )
    previous = latest_state(args.state)
    submitted_signatures = {
        (int(item.get("bytes", -1)), Path(source).name): source
        for source, item in previous.items() if item.get("status") in {"submitted", "completed"}
    }
    uploaded = 0
    for index, source in enumerate(videos, 1):
        source_key = str(source)
        old = previous.get(source_key, {})
        if old.get("status") in {"submitted", "completed", "duplicate"}:
            continue
        base = {"source": source_key, "relative": str(source.relative_to(args.source_root)), "bytes": source.stat().st_size, "index": index, "total": len(videos)}
        signature = (source.stat().st_size, source.name)
        if signature in submitted_signatures and submitted_signatures[signature] != source_key:
            append_state(args.state, {**base, "status": "duplicate", "canonical_source": submitted_signatures[signature], "message": "同名同大小副本已提交，避免重复占用 CVAT 存储"})
            continue
        if source.stat().st_size == 0:
            append_state(args.state, {**base, "status": "invalid", "message": "空文件，未创建任务"})
            continue
        stream = probe_video(source)
        if stream is None:
            append_state(args.state, {**base, "status": "invalid", "message": "ffprobe 无法读取视频流"})
            continue
        if args.dry_run:
            append_state(args.state, {**base, "status": "ready", "stream": stream})
            continue
        task_id = old.get("task_id")
        try:
            if task_id and reconcile_existing_task(session, api_url, int(task_id)):
                append_state(args.state, {**base, "status": "submitted", "task_id": int(task_id), "message": "CVAT 已有完整视频数据，断点恢复时不重复上传"})
                submitted_signatures[signature] = source_key
                continue
            if not task_id:
                relative_name = str(source.relative_to(args.source_root)).replace("/", "｜")
                task_id = create_task(session, api_url, args.project_id, f"SOP-{index:04d}｜{relative_name}")
                append_state(args.state, {**base, "status": "created", "task_id": task_id})
            upload_path = annotation_source(source, args.source_root, args.proxy_root, stream)
            upload_video(session, api_url, int(task_id), upload_path)
            wait_until_processed(session, api_url, int(task_id))
            append_state(args.state, {**base, "status": "submitted", "task_id": int(task_id), "upload_path": str(upload_path), "upload_bytes": upload_path.stat().st_size})
            submitted_signatures[signature] = source_key
            uploaded += 1
        except Exception as exc:
            append_state(args.state, {**base, "status": "failed", "task_id": task_id, "message": str(exc)})
        if args.limit and uploaded >= args.limit:
            break
        free = shutil.disk_usage(args.proxy_root.parent if args.proxy_root.parent.exists() else args.source_root).free
        if free < 80 * 1024**3:
            append_state(args.state, {"source": "__batch__", "status": "paused", "message": "剩余空间低于 80 GiB，批处理已暂停"})
            break
    print(json.dumps({"total_discovered": len(videos), "uploaded_this_run": uploaded, "state": str(args.state)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
