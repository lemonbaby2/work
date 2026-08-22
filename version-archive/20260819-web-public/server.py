from __future__ import annotations

import json
import base64
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
from urllib.parse import parse_qs, quote, unquote, urlparse


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DATA_ROOT = WEB_ROOT / "data"
RUNTIME_ROOT = ROOT / "runtime"
RECIPE_PATH = ROOT / "config" / "sop_recipe.json"
ALGORITHM_COMPARISON_PATH = ROOT / "config" / "algorithm_comparison.json"
NETWORK_CAMERA_PATH = ROOT / "config" / "network_cameras.json"
FRAME_CACHE: dict[str, list[dict]] = {}
DESKTOP_ROOT = Path(os.getenv("SOP_DESKTOP_DIR", "/home/xjai/Desktop/sop xjai"))
EVIDENCE_ROOT = DESKTOP_ROOT / "摄像头证据"
ANNOTATION_IMAGE_ROOT = DESKTOP_ROOT / "标注图片"
DEFAULT_CAMERA_SOURCES = {
    0: "/dev/v4l/by-id/usb-Jieli_Technology_USB_Composite_Device-video-index0",
    1: "/dev/v4l/by-id/usb-webcamvendor_webcamproduct_00000000-video-index0",
}


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


class LiveCameraService:
    """按需启动的 YOLOv11 摄像头 MJPEG 服务。"""

    def __init__(self, camera_id: int | None = None) -> None:
        self.camera_id = int(os.getenv("SOP_CAMERA_ID", "0")) if camera_id is None else camera_id
        configured_source = os.getenv(f"SOP_CAMERA_SOURCE_{self.camera_id}") or None
        if configured_source is None and self.camera_id == 0:
            configured_source = os.getenv("SOP_CAMERA_SOURCE")
        network_profile = dict(next((item for item in network_camera_profiles() if int(item.get("id", -1)) == self.camera_id), {}))
        if self.camera_id == 2 and configured_source and str(configured_source).lower().startswith(("rtsp://", "rtsps://")):
            network_profile["rtsp_path_confirmed"] = True
            network_profile["status"] = "temporary_env_override"
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
    return json.loads((DATA_ROOT / "videos.json").read_text(encoding="utf-8"))


def frame_records(video_id: str, kind: str = "detections") -> list[dict]:
    key = f"{video_id}:{kind}"
    if key in FRAME_CACHE:
        return FRAME_CACHE[key]
    if kind == "candidates":
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


def frame_annotation_items(video_id: str, requested_time: float) -> list[dict]:
    info = video_info(video_id)
    if info is None:
        raise ValueError("视频不存在")
    frame = min(max(0, int(round(requested_time * float(info.get("fps", 30))))), int(info.get("frames", 1)) - 1)
    width, height = video_size(video_id)
    reviews = annotation_reviews()
    items: list[dict] = []
    sources = (("prelabel", frame_records(video_id), "detections"), ("candidate", frame_records(video_id, "candidates"), "candidates"))
    for source_kind, records, field in sources:
        if not records:
            continue
        record = record_for_frame(records, frame)
        for index, detection in enumerate(record.get(field, [])):
            annotation_id = f"{video_id}:{source_kind}:{record.get('frame', frame)}:{index}"
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
            })
    return items


def manual_annotation_items(video_id: str | None = None) -> list[dict]:
    reviews = annotation_reviews()
    items = []
    for record in read_jsonl(RUNTIME_ROOT / "annotations.jsonl"):
        record_video_id = str(record.get("video_id") or video_id_for_source(record.get("video")))
        if video_id and record_video_id != video_id:
            continue
        normalized, pixels = normalize_box(record.get("box"), record_video_id)
        annotation_id = str(record.get("annotation_id") or f"manual:{record_video_id}:{record.get('_line')}")
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
        if path == "/api/videos":
            self.send_json(video_catalog())
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
            visual_signal = round(min(100.0, evidence_score), 1)
            temporal_signal = round(min(100.0, 72.0 + completed * 4.0), 1)
            tool_signal = 100.0 if has_tool else 0.0
            mes_signal = 0.0
            gate_status = {
                "视觉检测": {"score": visual_signal, "state": "pass" if visual_signal >= 55 else "watch", "detail": "当前帧存在可解释检测证据" if visual_signal >= 55 else "当前帧证据不足"},
                "时序一致性": {"score": temporal_signal, "state": "pass" if temporal_signal >= 70 else "watch", "detail": f"已完成 {completed}/{len(video['steps'])} 个步骤"},
                "工具/PSet": {"score": tool_signal, "state": "pass" if tool_signal else "blocked", "detail": "已看到电动紧固工具" if has_tool else "未接入电批扭矩/角度回执"},
                "MES回执": {"score": mes_signal, "state": "pass" if mes_signal else "blocked", "detail": "当前为本地演示，未收到生产 MES ACK"},
            }
            timeline = [{
                "id": item["id"],
                "label": item["label"],
                "state": "done" if requested_time >= float(item["end_s"]) else ("current" if item["id"] == step["id"] else "pending"),
                "progress": round(min(100.0, max(0.0, (requested_time - float(item["start_s"])) / max(0.1, float(item["end_s"]) - float(item["start_s"])) * 100)), 1),
            } for item in video["steps"]]
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
                "gate_status": gate_status,
                "timeline": timeline,
                "evidence_breakdown": {"visual": visual_signal, "temporal": temporal_signal, "tool": tool_signal, "mes": mes_signal},
                "release_readiness": round(sum(item["score"] for item in gate_status.values()) / len(gate_status), 1),
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
        if path == "/api/mes/schema":
            self.send_json({
                "ok": True,
                "protocols": ["HTTPS JSON", "MQTT QoS1", "OPC UA adapter"],
                "required_fields": ["event_id", "work_order", "product_sn", "station_id", "recipe_version", "steps", "tool_results", "final_result", "evidence", "model_version", "occurred_at"],
                "state_machine": ["LOCAL_PENDING", "SENT", "ACKED", "RETRYING", "HOLD", "MANUAL_RELEASE"],
                "guarantees": ["幂等键 event_id + product_sn + station_id", "断网本地队列", "指数退避重试", "ACK 超时转 HOLD", "全链路审计"],
            })
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

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if os.getenv("SOP_PUBLIC_READONLY", "0") == "1":
            self.send_json({"ok": False, "message": "公网展示实例为只读模式；请在本机或内网部署实例执行写入和设备控制操作"}, HTTPStatus.FORBIDDEN)
            return
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
                    "reviewer": payload.get("reviewer", "本地标注员"),
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
                }
                self.append_event("annotation_reviews.jsonl", review)
                self.send_json({"ok": True, "review": review, "message": "审核结果已留痕"})
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
                required = {"product_sn", "station_id", "final_result"}
                if not required.issubset(payload):
                    self.send_json({"ok": False, "message": "MES 模拟报文缺少产品SN、工位或最终结果"}, 400)
                    return
                event_id = str(payload.get("event_id") or f"MES-{int(time.time() * 1000)}")
                event = {
                    "event_id": event_id,
                    "idempotency_key": f"{event_id}:{payload['product_sn']}:{payload['station_id']}",
                    "ack": "SIMULATED_OK",
                    "state": "ACKED",
                    "transport": payload.get("transport", "HTTPS JSON"),
                    "retry_count": 0,
                    "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    **payload,
                }
                self.append_event("mes_events.jsonl", event)
                self.send_json({"ok": True, **event, "message": "本地模拟 MES 已确认接收，事件已写入审计队列"})
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
                if ip not in {"192.168.1.135", "192.168.188.2"} or mac != "c4:3c:b0:be:40:e8":
                    raise ValueError("当前绑定接口只允许修改第三摄像头的已核实身份；可用访问地址为 192.168.1.135（厂内网）或 192.168.188.2（摄像头 AP 配置网段）")
                saved = json.loads(NETWORK_CAMERA_PATH.read_text(encoding="utf-8")) if NETWORK_CAMERA_PATH.exists() else {"cameras": []}
                entry = next((item for item in saved.setdefault("cameras", []) if int(item.get("id", -1)) == 2), None)
                if entry is None:
                    entry = {"id": 2}
                    saved["cameras"].append(entry)
                entry.update({**camera, "id": 2, "ip": ip, "mac": mac, "rtsp_url": rtsp_url, "rtsp_path_confirmed": bool(camera.get("rtsp_path_confirmed", False)), "status": "bound", "network_mode": "camera_ap" if ip == "192.168.188.2" else "factory_lan"})
                NETWORK_CAMERA_PATH.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                camera_service(2).apply_network_profile(entry)
                self.send_json({"ok": True, "camera": entry, "message": "第三摄像头身份和流地址已绑定；固定IP需在路由器DHCP静态租约中完成"})
                return
            if path == "/api/network-cameras/discover":
                camera = payload.get("camera") if isinstance(payload.get("camera"), dict) else payload
                host = str(camera.get("ip", "192.168.1.135")).strip()
                port = int(camera.get("port", 554))
                username = str(camera.get("username", "")).strip()
                password = str(camera.get("password", ""))
                candidates = camera.get("paths") or ["/", "/live/ch00_0", "/live/ch00_1", "/live.sdp", "/stream", "/h264Preview_01_main", "/Streaming/Channels/101", "/Streaming/Channels/102", "/cam/realmonitor?channel=1&subtype=0", "/11", "/12"]
                if not isinstance(candidates, list):
                    raise ValueError("候选 RTSP 路径必须是数组")
                results = []
                for raw_path in candidates[:24]:
                    raw_path = str(raw_path).strip() or "/"
                    if not raw_path.startswith("/"):
                        raw_path = "/" + raw_path
                    auth = f"{quote(username, safe='')}:{quote(password, safe='')}@" if username else ""
                    candidate_url = f"rtsp://{auth}{host}:{port}{raw_path}"
                    endpoint_ok, detail = _tcp_endpoint_open(host, port, timeout=1.5)
                    if not endpoint_ok:
                        results.append({"url": _redact_rtsp_url(candidate_url), "ok": False, "stage": "tcp", "message": detail})
                        break
                    try:
                        probe = subprocess.run(["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-show_entries", "stream=codec_name,width,height,avg_frame_rate", "-of", "json", candidate_url], capture_output=True, text=True, timeout=6, check=False)
                    except subprocess.TimeoutExpired:
                        results.append({"url": _redact_rtsp_url(candidate_url), "ok": False, "stage": "timeout", "message": "读取超时"})
                        continue
                    item = {"url": _redact_rtsp_url(candidate_url), "ok": probe.returncode == 0, "stage": "rtsp", "message": (probe.stderr or "").strip().splitlines()[-1] if probe.returncode else "视频流可读"}
                    if probe.returncode == 0:
                        item["streams"] = json.loads(probe.stdout or "{}")
                    results.append(item)
                self.send_json({"ok": any(item.get("ok") for item in results), "host": host, "port": port, "results": results, "message": "发现可用 RTSP 路径" if any(item.get("ok") for item in results) else "未发现可用候选路径；请从厂家 App 复制完整地址和认证信息"})
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
