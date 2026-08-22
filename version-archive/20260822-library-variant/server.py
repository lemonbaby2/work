from __future__ import annotations

import json
import base64
import hashlib
import importlib.util
import math
import mimetypes
import os
import shutil
import socket
import subprocess
import threading
import time
from bisect import bisect_left
from shutil import which
from urllib.request import urlopen
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DATA_ROOT = WEB_ROOT / "data"
RUNTIME_ROOT = ROOT / "runtime"
RECIPE_PATH = ROOT / "config" / "sop_recipe.json"
ALGORITHM_COMPARISON_PATH = ROOT / "config" / "algorithm_comparison.json"
PCB_RULES_PATH = ROOT / "config" / "pcb_workstation_rules.json"
WORKFLOW_RULES_PATH = ROOT / "config" / "sop_workflow_rules.json"
NETWORK_CAMERA_PATH = ROOT / "config" / "network_cameras.json"
FRAME_CACHE: dict[str, list[dict]] = {}
DESKTOP_ROOT = Path(os.getenv("SOP_DESKTOP_DIR", "/home/xjai/Desktop/sop xjai"))
EVIDENCE_ROOT = DESKTOP_ROOT / "摄像头证据"
ANNOTATION_IMAGE_ROOT = DESKTOP_ROOT / "标注图片"
DEFAULT_CAMERA_SOURCES = {
    0: "/dev/v4l/by-id/usb-Jieli_Technology_USB_Composite_Device-video-index0",
    1: "/dev/v4l/by-id/usb-webcamvendor_webcamproduct_00000000-video-index0",
}

# 原始录像保留在桌面素材目录；平台只登记索引，导入后通过受控媒体路由播放，
# 避免把 DJI 的十几 GB 文件重复复制到 web/media。
VIDEO_LIBRARY_ROOTS = [
    Path("/home/xjai/Desktop/sop xjai/视频数据/宁波SoP项目"),
]
VIDEO_LIBRARY_CACHE = RUNTIME_ROOT / "video_library.json"
VIDEO_LIBRARY_IMPORTS = RUNTIME_ROOT / "video_library_imports.jsonl"
# 用户要求将项目目录下全部 MP4 纳入标注导航；监控导出的 MKV 仍保留在原目录，
# 不在网页下拉框中展开上千个碎片文件。
VIDEO_EXTENSIONS = {".mp4"}
VIDEO_CACHE_ROOT = WEB_ROOT / "media" / "imported"
LIBRARY_AI_STATUS_PATH = RUNTIME_ROOT / "library_ai_status.json"
LIBRARY_AI_RECORDS_ROOT = RUNTIME_ROOT / "library_ai_annotations"
ANNOTATION_DELETIONS_PATH = RUNTIME_ROOT / "annotation_deletions.jsonl"
ANNOTATION_INTERPOLATIONS_PATH = RUNTIME_ROOT / "annotation_interpolations.jsonl"
ANNOTATION_TRACKS_PATH = RUNTIME_ROOT / "annotation_tracks.jsonl"
LIBRARY_AI_LOCK = threading.Lock()
LIBRARY_AI_MODEL = None
LIBRARY_AI_MODEL_PATH: str | None = None


def _redact_rtsp_url(value: str) -> str:
    """Remove credentials before an RTSP URL is returned in an error message."""
    try:
        parsed = urlparse(value)
        if parsed.username is None and parsed.password is None:
            return value
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return parsed._replace(netloc=host).geturl()
    except ValueError:
        return value


def _rtsp_endpoint(value: str) -> tuple[str, int] | None:
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme not in {"rtsp", "rtsps"} or not parsed.hostname:
        return None
    return parsed.hostname, parsed.port or (322 if parsed.scheme == "rtsps" else 554)


def _tcp_endpoint_open(host: str, port: int, timeout: float = 2.0) -> tuple[bool, str | None]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, None
    except OSError as exc:
        return False, str(exc)


def network_camera_profiles() -> list[dict]:
    if not NETWORK_CAMERA_PATH.exists():
        return []
    try:
        return json.loads(NETWORK_CAMERA_PATH.read_text(encoding="utf-8")).get("cameras", [])
    except json.JSONDecodeError:
        return []


def neighbor_mac(ip_address: str) -> str | None:
    try:
        result = subprocess.run(["ip", "neigh", "show", ip_address], capture_output=True, text=True, timeout=0.8, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    fields = result.stdout.split()
    if "lladdr" not in fields:
        return None
    index = fields.index("lladdr") + 1
    return fields[index].lower() if index < len(fields) else None


def _udev_properties(device: str) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["udevadm", "info", "--query=property", "--name", device],
            capture_output=True,
            text=True,
            timeout=0.8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    properties = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    return properties


def device_inventory() -> dict[str, object]:
    """返回 USB/UVC 设备身份和主机网络信息。

    UVC 摄像头没有独立的以太网 MAC/IP；页面明确区分 USB 身份和主机网络，
    避免把主机地址误标成摄像头地址。
    """
    videos = []
    by_id = sorted(Path("/dev/v4l/by-id").glob("*-video-index0"))
    stable_by_target = {str(path.resolve()): str(path) for path in by_id if path.exists()}
    for device in sorted(Path("/dev").glob("video*")):
        if not device.name[5:].isdigit():
            continue
        number = device.name[5:]
        properties = _udev_properties(str(device))
        sysfs_name = Path(f"/sys/class/video4linux/video{number}/name")
        name = sysfs_name.read_text(encoding="utf-8", errors="replace").strip() if sysfs_name.exists() else device.name
        videos.append({
            "device": str(device),
            "stable_path": stable_by_target.get(str(device.resolve())),
            "name": name,
            "vendor": properties.get("ID_VENDOR_FROM_DATABASE") or properties.get("ID_VENDOR", "未知"),
            "model": properties.get("ID_MODEL_FROM_DATABASE") or properties.get("ID_MODEL", "未知"),
            "serial": properties.get("ID_SERIAL_SHORT") or properties.get("ID_SERIAL"),
            "usb_vendor_id": properties.get("ID_VENDOR_ID"),
            "usb_product_id": properties.get("ID_MODEL_ID"),
            "usb_path": properties.get("ID_PATH"),
            "video_capture": number in {"0", "2"},
            "network_address": None,
        })
    serials = []
    for device in sorted(Path("/dev").glob("ttyACM*")) + sorted(Path("/dev").glob("ttyUSB*")):
        properties = _udev_properties(str(device))
        serials.append({
            "device": str(device),
            "vendor": properties.get("ID_VENDOR_FROM_DATABASE") or properties.get("ID_VENDOR", "未知"),
            "model": properties.get("ID_MODEL", "未知"),
            "serial": properties.get("ID_SERIAL_SHORT") or properties.get("ID_SERIAL"),
            "usb_path": properties.get("ID_PATH"),
            "note": "串口控制/身份通道；不是网络接口，没有独立 MAC/IP",
        })
    interfaces = []
    try:
        result = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=0.8, check=False)
        for item in json.loads(result.stdout or "[]"):
            addresses = [address.get("local") for address in item.get("addr_info", []) if address.get("local")]
            interfaces.append({"name": item.get("ifname"), "mac": item.get("address"), "addresses": addresses, "state": item.get("operstate")})
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    network_cameras = network_camera_profiles()
    for camera in network_cameras:
        observed_mac = neighbor_mac(str(camera.get("ip", "")))
        camera["reachable"] = False
        try:
            with socket.create_connection((str(camera["ip"]), 554), timeout=2.0):
                camera["reachable"] = True
        except (OSError, KeyError, TypeError):
            pass
        camera["observed_mac"] = observed_mac
        camera["mac_matches"] = bool(observed_mac and observed_mac == str(camera.get("mac", "")).lower())
        camera["observed_via"] = "局域网邻居表 + 554端口" if camera["reachable"] else ("局域网邻居表" if observed_mac else "未验证")
    return {
        "ok": True,
        "host": socket.gethostname(),
        "videos": videos,
        "serials": serials,
        "network": interfaces,
        "network_cameras": network_cameras,
        "camera_network_note": "USB/UVC 摄像头没有独立网络地址；网络摄像头3已登记 IP 192.168.1.135 和 MAC c4:3c:b0:be:40:e8。推荐在路由器中按 MAC 建立 DHCP 静态租约固定 IP。",
        "desktop_root": str(DESKTOP_ROOT),
    }


def primary_lan_address() -> str:
    for interface in device_inventory().get("network", []):
        if interface.get("name") == "lo":
            continue
        for address in interface.get("addresses", []):
            if "." in str(address):
                return str(address)
    return "127.0.0.1"


def save_data_url(data_url: str, root: Path, stem: str) -> Path:
    header, separator, encoded = str(data_url).partition(",")
    if separator == "" or not header.startswith("data:image/") or ";base64" not in header:
        raise ValueError("证据图片必须是 base64 图片")
    raw = base64.b64decode(encoded, validate=True)
    if not raw or len(raw) > 10 * 1024 * 1024:
        raise ValueError("证据图片为空或超过10MB")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{stem}.jpg"
    path.write_bytes(raw)
    return path


def _video_id(path: Path) -> str:
    digest = hashlib.sha1(str(path).encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"library_{digest}"


def _probe_video(path: Path) -> dict[str, object]:
    """读取视频元数据；ffprobe 不可用时仍返回可播放的文件索引。"""
    result: dict[str, object] = {"duration_s": None, "width": None, "height": None, "fps": None}
    if which("ffprobe"):
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height,r_frame_rate",
                 "-of", "json", str(path)], capture_output=True, text=True, timeout=8, check=False,
            )
            payload = json.loads(probe.stdout or "{}")
            duration = (payload.get("format") or {}).get("duration")
            stream = next((item for item in payload.get("streams", []) if item.get("width")), {})
            if duration is not None:
                result["duration_s"] = round(float(duration), 3)
            if stream:
                result["width"], result["height"] = stream.get("width"), stream.get("height")
                rate = str(stream.get("r_frame_rate") or "")
                if "/" in rate:
                    numerator, denominator = rate.split("/", 1)
                    if float(denominator):
                        result["fps"] = round(float(numerator) / float(denominator), 3)
        except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
            pass
    return result


def _library_ai_status() -> dict[str, dict]:
    if not LIBRARY_AI_STATUS_PATH.exists():
        return {}
    try:
        return json.loads(LIBRARY_AI_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_library_ai_status(status: dict[str, dict]) -> None:
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    LIBRARY_AI_STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def video_library(force: bool = False) -> dict[str, object]:
    """扫描 8 月 20/21 日素材并返回可导入、可缓存的素材库。"""
    files = [path for root in VIDEO_LIBRARY_ROOTS if root.exists() for path in root.rglob("*")
             if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS]
    # Keep the signature JSON-compatible; tuples read back from JSON become
    # lists and would otherwise force an ffprobe of every MP4 on every API call.
    signature = [[str(path), path.stat().st_size, path.stat().st_mtime_ns] for path in files]
    if not force and VIDEO_LIBRARY_CACHE.exists():
        try:
            cached = json.loads(VIDEO_LIBRARY_CACHE.read_text(encoding="utf-8"))
            if cached.get("signature") == signature:
                statuses = _library_ai_status()
                for item in cached.get("items", []):
                    item["ai_status"] = statuses.get(item.get("id"), item.get("ai_status") or {"status": "待AI预标注", "annotated_frames": 0, "reviewed_frames": 0})
                return cached
        except (OSError, json.JSONDecodeError):
            pass
    imports = {str(item.get("source_path")): item for item in read_jsonl(VIDEO_LIBRARY_IMPORTS)}
    ai_status = _library_ai_status()
    items = []
    for path in sorted(files, key=lambda value: str(value).lower()):
        stat = path.stat()
        item = {
            "id": _video_id(path),
            "name": path.name,
            "source_path": str(path),
            "relative_path": next((str(path.relative_to(root)) for root in VIDEO_LIBRARY_ROOTS if root in path.parents), path.name),
            "date_bucket": next((bucket for bucket in ("8月21日", "8月20日", "8月19日") if bucket[:-1] in str(path)), "项目素材"),
            "size_bytes": stat.st_size,
            "size_gb": round(stat.st_size / (1024 ** 3), 3),
            "modified_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            "status": "已导入" if str(path) in imports else "可导入",
            "import_url": imports.get(str(path), {}).get("media_url") or f"/media-library/{_video_id(path)}",
            "ai_status": ai_status.get(_video_id(path), {"status": "待AI预标注", "annotated_frames": 0, "reviewed_frames": 0}),
        }
        item.update(_probe_video(path))
        item["quality"] = {"blur_check": "待抽帧", "duplicate_check": "待比对", "event_slice": "待切片"}
        items.append(item)
    payload = {"ok": True, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "roots": [str(root) for root in VIDEO_LIBRARY_ROOTS], "signature": signature, "items": items,
               "cache_policy": "元数据立即入库；导入视频通过受控媒体路由播放，抽帧/去重/事件切片结果进入 runtime/video_quality.jsonl"}
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    VIDEO_LIBRARY_CACHE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def imported_video_path(video_id: str) -> Path | None:
    for item in video_library().get("items", []):
        if str(item.get("id")) == video_id:
            path = Path(str(item.get("source_path", ""))).resolve()
            if path.is_file() and any(root.resolve() in path.parents for root in VIDEO_LIBRARY_ROOTS if root.exists()):
                return path
    return None


def _library_video_entry(item: dict) -> dict:
    duration = float(item.get("duration_s") or 0.0)
    fps = float(item.get("fps") or 30.0)
    frames = max(1, int(round(duration * fps))) if duration else 1
    width, height = item.get("width") or 1280, item.get("height") or 720
    steps = [{"id": f"S{index:02d}", "label": f"视频素材阶段 {index}", "start_s": round(duration * (index - 1) / 6, 3), "end_s": round(duration * index / 6, 3), "roi": [0.02, 0.02, 0.98, 0.98]} for index in range(1, 7)]
    return {
        "id": item["id"], "display_name": f"{item['date_bucket']}｜{item['name']}",
        "source": item["source_path"], "source_video": item["import_url"], "video": item["import_url"],
        "presentation_video": item["import_url"], "frames": frames, "fps": fps,
        "resolution": f"{width}×{height}", "presentation_resolution": f"{width}×{height}",
        "duration_s": duration, "algorithm": "AI预标注候选 + 人工审核（可再次标注）",
        "steps": steps, "parts": [], "library": True, "date_bucket": item["date_bucket"],
        "ai_status": item.get("ai_status") or {"status": "待AI预标注", "annotated_frames": 0, "reviewed_frames": 0},
    }


def _set_library_ai_status(video_id: str, **updates: object) -> dict:
    with LIBRARY_AI_LOCK:
        status = _library_ai_status()
        current = {"status": "待AI预标注", "annotated_frames": 0, "reviewed_frames": 0, **status.get(video_id, {})}
        current.update(updates)
        status[video_id] = current
        _write_library_ai_status(status)
        return current


def _run_library_ai(video_id: str, force: bool = False) -> None:
    global LIBRARY_AI_MODEL, LIBRARY_AI_MODEL_PATH
    source = imported_video_path(video_id)
    if source is None:
        _set_library_ai_status(video_id, status="错误", message="素材不存在")
        return
    current = _library_ai_status().get(video_id, {})
    if current.get("status") == "运行中":
        return
    record_path = LIBRARY_AI_RECORDS_ROOT / f"{video_id}.jsonl"
    if record_path.exists() and not force and current.get("status") == "已完成":
        return
    _set_library_ai_status(video_id, status="运行中", message="正在抽帧并生成AI预标注")
    try:
        import cv2
        from ultralytics import YOLO
        info = video_info(video_id) or {}
        cap = cv2.VideoCapture(str(source))
        total = int(info.get("frames") or cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1)
        fps = float(info.get("fps") or cap.get(cv2.CAP_PROP_FPS) or 30.0)
        sample_count = min(24, max(8, int(total / max(1, int(fps * 15)))))
        sample_frames = sorted(set(int(round(index * max(0, total - 1) / max(1, sample_count - 1))) for index in range(sample_count)))
        model_path = Path(os.getenv("SOP_PRELABEL_MODEL", str(ROOT / "models" / "yolo11n.pt")))
        if LIBRARY_AI_MODEL is None or LIBRARY_AI_MODEL_PATH != str(model_path):
            LIBRARY_AI_MODEL = YOLO(str(model_path))
            LIBRARY_AI_MODEL_PATH = str(model_path)
        model = LIBRARY_AI_MODEL
        LIBRARY_AI_RECORDS_ROOT.mkdir(parents=True, exist_ok=True)
        with record_path.open("w", encoding="utf-8") as handle:
            for frame_number in sample_frames:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
                ok, frame = cap.read()
                if not ok:
                    continue
                detections = []
                result = model.predict(frame, conf=0.15, verbose=False, device=os.getenv("SOP_PRELABEL_DEVICE", "0"))[0]
                names = getattr(result, "names", {}) or {}
                boxes = getattr(result, "boxes", None)
                if boxes is not None:
                    for index in range(len(boxes)):
                        xyxy = boxes.xyxy[index].tolist()
                        cls_index = int(boxes.cls[index].item()) if boxes.cls is not None else -1
                        confidence = float(boxes.conf[index].item()) if boxes.conf is not None else 0.0
                        detections.append({"class": str(cls_index), "label": str(names.get(cls_index, f"目标{cls_index}")), "confidence": round(confidence, 4), "xyxy": [round(float(value), 2) for value in xyxy], "source": "YOLO AI预标注", "review_status": "pending"})
                handle.write(json.dumps({"frame": frame_number, "time_s": round(frame_number / fps, 4), "detections": detections, "parts": [], "ai_model": model_path.name}, ensure_ascii=False) + "\n")
        cap.release()
        FRAME_CACHE.pop(f"{video_id}:detections", None)
        FRAME_CACHE.pop(f"{video_id}:candidates", None)
        _set_library_ai_status(video_id, status="已完成", annotated_frames=len(sample_frames), model=model_path.name, completed_at=time.strftime("%Y-%m-%d %H:%M:%S"), message="AI预标注完成，等待人工审核")
    except Exception as exc:
        _set_library_ai_status(video_id, status="错误", message=f"AI预标注失败：{exc}")


def start_library_ai(video_ids: list[str], force: bool = False) -> dict[str, dict]:
    unique_ids = list(dict.fromkeys(video_ids))
    statuses = _library_ai_status()
    pending_ids = []
    result: dict[str, dict] = {}
    for video_id in unique_ids:
        current = statuses.get(video_id, {})
        record_path = LIBRARY_AI_RECORDS_ROOT / f"{video_id}.jsonl"
        if not force and current.get("status") == "已完成" and record_path.exists():
            result[video_id] = current
            continue
        pending_ids.append(video_id)
        result[video_id] = _set_library_ai_status(video_id, status="排队中", message="已进入AI预标注队列")
    def worker() -> None:
        for video_id in pending_ids:
            _run_library_ai(video_id, force=force)
    if pending_ids:
        threading.Thread(target=worker, name="library-ai-prelabel", daemon=True).start()
    return result


class LiveCameraService:
    """按需启动的 YOLOv11 摄像头 MJPEG 服务。"""

    def __init__(self, camera_id: int | None = None) -> None:
        self.camera_id = int(os.getenv("SOP_CAMERA_ID", "0")) if camera_id is None else camera_id
        configured_source = os.getenv(f"SOP_CAMERA_SOURCE_{self.camera_id}") or None
        if configured_source is None and self.camera_id == 0:
            configured_source = os.getenv("SOP_CAMERA_SOURCE")
        network_profile = next((item for item in network_camera_profiles() if int(item.get("id", -1)) == self.camera_id), {})
        self.network_profile = network_profile
        self.source = configured_source or DEFAULT_CAMERA_SOURCES.get(self.camera_id) or network_profile.get("rtsp_url") or str(self.camera_id)
        self.model_path = Path(os.getenv("SOP_CAMERA_MODEL", str(ROOT / "models" / "yolo11n.pt")))
        self.device = os.getenv("SOP_CAMERA_DEVICE", "0")
        self.confidence = float(os.getenv("SOP_CAMERA_CONFIDENCE", "0.35"))
        self.max_fps = float(os.getenv("SOP_CAMERA_MAX_FPS", "15"))
        self.width = int(os.getenv("SOP_CAMERA_WIDTH", "1280"))
        self.height = int(os.getenv("SOP_CAMERA_HEIGHT", "720"))
        self.output_root = EVIDENCE_ROOT
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None
        self._capture = None
        self._model = None
        self._latest_jpeg: bytes | None = None
        self._sequence = 0
        self._recording = False
        self._record_path: Path | None = None
        self._record_log_path: Path | None = None
        self._record_writer = None
        self._record_started_at: str | None = None
        self._record_frames = 0
        self._record_detections = 0
        self._record_log: list[dict] = []
        self._status: dict[str, object] = {
            "running": False,
            "model": "YOLOv11n",
            "camera_id": self.camera_id,
            "model_path": str(self.model_path),
            "source": self.source,
            "identity": {
                "ip": network_profile.get("ip"),
                "mac": network_profile.get("mac"),
                "name": network_profile.get("name"),
                "rtsp_path_confirmed": network_profile.get("rtsp_path_confirmed", False),
            },
            "device": self.device,
            "fps": 0.0,
            "inference_ms": 0.0,
            "detections": 0,
            "frame": 0,
            "last_frame_at": None,
            "recording": False,
            "recorded_path": None,
            "output_dir": str(self.output_root),
            "error": None,
        }

    @staticmethod
    def _source(value: str) -> int | str:
        return int(value) if value.isdigit() else value

    def status(self) -> dict[str, object]:
        with self._lock:
            return dict(self._status)

    def apply_network_profile(self, profile: dict) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("请先停止第三摄像头，再更新网络配置")
            self.network_profile = profile
            self.source = str(profile.get("rtsp_url", self.source))
            self._status.update({
                "source": self.source,
                "identity": {
                    "ip": profile.get("ip"),
                    "mac": profile.get("mac"),
                    "name": profile.get("name"),
                    "rtsp_path_confirmed": profile.get("rtsp_path_confirmed", False),
                },
                "error": None,
            })

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            try:
                import cv2
                from ultralytics import YOLO

                if not self.model_path.exists():
                    raise FileNotFoundError(f"YOLOv11模型不存在: {self.model_path}")
                source = self._source(self.source)
                if self.camera_id == 2:
                    if not self.network_profile.get("rtsp_path_confirmed", False):
                        raise RuntimeError("第三摄像头身份已绑定，但 RTSP 路径尚未确认；请从厂家 App 复制完整 RTSP 地址，先点击“测试 RTSP”再保存绑定")
                    endpoint = _rtsp_endpoint(str(source))
                    if endpoint is None:
                        raise RuntimeError("第三摄像头 RTSP 地址格式无效；必须是 rtsp://主机:端口/实际路径")
                    reachable, detail = _tcp_endpoint_open(*endpoint)
                    if not reachable:
                        raise RuntimeError(f"第三摄像头网络不可达 {endpoint[0]}:{endpoint[1]}：{detail}；请检查同一局域网、固定 IP 和路由器 DHCP 租约")
                if isinstance(source, str) and source.lower().startswith(("rtsp://", "rtsps://")):
                    transport = os.getenv("SOP_RTSP_TRANSPORT", "tcp").strip().lower() or "tcp"
                    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", f"rtsp_transport;{transport}|stimeout;10000000")
                    capture = cv2.VideoCapture(source, getattr(cv2, "CAP_FFMPEG", 0))
                else:
                    capture = cv2.VideoCapture(source)
                if not capture.isOpened():
                    raise RuntimeError(f"无法打开摄像头视频源: {_redact_rtsp_url(str(source))}")
                self._capture = capture
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if not (isinstance(source, str) and source.lower().startswith(("rtsp://", "rtsps://"))):
                    capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                    capture.set(cv2.CAP_PROP_FPS, min(self.max_fps or 30, 30))
                    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                self._model = YOLO(str(self.model_path))
                self._status.update({"running": True, "error": None, "started_at": time.time()})
                self._thread = threading.Thread(target=self._run, name="yolo11-camera", daemon=True)
                self._thread.start()
            except Exception as exc:
                self._status.update({"running": False, "error": str(exc)})
                self._release_capture()
                raise

    def stop(self) -> None:
        with self._lock:
            self._status["running"] = False
            self._status["error"] = None
            self._finalize_recording_locked()
            self._release_capture()
            self._condition.notify_all()

    def start_recording(self) -> dict[str, object]:
        with self._lock:
            if not self._thread or not self._thread.is_alive() or not self._status.get("running"):
                raise RuntimeError("请先启动实时检测，再开始录制")
            if self._recording:
                return {"recording": True, "path": str(self._record_path) if self._record_path else None}
            stamp = time.strftime("%Y%m%d_%H%M%S")
            self.output_root.mkdir(parents=True, exist_ok=True)
            self._record_path = self.output_root / f"CAM_{self.camera_id}_{stamp}.mp4"
            self._record_log_path = self.output_root / f"CAM_{self.camera_id}_{stamp}.jsonl"
            self._record_writer = None
            self._record_started_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self._record_frames = 0
            self._record_detections = 0
            self._record_log = []
            self._recording = True
            self._status.update({"recording": True, "recorded_path": str(self._record_path), "error": None})
            return {"recording": True, "path": str(self._record_path)}

    def _finalize_recording_locked(self) -> dict[str, object] | None:
        if not self._recording and self._record_writer is None:
            return None
        if self._record_writer is not None:
            self._record_writer.release()
            self._record_writer = None
        if self._record_log_path:
            self._record_log_path.parent.mkdir(parents=True, exist_ok=True)
            self._record_log_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in self._record_log) + ("\n" if self._record_log else ""), encoding="utf-8")
        metadata_path = self._record_path.with_suffix(".metadata.json") if self._record_path else None
        if metadata_path:
            metadata_path.write_text(json.dumps({
                "camera_id": self.camera_id,
                "source": self.source,
                "started_at": self._record_started_at,
                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "frames": self._record_frames,
                "detections": self._record_detections,
                "video": str(self._record_path),
                "detections_log": str(self._record_log_path) if self._record_log_path else None,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        result = {"recording": False, "path": str(self._record_path) if self._record_path else None, "frames": self._record_frames}
        self._recording = False
        self._status.update({"recording": False, "recorded_path": str(self._record_path) if self._record_path else None})
        return result

    def stop_recording(self) -> dict[str, object]:
        with self._lock:
            return self._finalize_recording_locked() or {"recording": False, "path": None, "frames": 0}

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            if not self._latest_jpeg:
                raise RuntimeError("当前没有可保存的检测画面")
            self.output_root.mkdir(parents=True, exist_ok=True)
            path = self.output_root / f"CAM_{self.camera_id}_{time.strftime('%Y%m%d_%H%M%S')}_{self._sequence:06d}.jpg"
            path.write_bytes(self._latest_jpeg)
            return {"path": str(path), "frame": self._sequence, "detections": self._status.get("detections", 0)}

    def _release_capture(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def _run(self) -> None:
        import cv2

        while True:
            loop_started = time.perf_counter()
            with self._lock:
                capture = self._capture
                model = self._model
                if not self._status.get("running") or capture is None or model is None:
                    break
            ok, frame = capture.read()
            if not ok:
                with self._lock:
                    if not self._status.get("running"):
                        break
                    self._status.update({"running": False, "error": "摄像头读取失败或已断开"})
                    self._finalize_recording_locked()
                    self._release_capture()
                    self._condition.notify_all()
                break
            started = time.perf_counter()
            try:
                result = model.predict(source=frame, imgsz=640, conf=self.confidence, device=self.device, half=self.device != "cpu", max_det=50, verbose=False)[0]
                annotated = result.plot()
                detections = len(result.boxes) if result.boxes is not None else 0
                inference_ms = (time.perf_counter() - started) * 1000
                encoded, buffer = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
                if not encoded:
                    continue
                with self._lock:
                    if not self._status.get("running") or self._capture is not capture:
                        break
                    self._latest_jpeg = buffer.tobytes()
                    self._sequence += 1
                    if self._recording:
                        if self._record_writer is None:
                            self._record_writer = cv2.VideoWriter(
                                str(self._record_path), cv2.VideoWriter_fourcc(*"mp4v"),
                                max(1.0, min(float(capture.get(cv2.CAP_PROP_FPS) or 25), 30.0)),
                                (int(frame.shape[1]), int(frame.shape[0])),
                            )
                            if not self._record_writer.isOpened():
                                self._record_writer.release()
                                self._record_writer = None
                                raise RuntimeError("无法创建桌面证据 MP4，请检查 OpenCV 编码器")
                        self._record_writer.write(annotated)
                        self._record_frames += 1
                        self._record_detections += detections
                        self._record_log.append({"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "frame": self._record_frames, "detections": detections})
                    self._status.update({
                        "running": True,
                        "frame": self._sequence,
                        "detections": detections,
                        "inference_ms": round(inference_ms, 1),
                        "fps": round(float(capture.get(cv2.CAP_PROP_FPS) or 0), 1),
                        "width": int(frame.shape[1]),
                        "height": int(frame.shape[0]),
                        "last_frame_at": time.time(),
                        "recording": self._recording,
                        "error": None,
                    })
                    self._condition.notify_all()
            except Exception as exc:
                with self._lock:
                    self._status.update({"running": False, "error": f"YOLO推理失败: {exc}"})
                    self._finalize_recording_locked()
                    self._release_capture()
                    self._condition.notify_all()
                break
            if self.max_fps > 0:
                time.sleep(max(0.0, (1.0 / self.max_fps) - (time.perf_counter() - loop_started)))

    def mjpeg(self, handler: "SOPHandler") -> None:
        try:
            self.start()
        except Exception as exc:
            handler.send_json({"ok": False, "message": str(exc), "status": self.status()}, 503)
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        handler.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        handler.send_header("Pragma", "no-cache")
        handler.send_header("Connection", "close")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()
        last_sequence = 0
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._sequence > last_sequence or not self._status.get("running"), timeout=2.0)
                    jpeg = self._latest_jpeg
                    last_sequence = self._sequence
                    if jpeg is None:
                        if not self._status.get("running"):
                            break
                        continue
                handler.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


LIVE_CAMERAS = {camera_id: LiveCameraService(camera_id) for camera_id in (0, 1, 2)}


SOFTWARE_CHECKS = [
    ("OpenCV", "采集/ROI/图像处理", "cv2", None),
    ("Ultralytics YOLOv11", "GPU目标检测", "ultralytics", None),
    ("PyTorch CUDA", "DGX GPU推理", "torch", None),
    ("FFmpeg", "转码/抽帧/证据视频", None, "ffmpeg"),
    ("FFprobe", "视频质量检查", None, "ffprobe"),
    ("GStreamer", "低延迟视频管线", None, "gst-launch-1.0"),
    ("PySerial", "扫码枪/USB485/串口", "serial", None),
    ("PyModbus", "Modbus PLC", "pymodbus", None),
    ("AsyncUA", "OPC UA", "asyncua", None),
    ("FastAPI", "IPC/DGX API", "fastapi", None),
    ("Label Studio", "视频时序/审核标注", None, None),
    ("CVAT", "工业框/Mask/Tracking标注", None, None),
    ("Docker", "DGX服务容器化", None, "docker"),
]


def software_status() -> list[dict[str, object]]:
    label_studio_url = os.getenv("LABEL_STUDIO_URL", "http://127.0.0.1:8080").rstrip("/")
    cvat_url = os.getenv("CVAT_URL", "http://127.0.0.1:8081").rstrip("/")
    result = []
    for name, purpose, module, command in SOFTWARE_CHECKS:
        if name == "Label Studio":
            available = False
            try:
                with urlopen(label_studio_url, timeout=0.5) as response:
                    available = 200 <= response.status < 500
            except Exception:
                pass
            result.append({"name": name, "purpose": purpose, "installed": available, "endpoint": label_studio_url, "note": "Docker Compose未启动" if not available else "服务在线"})
        elif name == "CVAT":
            result.append({"name": name, "purpose": purpose, "installed": False, "endpoint": cvat_url, "note": "建议厂内自托管"})
        else:
            installed = bool(importlib.util.find_spec(module)) if module else bool(which(command))
            result.append({"name": name, "purpose": purpose, "installed": installed, "endpoint": None, "note": "可用" if installed else "未安装"})
    return result


def camera_service(camera_id: int) -> LiveCameraService:
    if camera_id not in LIVE_CAMERAS:
        raise ValueError(f"相机编号不支持: {camera_id}")
    return LIVE_CAMERAS[camera_id]


def video_catalog() -> dict:
    catalog = json.loads((DATA_ROOT / "videos.json").read_text(encoding="utf-8"))
    library = video_library()
    dynamic = [_library_video_entry(item) for item in library.get("items", [])]
    # 保留原来的五段视频顺序和字段，再把 8 月 20/21 日全部素材追加到同一目录。
    catalog["videos"] = catalog.get("videos", []) + dynamic
    catalog["totals"] = {
        **catalog.get("totals", {}),
        "videos": len(catalog["videos"]),
        "duration_s": round(sum(float(item.get("duration_s") or 0) for item in catalog["videos"]), 2),
        "frames": sum(int(item.get("frames") or 0) for item in catalog["videos"]),
        "steps": sum(len(item.get("steps") or []) for item in catalog["videos"]),
    }
    catalog["library"] = {"total": len(dynamic), "roots": library.get("roots", []), "ai_policy": "宁波SoP项目根目录下全部 MP4 先AI预标注，再人工复核；可再次标注并保留版本"}
    return catalog


def frame_records(video_id: str, kind: str = "detections") -> list[dict]:
    key = f"{video_id}:{kind}"
    if key in FRAME_CACHE:
        return FRAME_CACHE[key]
    if video_id.startswith("library_"):
        path = LIBRARY_AI_RECORDS_ROOT / f"{video_id}.jsonl"
    elif kind == "candidates":
        path = DATA_ROOT / f"{video_id}_fastener_candidates.jsonl"
        if not path.exists():
            path = DATA_ROOT / f"{video_id}_fine_object_candidates.jsonl"
    else:
        path = DATA_ROOT / ("frame_annotations.jsonl" if video_id == "video_de02" else f"{video_id}_frame_annotations.jsonl")
    if not path.exists():
        return []
    FRAME_CACHE[key] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return FRAME_CACHE[key]


def record_for_frame(records: list[dict], frame: int) -> dict:
    if not records:
        return {}
    positions = [int(record.get("frame", index)) for index, record in enumerate(records)]
    index = bisect_left(positions, frame)
    if index <= 0:
        return records[0]
    if index >= len(records):
        return records[-1]
    before, after = positions[index - 1], positions[index]
    return records[index - 1] if frame - before <= after - frame else records[index]


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            record["_line"] = line_number
            records.append(record)
        except json.JSONDecodeError:
            continue
    return records


def video_info(video_id: str) -> dict | None:
    return next((item for item in video_catalog().get("videos", []) if item.get("id") == video_id), None)


def video_id_for_source(source: str | None) -> str:
    source_name = Path(source or "").name
    for item in video_catalog().get("videos", []):
        if Path(item.get("source_video", "")).name == source_name:
            return str(item["id"])
    return "video_de02"


def video_size(video_id: str) -> tuple[int, int]:
    info = video_info(video_id) or {}
    value = str(info.get("resolution", "1280x720")).lower().replace("×", "x")
    try:
        width, height = value.split("x", 1)
        return max(1, int(width)), max(1, int(height))
    except (TypeError, ValueError):
        return 1280, 720


def normalize_box(box: object, video_id: str) -> tuple[list[float], list[float]]:
    if not isinstance(box, list) or len(box) != 4:
        raise ValueError("标注框必须是包含4个数值的xyxy数组")
    try:
        values = [float(value) for value in box]
    except (TypeError, ValueError) as exc:
        raise ValueError("标注框包含非数值字段") from exc
    if not all(math.isfinite(value) for value in values):
        raise ValueError("标注框包含无效数值")
    width, height = video_size(video_id)
    if max(values) <= 1.00001:
        normalized = values
        pixels = [values[0] * width, values[1] * height, values[2] * width, values[3] * height]
    else:
        pixels = values
        normalized = [values[0] / width, values[1] / height, values[2] / width, values[3] / height]
    normalized = [min(1.0, max(0.0, value)) for value in normalized]
    if normalized[2] <= normalized[0] or normalized[3] <= normalized[1]:
        raise ValueError("标注框坐标无效或超出画面")
    pixels = [round(value, 2) for value in pixels]
    return [round(value, 6) for value in normalized], pixels


def annotation_reviews() -> dict[str, dict]:
    return {
        str(item.get("annotation_id")): item
        for item in read_jsonl(RUNTIME_ROOT / "annotation_reviews.jsonl")
        if item.get("annotation_id")
    }


def annotation_deletions() -> dict[str, dict]:
    """Return the latest deletion tombstone for each annotation.

    JSONL is retained for portability, but deletion is a real state transition:
    readers filter tombstoned ids and the original record remains auditable.
    """
    latest: dict[str, dict] = {}
    for item in read_jsonl(ANNOTATION_DELETIONS_PATH):
        annotation_id = str(item.get("annotation_id") or "").strip()
        if annotation_id:
            latest[annotation_id] = item
    return latest


def annotation_tracks(video_id: str | None = None) -> list[dict]:
    tracks: dict[str, dict] = {}
    for item in read_jsonl(ANNOTATION_TRACKS_PATH):
        track_id = str(item.get("track_id") or "").strip()
        if not track_id:
            continue
        if video_id and str(item.get("video_id")) != video_id:
            continue
        tracks[track_id] = item
    deleted = annotation_deletions()
    visible = []
    for track in tracks.values():
        points = [point for point in track.get("points", []) if deleted.get(f"{track.get('track_id')}:{point.get('frame')}", {}).get("action") != "delete"]
        if not points:
            continue
        visible.append({**track, "points": points, "start_frame": points[0].get("frame"), "end_frame": points[-1].get("frame")})
    return list(reversed(visible))


def frame_annotation_items(video_id: str, requested_time: float) -> list[dict]:
    info = video_info(video_id)
    if info is None:
        raise ValueError("视频不存在")
    frame = min(max(0, int(round(requested_time * float(info.get("fps", 30))))), int(info.get("frames", 1)) - 1)
    width, height = video_size(video_id)
    reviews = annotation_reviews()
    deleted = annotation_deletions()
    items: list[dict] = []
    sources = (("prelabel", frame_records(video_id), "detections"), ("candidate", frame_records(video_id, "candidates"), "candidates"))
    for source_kind, records, field in sources:
        if not records:
            continue
        record = record_for_frame(records, frame)
        for index, detection in enumerate(record.get(field, [])):
            annotation_id = f"{video_id}:{source_kind}:{record.get('frame', frame)}:{index}"
            if deleted.get(annotation_id, {}).get("action", "delete") == "delete":
                continue
            normalized, pixels = normalize_box(detection.get("xyxy"), video_id)
            review = reviews.get(annotation_id, {})
            items.append({
                "annotation_id": annotation_id,
                "video_id": video_id,
                "frame": int(record.get("frame", frame)),
                "video_time": float(record.get("time_s", requested_time)),
                "label": detection.get("label") or detection.get("class") or "未分类目标",
                "class": detection.get("class"),
                "confidence": detection.get("confidence"),
                "box": normalized,
                "box_pixels": pixels,
                "box_format": "normalized_xyxy",
                "source_kind": source_kind,
                "source": detection.get("source") or ("逐帧检测预标注" if source_kind == "prelabel" else "小目标候选器"),
                "review_status": review.get("review_status") or detection.get("review_status") or "pending",
                "reviewer": review.get("reviewer"),
                "reviewed_at": review.get("recorded_at"),
                "remarks": review.get("remarks") or review.get("comment", ""),
            })
    # Persisted keyframe interpolation is presented beside manual boxes so it
    # can be reviewed, edited, or deleted with the same controls.
    for interpolation in read_jsonl(ANNOTATION_INTERPOLATIONS_PATH):
        if str(interpolation.get("video_id")) != video_id:
            continue
        point = next((point for point in interpolation.get("points", []) if int(point.get("frame", -1)) == frame), None)
        if not point:
            continue
        annotation_id = f"{interpolation.get('interpolation_id')}:{frame}"
        if deleted.get(annotation_id, {}).get("action") != "delete":
            items.append({
                "annotation_id": annotation_id, "video_id": video_id, "frame": frame,
                "video_time": float(point.get("time_s", requested_time)), "label": interpolation.get("label", "关键物件"),
                "confidence": 1.0, "box": point.get("box"), "box_format": "normalized_xyxy",
                "source_kind": "manual", "source": "关键帧插值", "review_status": "pending",
                "annotator_name": interpolation.get("annotator_name"), "remarks": interpolation.get("remarks", ""),
            })
    return items


def manual_annotation_items(video_id: str | None = None) -> list[dict]:
    reviews = annotation_reviews()
    deleted = annotation_deletions()
    items = []
    for record in read_jsonl(RUNTIME_ROOT / "annotations.jsonl"):
        record_video_id = str(record.get("video_id") or video_id_for_source(record.get("video")))
        if video_id and record_video_id != video_id:
            continue
        normalized, pixels = normalize_box(record.get("box"), record_video_id)
        annotation_id = str(record.get("annotation_id") or f"manual:{record_video_id}:{record.get('_line')}")
        if deleted.get(annotation_id, {}).get("action", "delete") == "delete":
            continue
        review = reviews.get(annotation_id, {})
        items.append({
            **{key: value for key, value in record.items() if key != "_line"},
            "annotation_id": annotation_id,
            "video_id": record_video_id,
            "frame": int(record.get("frame", round(float(record.get("video_time", 0)) * float((video_info(record_video_id) or {}).get("fps", 30))))),
            "video_time": float(record.get("video_time", 0)),
            "box": normalized,
            "box_pixels": pixels,
            "box_format": "normalized_xyxy",
            "source_kind": "manual",
            "source": record.get("source", "平台人工标注"),
            "review_status": review.get("review_status") or record.get("review_status", "human_confirmed"),
            "reviewer": review.get("reviewer") or record.get("reviewer"),
            "reviewed_at": review.get("recorded_at"),
            "remarks": review.get("remarks") or review.get("comment", "") or record.get("remarks", ""),
        })
    return items


def annotation_stats() -> dict:
    prelabels = 0
    candidates = 0
    for video in video_catalog().get("videos", []):
        video_id = str(video["id"])
        prelabels += sum(len(record.get("detections", [])) for record in frame_records(video_id))
        candidates += sum(len(record.get("candidates", [])) for record in frame_records(video_id, "candidates"))
    manual = manual_annotation_items()
    reviews = read_jsonl(RUNTIME_ROOT / "annotation_reviews.jsonl")
    status_counts: dict[str, int] = {}
    for item in manual + reviews:
        status = str(item.get("review_status", "pending"))
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "ok": True,
        "prelabels": prelabels,
        "candidates": candidates,
        "manual": len(manual),
        "reviews": len(reviews),
        "status_counts": status_counts,
        "truth_boundary": "预标注和小目标候选仅用于提高人工标注效率，人工确认前不属于生产真值。",
    }


def exported_annotation_items(status: str = "human_confirmed") -> list[dict]:
    items = [item for item in manual_annotation_items() if item.get("review_status") == status]
    seen = {str(item["annotation_id"]) for item in items}
    for annotation_id, review in annotation_reviews().items():
        if review.get("review_status") != status or annotation_id in seen or annotation_id.startswith("manual:"):
            continue
        parts = annotation_id.split(":")
        if len(parts) != 4 or parts[1] not in {"prelabel", "candidate"}:
            continue
        video_id, _, frame_text, _ = parts
        info = video_info(video_id)
        if info is None:
            continue
        try:
            frame = int(frame_text)
        except ValueError:
            continue
        frame_items = frame_annotation_items(video_id, frame / float(info.get("fps", 30)))
        item = next((candidate for candidate in frame_items if candidate.get("annotation_id") == annotation_id), None)
        if item:
            items.append(item)
            seen.add(annotation_id)
    return sorted(items, key=lambda item: (str(item.get("video_id")), int(item.get("frame", 0)), str(item.get("annotation_id"))))


def box_iou(left: list[float], right: list[float]) -> float:
    lx1, ly1, lx2, ly2 = left
    rx1, ry1, rx2, ry2 = right
    ix1, iy1, ix2, iy2 = max(lx1, rx1), max(ly1, ry1), min(lx2, rx2), min(ly2, ry2)
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    left_area = max(0.0, lx2 - lx1) * max(0.0, ly2 - ly1)
    right_area = max(0.0, rx2 - rx1) * max(0.0, ry2 - ry1)
    denominator = left_area + right_area - intersection
    return intersection / denominator if denominator else 0.0


def generate_track(video_id: str, start_frame: int, end_frame: int, anchor_box: list[float], label: str) -> dict[str, object]:
    """用现有逐帧检测结果生成快速可编辑轨迹。

    这是标注辅助轨迹，不冒充 SAM2 真值：每帧优先选择同标签且与上一框 IoU
    最大的候选；检测缺失时短暂保持上一框，保证跳帧时 UI 不冻结。
    """
    info = video_info(video_id)
    if info is None:
        raise ValueError("视频不存在")
    max_frame = max(0, int(info.get("frames", 1)) - 1)
    start_frame = min(max(0, int(start_frame)), max_frame)
    end_frame = min(max(0, int(end_frame)), max_frame)
    direction = 1 if end_frame >= start_frame else -1
    span = abs(end_frame - start_frame)
    step = max(1, math.ceil(span / 1800))
    records = frame_records(video_id)
    candidate_records = frame_records(video_id, "candidates")
    points: list[dict[str, object]] = []
    previous = [float(value) for value in anchor_box]
    frames = list(range(start_frame, end_frame + direction, direction * step))
    if frames[-1] != end_frame:
        frames.append(end_frame)
    for frame in frames:
        record = record_for_frame(records, frame) if records else {}
        candidate_record = record_for_frame(candidate_records, frame) if candidate_records else {}
        detections = list(record.get("detections", [])) + list(candidate_record.get("candidates", []))
        choices = []
        for detection in detections:
            detected_label = str(detection.get("label") or detection.get("class") or "")
            if label and detected_label != label and label not in detected_label and detected_label not in label:
                continue
            try:
                normalized, _ = normalize_box(detection.get("xyxy"), video_id)
            except ValueError:
                continue
            confidence = float(detection.get("confidence") or 0.0)
            choices.append((box_iou(previous, normalized) + confidence * 0.15, normalized, confidence))
        # A manually named PCB/工具 label often differs from the detector's
        # COCO class. Keep tracking the best spatial candidate instead of
        # freezing at the anchor when that happens.
        if not choices:
            for detection in detections:
                try:
                    normalized, _ = normalize_box(detection.get("xyxy"), video_id)
                except ValueError:
                    continue
                confidence = float(detection.get("confidence") or 0.0)
                choices.append((box_iou(previous, normalized) + confidence * 0.05, normalized, confidence))
        # 起始帧必须保留人工锚点；从下一帧开始才允许检测结果修正位置。
        if choices and frame != start_frame:
            _, previous, confidence = max(choices, key=lambda item: item[0])
        elif frame == start_frame:
            confidence = 1.0
        else:
            confidence = 0.0
        points.append({"frame": frame, "time_s": round(frame / float(info.get("fps", 30)), 4), "box": [round(value, 6) for value in previous], "confidence": round(confidence, 4), "source": "manual_anchor" if frame == start_frame else ("detector_iou" if choices else "hold_last")})
    track_id = f"track:{video_id}:{start_frame}:{time.time_ns()}"
    return {"track_id": track_id, "video_id": video_id, "label": label, "start_frame": start_frame, "end_frame": end_frame, "points": points, "method": "逐帧检测 + IoU连续匹配（可替换为SAM2）", "editable": True}


class SOPHandler(SimpleHTTPRequestHandler):
    server_version = "NingboSOP/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, format: str, *args) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {format % args}")

    def guess_type(self, path: str) -> str:
        content_type = super().guess_type(path)
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            return f"{content_type}; charset=utf-8"
        return content_type

    def send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 12_000_000:
            raise ValueError("请求内容过大")
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def append_event(self, filename: str, payload: dict) -> None:
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        payload = {"recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"), **payload}
        with (RUNTIME_ROOT / filename).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/dashboard":
            self.send_json(json.loads((DATA_ROOT / "dashboard.json").read_text(encoding="utf-8")))
            return
        if path == "/api/recipe":
            self.send_json(json.loads(RECIPE_PATH.read_text(encoding="utf-8")))
            return
        if path == "/api/pcb-rules":
            self.send_json(json.loads(PCB_RULES_PATH.read_text(encoding="utf-8")) if PCB_RULES_PATH.exists() else {"stations": {}, "objects": [], "rois": []})
            return
        if path == "/api/sop-workflow-rules":
            self.send_json(json.loads(WORKFLOW_RULES_PATH.read_text(encoding="utf-8")) if WORKFLOW_RULES_PATH.exists() else {})
            return
        if path == "/api/videos":
            self.send_json(video_catalog())
            return
        if path == "/api/video-library":
            query = parse_qs(parsed.query)
            self.send_json(video_library(force=query.get("refresh", ["0"])[0] == "1"))
            return
        if path == "/api/video-library/ai-status":
            self.send_json({"ok": True, "items": _library_ai_status()})
            return
        if path == "/api/annotations":
            query = parse_qs(parsed.query)
            video_id = query.get("video", ["video_de02"])[0]
            requested_time = max(0.0, float(query.get("time", ["0"])[0]))
            source = query.get("source", ["all"])[0]
            status = query.get("status", ["all"])[0]
            limit = min(500, max(1, int(query.get("limit", ["100"])[0])))
            items = []
            if source in {"all", "frame", "prelabel", "candidate"}:
                items.extend(frame_annotation_items(video_id, requested_time))
            if source in {"all", "manual"}:
                items.extend(manual_annotation_items(video_id))
            if source in {"prelabel", "candidate"}:
                items = [item for item in items if item.get("source_kind") == source]
            if status != "all":
                items = [item for item in items if item.get("review_status") == status]
            self.send_json({
                "ok": True,
                "video_id": video_id,
                "requested_time": round(requested_time, 3),
                "items": items[:limit],
                "total": len(items),
                "frame_size": {"width": video_size(video_id)[0], "height": video_size(video_id)[1]},
            })
            return
        if path == "/api/annotations/stats":
            self.send_json(annotation_stats())
            return
        if path == "/api/annotations/export":
            query = parse_qs(parsed.query)
            status = query.get("status", ["human_confirmed"])[0]
            items = exported_annotation_items(status)
            self.send_json({
                "ok": True,
                "dataset_version": time.strftime("sop-annotations-%Y%m%d"),
                "box_format": "normalized_xyxy",
                "review_status": status,
                "items": items,
                "total": len(items),
                "truth_boundary": "导出结果包含当前审核状态快照；正式训练前仍需质量部门锁定版本并计算文件校验值。",
            })
            return
        if path == "/api/annotations/track":
            query = parse_qs(parsed.query)
            video_id = query.get("video", ["video_de02"])[0]
            start_frame = int(query.get("start_frame", ["0"])[0])
            end_frame = int(query.get("end_frame", [str(start_frame)])[0])
            label = query.get("label", [""])[0]
            try:
                anchor_box = json.loads(query.get("box", ["[]"])[0])
            except json.JSONDecodeError as exc:
                raise ValueError("轨迹起始框格式无效") from exc
            normalized, _ = normalize_box(anchor_box, video_id)
            self.send_json({"ok": True, **generate_track(video_id, start_frame, end_frame, normalized, label)})
            return
        if path == "/api/annotations/tracks":
            query = parse_qs(parsed.query)
            video_id = query.get("video", [""])[0] or None
            self.send_json({"ok": True, "tracks": annotation_tracks(video_id)})
            return
        if path == "/api/annotations/deletions":
            query = parse_qs(parsed.query)
            video_id = query.get("video", [""])[0]
            items = list(annotation_deletions().values())
            if video_id:
                items = [item for item in items if str(item.get("video_id")) == video_id]
            self.send_json({"ok": True, "items": list(reversed(items[-500:]))})
            return
        if path == "/api/annotations/interpolations":
            query = parse_qs(parsed.query)
            video_id = query.get("video", [""])[0]
            items = read_jsonl(ANNOTATION_INTERPOLATIONS_PATH)
            if video_id:
                items = [item for item in items if str(item.get("video_id")) == video_id]
            self.send_json({"ok": True, "items": list(reversed(items[-500:]))})
            return
        if path == "/api/algorithm-comparison":
            if not ALGORITHM_COMPARISON_PATH.exists():
                self.send_json({"ok": False, "message": "三算法对比配置不存在"}, 404)
                return
            self.send_json(json.loads(ALGORITHM_COMPARISON_PATH.read_text(encoding="utf-8")))
            return
        if path == "/api/decision":
            query = parse_qs(parsed.query)
            video_id = query.get("video", ["video_de02"])[0]
            requested_time = max(0.0, float(query.get("time", ["0"])[0]))
            catalog = video_catalog()
            video = next((item for item in catalog["videos"] if item["id"] == video_id), None)
            if video is None:
                self.send_json({"ok": False, "message": "视频不存在"}, 404)
                return
            index = min(int(round(requested_time * float(video["fps"]))), int(video["frames"]) - 1)
            records = frame_records(video_id)
            candidates = frame_records(video_id, "candidates")
            record = record_for_frame(records, index) if records else {"detections": [], "parts": [], "completed_steps": 0}
            candidate_record = record_for_frame(candidates, index) if candidates else {"candidates": []}
            step = next((item for item in video["steps"] if float(item["start_s"]) <= requested_time < float(item["end_s"])), video["steps"][-1])
            completed = sum(requested_time >= float(item["end_s"]) for item in video["steps"])
            dynamic = record.get("detections", [])
            fasteners = candidate_record.get("candidates", [])
            has_tool = any(item.get("label") == "电动紧固工具" for item in dynamic)
            has_hand = any(item.get("label") == "操作人员手部" for item in dynamic)
            confidence_values = [float(item.get("confidence", 0)) for item in dynamic + fasteners]
            evidence_score = round(100 * max(confidence_values), 1) if confidence_values else 0.0
            risk_score = 62 + (8 if not has_tool and "紧固" in step["label"] else 0)
            reasons = [
                f"当前应执行 {step['id']}：{step['label']}",
                f"已加载{len(record.get('parts', []))}个业务零件区域、{len(dynamic)}个动态目标、{len(fasteners)}个紧固点候选",
                "视觉证据只用于步骤判断；真实扭矩和MES回执尚未接入",
            ]
            action = "保持工位HOLD，等待工具控制器与MES确认"
            if "紧固" in step["label"] and not has_tool:
                action = "当前是紧固步骤但未稳定看到工具，请检查遮挡、相机角度或工具报文"
            self.send_json({
                "ok": True, "video_id": video_id, "time_s": round(requested_time, 2), "frame": index,
                "step": step, "completed_steps": completed, "visual_state": "PASS" if completed >= len(video["steps"]) else "RUNNING",
                "release": "HOLD", "risk_score": min(risk_score, 100), "risk_level": "中高" if risk_score >= 65 else "中",
                "evidence_score": evidence_score, "objects": {"business_regions": len(record.get("parts", [])), "dynamic": len(dynamic), "fastener_candidates": len(fasteners), "hand_seen": has_hand, "tool_seen": has_tool},
                "reasons": reasons, "recommended_action": action,
                "decision_chain": ["目标检测", "跨帧跟踪", "工位区域", "步骤顺序", "紧固质量", "MES确认", "最终放行"],
                "truth_notice": "紧固点为自动预标注候选，人工复核前不计入螺钉合格数量",
            })
            return
        if path == "/api/health":
            self.send_json({"status": "ok", "service": "宁波SOP分析平台", "time": time.time()})
            return
        if path == "/api/device/inventory":
            self.send_json(device_inventory())
            return
        if path == "/api/network-cameras":
            self.send_json({"cameras": network_camera_profiles()})
            return
        if path == "/api/integrations":
            label_studio_url = os.getenv("LABEL_STUDIO_URL", "http://127.0.0.1:8080").rstrip("/")
            available = False
            try:
                with urlopen(label_studio_url, timeout=0.8) as response:
                    available = 200 <= response.status < 500
            except Exception:
                available = False
            self.send_json({"label_studio": {"url": label_studio_url, "available": available}, "cvat": {"url": os.getenv("CVAT_URL", "http://127.0.0.1:8081")}})
            return
        if path == "/api/software/status":
            self.send_json({"ok": True, "host": "DGX Spark / IPC软件清单", "items": software_status()})
            return
        if path == "/api/model-benchmark":
            report_path = WEB_ROOT / "analysis" / "model_benchmark" / "benchmark_report.json"
            if not report_path.exists():
                self.send_json({"ok": False, "message": "性能对比报告尚未生成，请运行 scripts/benchmark_detection_models.py"}, 404)
                return
            self.send_json(json.loads(report_path.read_text(encoding="utf-8")))
            return
        if path.startswith("/media-library/"):
            library_id = unquote(path.removeprefix("/media-library/")).strip()
            target = imported_video_path(library_id)
            if target is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.serve_external_media(target)
            return
        if path == "/api/camera/status":
            query = parse_qs(parsed.query)
            try:
                selected = int(query.get("camera", ["0"])[0])
                self.send_json({"ok": True, **camera_service(selected).status(), "cameras": [service.status() for service in LIVE_CAMERAS.values()]})
            except (TypeError, ValueError) as exc:
                self.send_json({"ok": False, "message": str(exc)}, 400)
            return
        if path == "/api/camera/mjpeg":
            query = parse_qs(parsed.query)
            try:
                selected = int(query.get("camera", ["0"])[0])
                camera_service(selected).mjpeg(self)
            except (TypeError, ValueError) as exc:
                self.send_json({"ok": False, "message": str(exc)}, 400)
            return
        if path.startswith("/media/") and self.headers.get("Range"):
            self.serve_media_range(path)
            return
        super().do_GET()

    def serve_media_range(self, request_path: str) -> None:
        relative = unquote(request_path).lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in target.parents or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        size = target.stat().st_size
        value = self.headers.get("Range", "bytes=0-").removeprefix("bytes=")
        start_text, _, end_text = value.partition("-")
        start = int(start_text or 0)
        end = min(int(end_text) if end_text else size - 1, size - 1)
        if start < 0 or start > end:
            self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return
        length = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        with target.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    break
                remaining -= len(chunk)

    def serve_external_media(self, target: Path) -> None:
        """仅服务于 video_library() 已登记的素材，支持浏览器 Range 播放。"""
        size = target.stat().st_size
        value = self.headers.get("Range", "bytes=0-").removeprefix("bytes=")
        start_text, _, end_text = value.partition("-")
        start = int(start_text or 0)
        end = min(int(end_text) if end_text else size - 1, size - 1)
        if start < 0 or start > end:
            self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return
        length = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT if self.headers.get("Range") else HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        with target.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    break
                remaining -= len(chunk)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/annotations":
                required = {"video_time", "label", "box"}
                if not required.issubset(payload):
                    self.send_json({"ok": False, "message": "标注字段不完整"}, 400)
                    return
                video_id = str(payload.get("video_id") or video_id_for_source(payload.get("video")))
                if video_info(video_id) is None:
                    self.send_json({"ok": False, "message": "视频不存在"}, 404)
                    return
                normalized, pixels = normalize_box(payload["box"], video_id)
                video_time = max(0.0, float(payload["video_time"]))
                fps = float((video_info(video_id) or {}).get("fps", 30))
                annotation = {
                    **payload,
                    "annotation_id": str(payload.get("annotation_id") or f"manual:{video_id}:{time.time_ns()}"),
                    "video_id": video_id,
                    "video_time": round(video_time, 3),
                    "frame": int(round(video_time * fps)),
                    "box": normalized,
                    "box_pixels": pixels,
                    "box_format": "normalized_xyxy",
                    "source_kind": "manual",
                    "source": payload.get("source", "平台人工标注"),
                    "review_status": payload.get("review_status", "pending"),
                    "reviewer": payload.get("reviewer") or payload.get("annotator_name") or "本地标注员",
                    "annotator_name": payload.get("annotator_name") or payload.get("reviewer") or "本地标注员",
                    "remarks": str(payload.get("remarks", "")).strip(),
                }
                evidence_data_url = payload.get("evidence_data_url")
                if evidence_data_url:
                    evidence_path = save_data_url(
                        evidence_data_url,
                        ANNOTATION_IMAGE_ROOT,
                        f"{video_id}_{annotation['frame']}_{time.strftime('%Y%m%d_%H%M%S')}",
                    )
                    annotation["evidence_path"] = str(evidence_path)
                self.append_event("annotations.jsonl", annotation)
                self.send_json({"ok": True, "annotation": annotation, "message": "当前帧标注已保存，可进入质量抽检"})
                return
            if path == "/api/annotations/delete":
                raw_ids = payload.get("annotation_ids", payload.get("annotation_id", []))
                ids = [str(raw_ids)] if isinstance(raw_ids, str) else [str(item) for item in (raw_ids or [])]
                ids = [item.strip() for item in ids if item.strip()][:500]
                if not ids:
                    self.send_json({"ok": False, "message": "没有指定要删除的标注"}, 400)
                    return
                operator = str(payload.get("deleted_by") or payload.get("annotator_name") or "标注员12345").strip()
                reason = str(payload.get("reason") or "人工复审删除错误框").strip()
                existing = {item["annotation_id"]: item for item in manual_annotation_items()}
                existing.update({item["annotation_id"]: item for item in frame_annotation_items(str(payload.get("video_id") or "video_de02"), float(payload.get("video_time", 0)))})
                deleted = []
                for annotation_id in ids:
                    item = existing.get(annotation_id, {"annotation_id": annotation_id, "video_id": payload.get("video_id")})
                    self.append_event("annotation_deletions.jsonl", {"annotation_id": annotation_id, "video_id": item.get("video_id"), "source_kind": item.get("source_kind"), "deleted_by": operator, "reason": reason, "action": "delete"})
                    deleted.append({"annotation_id": annotation_id, "video_id": item.get("video_id"), "deleted_by": operator, "reason": reason})
                self.send_json({"ok": True, "deleted": deleted, "deleted_count": len(deleted), "message": f"已删除 {len(deleted)} 个标注，删除记录已留痕，可从删除记录恢复"})
                return
            if path == "/api/annotations/restore":
                raw_ids = payload.get("annotation_ids", payload.get("annotation_id", []))
                ids = [str(raw_ids)] if isinstance(raw_ids, str) else [str(item) for item in (raw_ids or [])]
                ids = [item.strip() for item in ids if item.strip()][:500]
                operator = str(payload.get("restored_by") or payload.get("annotator_name") or "标注员12345").strip()
                for annotation_id in ids:
                    self.append_event("annotation_deletions.jsonl", {"annotation_id": annotation_id, "restored_by": operator, "reason": str(payload.get("reason") or "人工复核恢复"), "action": "restore"})
                self.send_json({"ok": True, "restored_count": len(ids), "message": f"已恢复 {len(ids)} 个标注"})
                return
            if path == "/api/annotations/interpolate":
                video_id = str(payload.get("video_id") or "").strip()
                info = video_info(video_id)
                if info is None:
                    self.send_json({"ok": False, "message": "视频不存在"}, 404)
                    return
                start_frame = int(payload.get("start_frame", 0)); end_frame = int(payload.get("end_frame", start_frame))
                start_box, _ = normalize_box(payload.get("start_box"), video_id)
                end_box, _ = normalize_box(payload.get("end_box"), video_id)
                if end_frame < start_frame:
                    start_frame, end_frame, start_box, end_box = end_frame, start_frame, end_box, start_box
                span = max(1, end_frame - start_frame)
                points = []
                fps = float(info.get("fps") or 30)
                for frame in range(start_frame, end_frame + 1):
                    ratio = (frame - start_frame) / span
                    box = [round(start_box[i] + (end_box[i] - start_box[i]) * ratio, 6) for i in range(4)]
                    points.append({"frame": frame, "time_s": round(frame / fps, 4), "box": box, "source": "keyframe_interpolation", "confidence": 1.0})
                record = {"interpolation_id": f"interp:{video_id}:{start_frame}:{end_frame}:{time.time_ns()}", "video_id": video_id, "start_frame": start_frame, "end_frame": end_frame, "start_box": start_box, "end_box": end_box, "points": points, "annotator_name": payload.get("annotator_name") or "标注员12345", "remarks": str(payload.get("remarks") or "")}
                self.append_event("annotation_interpolations.jsonl", record)
                self.send_json({"ok": True, **record, "message": f"已插值 {len(points)} 个帧点，可继续拖框修改并保存轨迹"})
                return
            if path == "/api/annotations/review":
                annotation_id = str(payload.get("annotation_id", "")).strip()
                status = str(payload.get("review_status", "")).strip()
                allowed = {"pending", "human_confirmed", "rejected", "needs_correction"}
                if not annotation_id or status not in allowed:
                    self.send_json({"ok": False, "message": "审核对象或状态无效"}, 400)
                    return
                review = {
                    "annotation_id": annotation_id,
                    "review_status": status,
                    "reviewer": payload.get("reviewer", "本地质量员"),
                    "comment": payload.get("comment", ""),
                    "remarks": str(payload.get("remarks", payload.get("comment", ""))).strip(),
                }
                self.append_event("annotation_reviews.jsonl", review)
                self.send_json({"ok": True, "review": review, "message": "审核结果已留痕"})
                return
            if path == "/api/annotations/track":
                points = payload.get("points")
                if not isinstance(points, list) or not points:
                    self.send_json({"ok": False, "message": "轨迹点不能为空"}, 400)
                    return
                track = {
                    "track_id": str(payload.get("track_id") or f"track:{time.time_ns()}"),
                    "video_id": str(payload.get("video_id") or "video_de02"),
                    "label": str(payload.get("label") or "未分类目标"),
                    "start_frame": int(payload.get("start_frame", points[0].get("frame", 0))),
                    "end_frame": int(payload.get("end_frame", points[-1].get("frame", 0))),
                    "points": points[:5000],
                    "annotator_name": payload.get("annotator_name") or payload.get("reviewer") or "标注员12345",
                    "remarks": str(payload.get("remarks", "")).strip(),
                    "method": payload.get("method", "逐帧检测 + IoU连续匹配"),
                }
                self.append_event("annotation_tracks.jsonl", track)
                self.send_json({"ok": True, "track": track, "message": "轨迹已保存，可回放、编辑并导出到训练集"})
                return
            if path in {"/api/annotations/track/delete", "/api/annotations/track/segment-delete"}:
                video_id = str(payload.get("video_id") or "")
                track_id = str(payload.get("track_id") or "")
                start_frame = int(payload.get("start_frame", -1)); end_frame = int(payload.get("end_frame", start_frame))
                operator = str(payload.get("deleted_by") or payload.get("annotator_name") or "标注员12345")
                tracks = annotation_tracks(video_id)
                track = next((item for item in tracks if str(item.get("track_id")) == track_id), None)
                if not track:
                    self.send_json({"ok": False, "message": "轨迹不存在"}, 404); return
                full_delete = path == "/api/annotations/track/delete"
                points = track.get("points", []) if full_delete else [point for point in track.get("points", []) if start_frame <= int(point.get("frame", -1)) <= end_frame]
                for point in points:
                    annotation_id = f"{track_id}:{point.get('frame')}"
                    self.append_event("annotation_deletions.jsonl", {"annotation_id": annotation_id, "video_id": video_id, "track_id": track_id, "deleted_by": operator, "reason": "删除轨迹" if full_delete else f"删除轨迹区间 {start_frame}-{end_frame}", "action": "delete"})
                self.send_json({"ok": True, "deleted_count": len(points), "message": f"已删除轨迹点 {len(points)} 个"})
                return
            if path == "/api/video-library/import":
                video_id = str(payload.get("video_id", "")).strip()
                source = imported_video_path(video_id)
                if source is None:
                    self.send_json({"ok": False, "message": "素材不存在或不在允许的 8 月 20/21 日目录"}, 404)
                    return
                # 登记导入，不复制原文件；播放地址由受控媒体路由提供。
                self.append_event("video_library_imports.jsonl", {"video_id": video_id, "source_path": str(source), "media_url": f"/media-library/{video_id}", "operator": payload.get("operator", "平台管理员"), "remarks": str(payload.get("remarks", "")).strip()})
                video_library(force=True)
                self.send_json({"ok": True, "video_id": video_id, "media_url": f"/media-library/{video_id}", "message": "视频已登记到平台缓存区，可直接在网页播放；原文件未复制"})
                return
            if path == "/api/video-library/ai-annotate":
                requested = payload.get("video_ids")
                if requested == "all" or requested is None:
                    requested = [str(item.get("id")) for item in video_library().get("items", [])]
                if not isinstance(requested, list) or not requested:
                    self.send_json({"ok": False, "message": "没有可进行AI预标注的视频"}, 400)
                    return
                result = start_library_ai([str(video_id) for video_id in requested], force=bool(payload.get("force", False)))
                self.send_json({"ok": True, "items": result, "message": f"已提交 {len(result)} 个视频的AI预标注；页面会自动更新进度"})
                return
            if path == "/api/sop/save":
                if not isinstance(payload.get("steps"), list) or not payload["steps"]:
                    self.send_json({"ok": False, "message": "SOP步骤不能为空"}, 400)
                    return
                backup = RECIPE_PATH.with_suffix(f".{time.strftime('%Y%m%d_%H%M%S')}.bak.json")
                shutil.copy2(RECIPE_PATH, backup)
                recipe = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
                recipe["steps"] = payload["steps"]
                RECIPE_PATH.write_text(json.dumps(recipe, ensure_ascii=False, indent=2), encoding="utf-8")
                self.append_event("recipe_releases.jsonl", {"recipe_version": payload.get("version", "web-draft"), "steps": payload["steps"]})
                self.send_json({"ok": True, "message": "SOP草案已保存，旧版本已自动备份"})
                return
            if path == "/api/train/start":
                job_id = f"TRAIN-{time.strftime('%Y%m%d-%H%M%S')}"
                self.append_event("training_jobs.jsonl", {"job_id": job_id, "status": "待审核", **payload})
                self.send_json({"ok": True, "job_id": job_id, "message": "训练任务已登记；审核数据集后才会占用GPU"})
                return
            if path == "/api/deploy":
                release_id = f"REL-{time.strftime('%Y%m%d-%H%M%S')}"
                self.append_event("deployments.jsonl", {"release_id": release_id, "status": "灰度待确认", **payload})
                self.send_json({"ok": True, "release_id": release_id, "message": "已生成灰度下发单，需工艺/质量双人确认"})
                return
            if path == "/api/mes/test":
                event_id = f"MES-{int(time.time() * 1000)}"
                self.append_event("mes_events.jsonl", {"event_id": event_id, "ack": "SIMULATED_OK", **payload})
                self.send_json({"ok": True, "event_id": event_id, "ack": "SIMULATED_OK", "message": "本地模拟MES已确认接收"})
                return
            if path == "/api/camera/stop":
                query = parse_qs(urlparse(self.path).query)
                selected = query.get("camera", ["all"])[0]
                services = LIVE_CAMERAS.values() if selected == "all" else [camera_service(int(selected))]
                for service in services:
                    service.stop()
                self.send_json({"ok": True, "message": "实时摄像头服务已停止", "camera": selected})
                return
            if path == "/api/camera/snapshot":
                query = parse_qs(urlparse(self.path).query)
                selected = int(query.get("camera", ["0"])[0])
                result = camera_service(selected).snapshot()
                self.send_json({"ok": True, **result, "message": f"检测截图已保存到 {result['path']}"})
                return
            if path == "/api/camera/record/start":
                query = parse_qs(urlparse(self.path).query)
                selected = int(query.get("camera", ["0"])[0])
                result = camera_service(selected).start_recording()
                self.send_json({"ok": True, **result, "message": f"已开始录制，文件将保存到 {result['path']}"})
                return
            if path == "/api/camera/record/stop":
                query = parse_qs(urlparse(self.path).query)
                selected = int(query.get("camera", ["0"])[0])
                result = camera_service(selected).stop_recording()
                self.send_json({"ok": True, **result, "message": f"录制已保存：{result.get('path') or '没有有效帧'}"})
                return
            if path == "/api/network-cameras/bind":
                camera = payload.get("camera") if isinstance(payload.get("camera"), dict) else payload
                required = {"ip", "mac", "rtsp_url"}
                if not required.issubset(camera):
                    self.send_json({"ok": False, "message": "网络摄像头绑定至少需要 IP、MAC 和 RTSP 地址"}, 400)
                    return
                ip = str(camera["ip"]).strip()
                mac = str(camera["mac"]).strip().lower()
                rtsp_url = str(camera["rtsp_url"]).strip()
                if not rtsp_url.startswith(("rtsp://", "rtsps://")):
                    raise ValueError("RTSP 地址必须以 rtsp:// 或 rtsps:// 开头")
                if ip != "192.168.1.135" or mac != "c4:3c:b0:be:40:e8":
                    raise ValueError("当前绑定接口只允许修改第三摄像头的已核实身份，请先确认 IP/MAC")
                saved = json.loads(NETWORK_CAMERA_PATH.read_text(encoding="utf-8")) if NETWORK_CAMERA_PATH.exists() else {"cameras": []}
                entry = next((item for item in saved.setdefault("cameras", []) if int(item.get("id", -1)) == 2), None)
                if entry is None:
                    entry = {"id": 2}
                    saved["cameras"].append(entry)
                entry.update({**camera, "id": 2, "ip": ip, "mac": mac, "rtsp_url": rtsp_url, "rtsp_path_confirmed": bool(camera.get("rtsp_path_confirmed", False)), "status": "bound"})
                NETWORK_CAMERA_PATH.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                camera_service(2).apply_network_profile(entry)
                self.send_json({"ok": True, "camera": entry, "message": "第三摄像头身份和流地址已绑定；固定IP需在路由器DHCP静态租约中完成"})
                return
            if path == "/api/network-cameras/test":
                camera = payload.get("camera") if isinstance(payload.get("camera"), dict) else payload
                rtsp_url = str(camera.get("rtsp_url", "")).strip()
                if not rtsp_url.startswith(("rtsp://", "rtsps://")):
                    raise ValueError("RTSP 地址必须以 rtsp:// 或 rtsps:// 开头")
                endpoint = _rtsp_endpoint(rtsp_url)
                if endpoint is None:
                    raise ValueError("RTSP 地址必须包含主机名/IP，例如 rtsp://192.168.1.135:554/实际路径")
                reachable, detail = _tcp_endpoint_open(*endpoint)
                if not reachable:
                    self.send_json({"ok": False, "message": f"RTSP 设备网络不可达：{endpoint[0]}:{endpoint[1]}；{detail}。请先检查摄像头上电、Wi-Fi、固定 IP 和路由器 DHCP 租约", "rtsp_url": _redact_rtsp_url(rtsp_url)}, 422)
                    return
                try:
                    result = subprocess.run(
                        ["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-show_entries", "stream=codec_name,width,height,avg_frame_rate", "-of", "json", rtsp_url],
                        capture_output=True, text=True, timeout=12, check=False,
                    )
                except subprocess.TimeoutExpired:
                    self.send_json({"ok": False, "message": "RTSP 测试超时：设备在线但地址未返回视频流，请从摄像头 App 复制完整 RTSP 地址", "rtsp_url": _redact_rtsp_url(rtsp_url)}, 422)
                    return
                except OSError as exc:
                    self.send_json({"ok": False, "message": f"本机无法执行 ffprobe：{exc}", "rtsp_url": _redact_rtsp_url(rtsp_url)}, 503)
                    return
                if result.returncode != 0:
                    message = (result.stderr or "RTSP 无视频流").strip().splitlines()[-1]
                    self.send_json({"ok": False, "message": message, "rtsp_url": _redact_rtsp_url(rtsp_url)}, 422)
                    return
                self.send_json({"ok": True, "message": "RTSP 视频流测试通过，可保存绑定", "rtsp_url": rtsp_url, "streams": json.loads(result.stdout or "{}")})
                return
            if path == "/api/camera/start":
                query = parse_qs(urlparse(self.path).query)
                selected = query.get("camera", ["all"])[0]
                services = LIVE_CAMERAS.values() if selected == "all" else [camera_service(int(selected))]
                results = []
                for service in services:
                    try:
                        service.start()
                    except Exception as exc:
                        results.append({"camera": service.camera_id, "ok": False, "error": str(exc)})
                    else:
                        results.append({"camera": service.camera_id, "ok": True})
                self.send_json({"ok": all(item["ok"] for item in results), "camera": selected, "results": results}, 200)
                return
            if path == "/api/decision/review":
                self.append_event("decision_reviews.jsonl", payload)
                self.send_json({"ok": True, "message": "人工复核意见已保存并进入审计记录"})
                return
            self.send_json({"ok": False, "message": "接口不存在"}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "message": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"ok": False, "message": f"服务异常：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
    mimetypes.add_type("video/mp4", ".mp4")
    host, port = os.getenv("SOP_HOST", "0.0.0.0"), int(os.getenv("SOP_PORT", "8096"))
    print(f"宁波SOP分析平台已启动：http://127.0.0.1:{port}")
    print(f"局域网访问地址：http://{primary_lan_address()}:{port}")
    print(f"证据保存目录：{EVIDENCE_ROOT}")
    print("按 Ctrl+C 停止服务")
    ThreadingHTTPServer((host, port), SOPHandler).serve_forever()


if __name__ == "__main__":
    main()
