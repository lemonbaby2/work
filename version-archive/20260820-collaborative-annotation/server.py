from __future__ import annotations

import json
import base64
import importlib.util
import math
import mimetypes
import os
import shutil
import socket
import sqlite3
import secrets
import hashlib
import subprocess
import sys
import threading
import time
from bisect import bisect_left
from shutil import which
from urllib.request import Request, urlopen
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
TRAINING_CATALOG_PATH = ROOT / "config" / "training_catalog.json"
DATASET_CATALOG_PATH = ROOT / "config" / "dataset_catalog.json"
PRODUCTION_LINES_PATH = ROOT / "config" / "production_lines.json"
SPARK_DEPLOYMENT_PATH = ROOT / "config" / "spark_deployment.json"
ACTIVE_LINE_ID = os.getenv("SOP_PRODUCTION_LINE", "pcb")
SPARK_MODEL_ROOT = Path(os.getenv("SOP_SPARK_MODEL_DIR", "/home/xjai/sop-model-store"))
COLLAB_DB_PATH = Path(os.getenv("SOP_COLLAB_DB", str(RUNTIME_ROOT / "collaboration.sqlite3")))
COLLAB_SESSION_TTL = int(os.getenv("SOP_COLLAB_SESSION_TTL", str(12 * 3600)))
COLLAB_DEFAULT_PASSWORD = os.getenv("SOP_DEFAULT_PASSWORD", "change-me-now")
COLLAB_ROLES = {"admin", "reviewer", "annotator", "viewer"}
CUSTOM_LABELS_PATH = Path(os.getenv("SOP_CUSTOM_LABELS", str(RUNTIME_ROOT / "custom_labels.json")))


def deployed_model(filename: str) -> Path:
    spark_path = SPARK_MODEL_ROOT / filename
    return spark_path if spark_path.exists() else ROOT / "models" / filename


LINE_MODEL_PATHS = {
    "pcb": deployed_model("yolo26n.pt"),
    "automotive": deployed_model("yolo26n_两视频小目标蒸馏_待人工验收.pt"),
    "molding-coating": deployed_model("yolo26n.pt"),
}
FRAME_CACHE: dict[str, list[dict]] = {}
DESKTOP_ROOT = Path(os.getenv("SOP_DESKTOP_DIR", "/home/xjai/Desktop/sop xjai"))
EVIDENCE_ROOT = DESKTOP_ROOT / "摄像头证据"
ANNOTATION_IMAGE_ROOT = DESKTOP_ROOT / "标注图片"
CAMERA_SLOT_COUNT = max(1, int(os.getenv("SOP_CAMERA_COUNT", "4")))


def _collab_connection() -> sqlite3.Connection:
    COLLAB_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(COLLAB_DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 180_000)
    return f"pbkdf2_sha256$180000${salt.hex()}${digest.hex()}"


def _password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds))
        return secrets.compare_digest(candidate.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def init_collaboration_db() -> None:
    with _collab_connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          username TEXT PRIMARY KEY,
          display_name TEXT NOT NULL,
          role TEXT NOT NULL,
          password_hash TEXT NOT NULL,
          disabled INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token TEXT PRIMARY KEY,
          username TEXT NOT NULL,
          expires_at REAL NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS annotation_versions (
          annotation_id TEXT PRIMARY KEY,
          video_id TEXT NOT NULL,
          frame INTEGER NOT NULL,
          payload TEXT NOT NULL,
          version INTEGER NOT NULL DEFAULT 1,
          updated_by TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS annotation_locks (
          lock_key TEXT PRIMARY KEY,
          username TEXT NOT NULL,
          expires_at REAL NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT NOT NULL,
          action TEXT NOT NULL,
          object_id TEXT,
          detail TEXT,
          recorded_at TEXT NOT NULL
        );
        """)
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            db.executemany(
                "INSERT INTO users(username, display_name, role, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                [
                    ("admin", "平台管理员", "admin", _password_hash(COLLAB_DEFAULT_PASSWORD), now),
                    ("annotator", "标注员", "annotator", _password_hash(COLLAB_DEFAULT_PASSWORD), now),
                    ("reviewer", "质量复核员", "reviewer", _password_hash(COLLAB_DEFAULT_PASSWORD), now),
                ],
            )


def _collab_user_from_token(token: str | None) -> dict[str, object] | None:
    if not token:
        return None
    now = time.time()
    with _collab_connection() as db:
        row = db.execute(
            "SELECT u.username, u.display_name, u.role FROM sessions s JOIN users u ON u.username=s.username "
            "WHERE s.token=? AND s.expires_at>? AND u.disabled=0", (token, now)
        ).fetchone()
    return dict(row) if row else None


def _request_token(handler: SimpleHTTPRequestHandler) -> str | None:
    header = handler.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    cookie = handler.headers.get("Cookie", "")
    for item in cookie.split(";"):
        name, _, value = item.strip().partition("=")
        if name == "sop_session":
            return value
    return None


def _collab_user(handler: SimpleHTTPRequestHandler) -> dict[str, object] | None:
    return _collab_user_from_token(_request_token(handler))


def _require_collab_user(handler: SimpleHTTPRequestHandler, roles: set[str] | None = None) -> dict[str, object] | None:
    user = _collab_user(handler)
    if not user:
        handler.send_json({"ok": False, "message": "请先登录协同标注工作区"}, 401)
        return None
    if roles and user["role"] not in roles and user["role"] != "admin":
        handler.send_json({"ok": False, "message": "当前账号没有执行此操作的权限"}, 403)
        return None
    return user


def _collab_audit(username: str, action: str, object_id: str = "", detail: object = None) -> None:
    with _collab_connection() as db:
        db.execute(
            "INSERT INTO audit_log(username, action, object_id, detail, recorded_at) VALUES (?, ?, ?, ?, ?)",
            (username, action, object_id, json.dumps(detail, ensure_ascii=False) if detail is not None else "", time.strftime("%Y-%m-%d %H:%M:%S")),
        )


def collab_auth_login(username: str, password: str) -> tuple[dict[str, object] | None, str | None]:
    with _collab_connection() as db:
        row = db.execute("SELECT * FROM users WHERE username=? AND disabled=0", (username,)).fetchone()
        if not row or not _password_matches(password, row["password_hash"]):
            return None, None
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO sessions(token, username, expires_at, created_at) VALUES (?, ?, ?, ?)", (token, username, time.time() + COLLAB_SESSION_TTL, time.strftime("%Y-%m-%d %H:%M:%S")))
        user = {"username": row["username"], "display_name": row["display_name"], "role": row["role"]}
    _collab_audit(username, "login")
    return user, token


def collab_lock_key(video_id: str, frame: int) -> str:
    return f"{video_id}:frame:{int(frame)}"


def collab_lock_state(video_id: str, frame: int, username: str | None = None) -> dict[str, object]:
    key = collab_lock_key(video_id, frame)
    now = time.time()
    with _collab_connection() as db:
        db.execute("DELETE FROM annotation_locks WHERE expires_at<=?", (now,))
        row = db.execute("SELECT lock_key, username, expires_at FROM annotation_locks WHERE lock_key=?", (key,)).fetchone()
    return {"locked": bool(row), "lock_key": key, "username": row["username"] if row else None, "mine": bool(row and username and row["username"] == username), "expires_at": row["expires_at"] if row else None}


def collab_annotations(video_id: str | None = None) -> list[dict]:
    with _collab_connection() as db:
        rows = db.execute("SELECT annotation_id, payload, version, updated_by, updated_at FROM annotation_versions ORDER BY updated_at").fetchall()
    output = []
    for row in rows:
        try:
            item = json.loads(row["payload"])
        except json.JSONDecodeError:
            continue
        if video_id and str(item.get("video_id")) != video_id:
            continue
        item["version"] = row["version"]
        item["updated_by"] = row["updated_by"]
        item["updated_at"] = row["updated_at"]
        output.append(item)
    return output


def custom_labels() -> dict[str, list[str]]:
    value = read_config(CUSTOM_LABELS_PATH, {})
    return value if isinstance(value, dict) else {}


def save_custom_labels(line_id: str, labels: object) -> list[str]:
    if not isinstance(labels, list):
        raise ValueError("标签必须是数组")
    cleaned: list[str] = []
    for value in labels:
        label = " ".join(str(value).strip().split())
        if label and label not in cleaned:
            if len(label) > 64:
                raise ValueError("标签名称不能超过64个字符")
            cleaned.append(label)
    if not cleaned:
        raise ValueError("至少需要保留一个标签")
    labels_map = custom_labels()
    labels_map[line_id] = cleaned
    CUSTOM_LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_LABELS_PATH.write_text(json.dumps(labels_map, ensure_ascii=False, indent=2), encoding="utf-8")
    return cleaned


def acquire_collab_lock(video_id: str, frame: int, username: str, ttl: int = 900) -> dict[str, object]:
    key = collab_lock_key(video_id, frame)
    now = time.time()
    expires = now + max(60, min(ttl, 3600))
    with _collab_connection() as db:
        db.execute("DELETE FROM annotation_locks WHERE expires_at<=?", (now,))
        row = db.execute("SELECT username, expires_at FROM annotation_locks WHERE lock_key=?", (key,)).fetchone()
        if row and row["username"] != username:
            return {"ok": False, "locked": True, "lock_key": key, "username": row["username"], "mine": False, "expires_at": row["expires_at"], "message": f"此关键帧正在被 {row['username']} 编辑"}
        db.execute("INSERT OR REPLACE INTO annotation_locks(lock_key, username, expires_at, updated_at) VALUES (?, ?, ?, ?)", (key, username, expires, time.strftime("%Y-%m-%d %H:%M:%S")))
    _collab_audit(username, "lock", key)
    return {"ok": True, **collab_lock_state(video_id, frame, username), "message": "已获得关键帧编辑锁，15分钟内自动续期"}


def release_collab_lock(video_id: str, frame: int, username: str) -> dict[str, object]:
    key = collab_lock_key(video_id, frame)
    with _collab_connection() as db:
        db.execute("DELETE FROM annotation_locks WHERE lock_key=? AND username=?", (key, username))
    _collab_audit(username, "unlock", key)
    return {"ok": True, **collab_lock_state(video_id, frame, username)}


def save_collab_annotation(annotation: dict, username: str, expected_version: int | None = None) -> dict[str, object]:
    annotation_id = str(annotation["annotation_id"])
    video_id = str(annotation["video_id"])
    frame = int(annotation["frame"])
    lock = collab_lock_state(video_id, frame, username)
    if lock["locked"] and not lock["mine"]:
        raise PermissionError(f"此关键帧正在被 {lock['username']} 编辑")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _collab_connection() as db:
        row = db.execute("SELECT version FROM annotation_versions WHERE annotation_id=?", (annotation_id,)).fetchone()
        current = int(row["version"]) if row else 0
        if expected_version is not None and current != int(expected_version):
            raise RuntimeError(f"标注已被其他人员更新，请刷新后再保存（当前版本 {current}）")
        version = current + 1
        db.execute(
            "INSERT INTO annotation_versions(annotation_id, video_id, frame, payload, version, updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(annotation_id) DO UPDATE SET payload=excluded.payload, version=excluded.version, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (annotation_id, video_id, frame, json.dumps(annotation, ensure_ascii=False), version, username, now),
        )
    annotation["version"] = version
    annotation["updated_by"] = username
    annotation["updated_at"] = now
    _collab_audit(username, "annotation_save", annotation_id, {"version": version, "frame": frame})
    return annotation


init_collaboration_db()


def _discover_camera_sources(limit: int = CAMERA_SLOT_COUNT) -> dict[int, str]:
    """Return stable camera sources ordered by availability.

    Prefer /dev/v4l/by-id symlinks and keep the first `limit` distinct targets.
    """
    by_id_dir = Path("/dev/v4l/by-id")
    candidates: list[str] = []
    used_targets: set[str] = set()
    if by_id_dir.exists():
        for path in sorted(by_id_dir.glob("*-video-index0")):
            try:
                resolved = str(path.resolve())
            except OSError:
                continue
            if resolved not in used_targets:
                used_targets.add(resolved)
                candidates.append(str(path))
            if len(candidates) >= limit:
                break
    if len(candidates) < limit:
        for path in sorted(Path("/dev").glob("video*")):
            if not path.name[5:].isdigit():
                continue
            index_path = Path(f"/sys/class/video4linux/{path.name}/index")
            if index_path.exists() and index_path.read_text(encoding="utf-8", errors="ignore").strip() != "0":
                continue
            resolved = str(path.resolve())
            if resolved not in used_targets:
                used_targets.add(resolved)
                candidates.append(str(path))
            if len(candidates) >= limit:
                break
    return {index: source for index, source in enumerate(candidates[:limit])}


DEFAULT_CAMERA_SOURCES = _discover_camera_sources()
CAMERA_CAPABILITY_CACHE: dict[str, tuple[float, dict[str, object]]] = {}
CAMERA_CAPABILITY_CACHE_TTL = float(os.getenv("SOP_CAMERA_CAPABILITY_CACHE_TTL", "60"))


def _v4l2_output(device: str, *arguments: str) -> str:
    if which("v4l2-ctl") is None:
        return ""
    try:
        result = subprocess.run(["v4l2-ctl", "-d", device, *arguments], capture_output=True, text=True, timeout=2.5, check=False)
        return result.stdout or result.stderr
    except (OSError, subprocess.TimeoutExpired):
        return ""


def camera_capabilities(device: str) -> dict[str, object]:
    cached = CAMERA_CAPABILITY_CACHE.get(device)
    if cached and time.monotonic() - cached[0] < CAMERA_CAPABILITY_CACHE_TTL:
        return cached[1]
    formats = _v4l2_output(device, "--list-formats-ext")
    controls = _v4l2_output(device, "--list-ctrls-menus")
    current = _v4l2_output(device, "--get-fmt-video", "--get-parm")
    modes: list[dict[str, object]] = []
    pixel_format = ""
    size = ""
    for raw in formats.splitlines():
        line = raw.strip()
        if line.startswith("[") and "'" in line:
            pixel_format = line.split("'", 2)[1]
        elif line.startswith("Size: Discrete "):
            size = line.removeprefix("Size: Discrete ")
        elif line.startswith("Interval: Discrete ") and pixel_format and size:
            fps_text = line.rsplit("(", 1)[-1].split(" fps", 1)[0]
            try:
                fps = float(fps_text)
            except ValueError:
                continue
            modes.append({"pixel_format": pixel_format, "size": size, "fps": fps})
    control_items = []
    for raw in controls.splitlines():
        line = raw.strip()
        if " 0x" not in line or " : " not in line:
            continue
        name, details = line.split(" 0x", 1)
        control_items.append({"name": name.strip(), "details": details.split(" : ", 1)[-1]})
    result = {"modes": modes, "controls": control_items, "current": current.strip(), "formats_raw": formats.strip()}
    CAMERA_CAPABILITY_CACHE[device] = (time.monotonic(), result)
    return result


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
    probe_path = ROOT / "qa" / "insta360_link2c_capabilities.json"
    try:
        probe_report = json.loads(probe_path.read_text(encoding="utf-8")) if probe_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        probe_report = {}
    by_id = sorted(Path("/dev/v4l/by-id").glob("*-video-index0"))
    stable_by_target = {str(path.resolve()): str(path) for path in by_id if path.exists()}
    for device in sorted(Path("/dev").glob("video*")):
        if not device.name[5:].isdigit():
            continue
        number = device.name[5:]
        properties = _udev_properties(str(device))
        sysfs_name = Path(f"/sys/class/video4linux/video{number}/name")
        name = sysfs_name.read_text(encoding="utf-8", errors="replace").strip() if sysfs_name.exists() else device.name
        index_path = Path(f"/sys/class/video4linux/{device.name}/index")
        is_primary = str(device) in stable_by_target or (index_path.exists() and index_path.read_text(encoding="utf-8", errors="ignore").strip() == "0")
        capability = camera_capabilities(str(device)) if is_primary else {"modes": [], "controls": [], "current": ""}
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
            "video_capture": bool(capability.get("modes")),
            "network_address": None,
            "is_primary_stream": is_primary,
            "capabilities": capability,
            "probe_report": probe_report if str(device) == str(probe_report.get("device")) else None,
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
    camera_sources = []
    for camera_id, source in DEFAULT_CAMERA_SOURCES.items():
        try:
            resolved = str(Path(source).resolve())
        except OSError:
            resolved = source
        matched = next((item for item in videos if item.get("device") == resolved), None)
        camera_sources.append({"camera_id": camera_id, "camera_name": matched.get("name") if matched else f"摄像头{camera_id}", "source": source})
    return {
        "ok": True,
        "host": socket.gethostname(),
        "videos": videos,
        "serials": serials,
        "network": interfaces,
        "camera_network_note": "当前摄像头通过 USB/UVC 接入，不存在可分配的摄像头 MAC/IP；页面显示的是采集主机网络地址。若要固定摄像头 IP，需使用网口/GigE 或 RTSP 摄像机并在交换机/DHCP 中做保留。",
        "desktop_root": str(DESKTOP_ROOT),
        "camera_sources": camera_sources,
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
        configured_source = os.getenv(f"SOP_CAMERA_SOURCE_{self.camera_id}")
        if configured_source is None and self.camera_id == 0:
            configured_source = os.getenv("SOP_CAMERA_SOURCE")
        self.source = configured_source or DEFAULT_CAMERA_SOURCES.get(self.camera_id, "")
        default_model = LINE_MODEL_PATHS.get(ACTIVE_LINE_ID, ROOT / "models" / "yolo11n.pt")
        self.model_path = Path(os.getenv("SOP_CAMERA_MODEL", str(default_model)))
        self.device = os.getenv("SOP_CAMERA_DEVICE", "0")
        self.confidence = float(os.getenv("SOP_CAMERA_CONFIDENCE", "0.35"))
        self.max_fps = float(os.getenv("SOP_CAMERA_MAX_FPS", "15"))
        link2c = "Insta360" in self.source
        self.width = int(os.getenv(f"SOP_CAMERA_WIDTH_{self.camera_id}", os.getenv("SOP_CAMERA_WIDTH", "1920" if link2c else "1280")))
        self.height = int(os.getenv(f"SOP_CAMERA_HEIGHT_{self.camera_id}", os.getenv("SOP_CAMERA_HEIGHT", "1080" if link2c else "720")))
        self.capture_fps = float(os.getenv(f"SOP_CAMERA_CAPTURE_FPS_{self.camera_id}", "30"))
        self.stream_width = max(0, int(os.getenv("SOP_CAMERA_STREAM_WIDTH", "1280")))
        self.jpeg_quality = min(95, max(40, int(os.getenv("SOP_CAMERA_JPEG_QUALITY", "72"))))
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
            "model": self.model_path.stem,
            "camera_id": self.camera_id,
            "camera_name": f"摄像头{self.camera_id}",
            "model_path": str(self.model_path),
            "source": self.source,
            "pixel_format": "MJPG",
            "capture_fps": self.capture_fps,
            "stream_width": self.stream_width,
            "jpeg_quality": self.jpeg_quality,
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

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            try:
                import cv2
                from ultralytics import YOLO

                if not self.source:
                    raise RuntimeError(f"摄像头槽位{self.camera_id}尚未绑定视频采集设备")
                if not self.model_path.exists():
                    raise FileNotFoundError(f"YOLOv11模型不存在: {self.model_path}")
                capture = cv2.VideoCapture(self._source(self.source))
                if not capture.isOpened():
                    raise RuntimeError(f"无法打开摄像头: {self.source}")
                self._capture = capture
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                capture.set(cv2.CAP_PROP_FPS, min(self.capture_fps or 30, 30))
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
                quantize = None if self.device == "cpu" else 16
                result = model.predict(source=frame, imgsz=640, conf=self.confidence, device=self.device, quantize=quantize, max_det=50, verbose=False)[0]
                annotated = result.plot()
                detections = len(result.boxes) if result.boxes is not None else 0
                inference_ms = (time.perf_counter() - started) * 1000
                stream_frame = annotated
                if self.stream_width and annotated.shape[1] > self.stream_width:
                    stream_height = round(annotated.shape[0] * self.stream_width / annotated.shape[1])
                    stream_frame = cv2.resize(annotated, (self.stream_width, stream_height), interpolation=cv2.INTER_AREA)
                encoded, buffer = cv2.imencode(".jpg", stream_frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
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
                        "stream_output": f"{stream_frame.shape[1]}x{stream_frame.shape[0]}",
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


LIVE_CAMERAS = {camera_id: LiveCameraService(camera_id) for camera_id in range(CAMERA_SLOT_COUNT)}


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
            available = probe_url(cvat_url)
            result.append({"name": name, "purpose": purpose, "installed": available, "endpoint": cvat_url, "note": "服务在线" if available else "建议厂内自托管"})
        else:
            installed = bool(importlib.util.find_spec(module)) if module else bool(which(command))
            result.append({"name": name, "purpose": purpose, "installed": installed, "endpoint": None, "note": "可用" if installed else "未安装"})
    return result


def read_config(path: Path, fallback: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def cvat_config() -> dict[str, object]:
    url = os.getenv("CVAT_URL", "http://127.0.0.1:8081").rstrip("/")
    token = os.getenv("CVAT_TOKEN", "").strip()
    return {"url": url, "token_configured": bool(token)}


def probe_url(url: str) -> bool:
    try:
        with urlopen(url, timeout=0.8) as response:
            return 200 <= response.status < 500
    except Exception:
        return False


def cvat_task_create(payload: dict[str, object]) -> dict[str, object]:
    config = cvat_config()
    url = str(config["url"])
    token = os.getenv("CVAT_TOKEN", "").strip()
    task_name = str(payload.get("name") or "宁波模塑 SOP 标注任务").strip()
    labels = payload.get("labels") or []
    if not isinstance(labels, list) or not labels:
        raise ValueError("CVAT任务至少需要一个标签")
    if not token:
        return {
            "ok": True,
            "mode": "link",
            "task_name": task_name,
            "url": f"{url}/tasks",
            "message": "已生成CVAT任务入口；配置 CVAT_TOKEN 后可由平台自动创建任务",
        }
    body = json.dumps({"name": task_name, "labels": [{"name": str(label)} for label in labels]}).encode("utf-8")
    request = Request(f"{url}/api/tasks", data=body, method="POST", headers={"Content-Type": "application/json", "Authorization": f"Token {token}"})
    with urlopen(request, timeout=8) as response:
        result = json.loads(response.read().decode("utf-8"))
    task_id = result.get("id")
    return {"ok": True, "mode": "api", "task_id": task_id, "url": f"{url}/tasks/{task_id}", "message": "CVAT任务已创建"}


def production_lines() -> list[dict[str, object]]:
    value = read_config(PRODUCTION_LINES_PATH, [])
    if not isinstance(value, list):
        return []
    labels_map = custom_labels()
    return [{**line, "labels": labels_map.get(str(line.get("id")), line.get("labels", []))} for line in value]


def active_production_line() -> dict[str, object] | None:
    return next((line for line in production_lines() if line.get("id") == ACTIVE_LINE_ID), None)


def select_production_line(line_id: str) -> dict[str, object]:
    global ACTIVE_LINE_ID
    line = next((item for item in production_lines() if item.get("id") == line_id), None)
    if line is None:
        raise ValueError(f"未知产线: {line_id}")
    ACTIVE_LINE_ID = line_id
    model_path = LINE_MODEL_PATHS.get(line_id, ROOT / "models" / "yolo11n.pt")
    for service in LIVE_CAMERAS.values():
        service.stop()
        service.model_path = model_path
        service._status.update({"model_path": str(model_path), "model": line.get("primary_model", "YOLO")})
    return line


DATASET_LOCAL_HINTS = {
    "pcb-components": [WEB_ROOT / "assets" / "datasets" / "pcb"],
    "automotive-fasteners": [ROOT / "datasets" / "新增两视频_YOLOE26_SAHI细粒度预标注_待人工复核", ROOT / "datasets" / "三视频多物体预标注_待人工复核"],
    "automotive-sop-ng": [DATA_ROOT],
}


def dataset_catalog_with_status() -> list[dict[str, object]]:
    catalog = read_config(DATASET_CATALOG_PATH, [])
    if not isinstance(catalog, list):
        return []
    output = []
    for item in catalog:
        paths = [path for path in DATASET_LOCAL_HINTS.get(str(item.get("id")), []) if path.exists()]
        images = sum(1 for path in paths for suffix in ("*.jpg", "*.jpeg", "*.png") for _ in path.rglob(suffix))
        labels = sum(1 for path in paths for _ in path.rglob("*.txt"))
        if item.get("id") == "pcb-components" and images:
            local_state = "preview-only"
            local_message = f"已嵌入{images}张来源预览图，尚不是CVAT真值训练集"
        elif images and labels:
            local_state = "pending-review"
            local_message = f"本地{images}张图片/{labels}份标签，自动预标注待人工复核"
        elif paths:
            local_state = "local-source"
            local_message = "已有本地视频/事件来源，待按产品SN生成正式数据集"
        else:
            local_state = "source-indexed"
            local_message = "已登记公开来源，尚未下载或导入"
        output.append({**item, "local_state": local_state, "local_message": local_message, "local_paths": [str(path) for path in paths], "image_count": images, "label_count": labels})
    return output


def spark_status() -> dict[str, object]:
    config = read_config(SPARK_DEPLOYMENT_PATH, {})
    if not isinstance(config, dict):
        config = {}
    model_store = Path(os.getenv(str(config.get("model_store_env", "SOP_SPARK_MODEL_DIR")), str(config.get("default_model_store", "/home/xjai/sop-model-store"))))
    inference_url = os.getenv(str(config.get("inference_url_env", "SPARK_INFERENCE_URL")), str(config.get("default_inference_url", "http://127.0.0.1:8001"))).rstrip("/")
    gpu = {"available": False, "name": None, "driver": None}
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"], capture_output=True, text=True, timeout=2, check=False)
        name, _, driver = result.stdout.strip().partition(",")
        if name:
            gpu = {"available": True, "name": name.strip(), "driver": driver.strip()}
    except (OSError, subprocess.TimeoutExpired):
        pass
    registry_path = model_store / "registry.json"
    registry = read_config(registry_path, {}) if registry_path.exists() else read_config(RUNTIME_ROOT / "spark_model_registry.json", {})
    models = registry.get("models", []) if isinstance(registry, dict) else []
    return {
        "ok": True,
        "host": socket.gethostname(),
        "architecture": os.uname().machine,
        "is_local_spark": "GB10" in str(gpu.get("name")) or "spark" in socket.gethostname().lower(),
        "gpu": gpu,
        "model_store": str(model_store),
        "registry_exists": bool(models),
        "models_registered": len(models),
        "models_available": sum(bool(item.get("exists")) for item in models),
        "inference_url": inference_url,
        "inference_available": probe_url(f"{inference_url}/v2/health/ready"),
        "policy": config.get("deployment_policy"),
        "models": models or config.get("models", []),
        "batch_profile": read_config(ROOT / "qa" / "spark_batch_profile.json", {}),
    }


def sync_models_to_spark() -> dict[str, object]:
    script = ROOT / "scripts" / "sync_models_to_spark.py"
    result = subprocess.run([sys.executable, str(script), "--apply"], cwd=ROOT, capture_output=True, text=True, timeout=180, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Spark模型同步失败")
    return json.loads(result.stdout)


def camera_service(camera_id: int) -> LiveCameraService:
    if camera_id not in LIVE_CAMERAS:
        raise ValueError(f"相机编号不支持: {camera_id}，当前可用编号：{', '.join(str(key) for key in LIVE_CAMERAS)}")
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
    records = read_jsonl(RUNTIME_ROOT / "annotations.jsonl") + collab_annotations(video_id)
    for record in records:
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
            "source_kind": record.get("source_kind", "manual"),
            "source": record.get("source", "平台人工标注"),
            "review_status": review.get("review_status") or record.get("review_status", "human_confirmed"),
            "reviewer": review.get("reviewer") or record.get("reviewer"),
            "reviewed_at": review.get("recorded_at"),
        })
    # A collaborative save supersedes a legacy JSONL record with the same ID.
    unique: dict[str, dict] = {}
    for item in items:
        unique[str(item["annotation_id"])] = item
    return list(unique.values())


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

    def send_json(self, payload: object, status: int = 200, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
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
        if path == "/api/auth/me":
            user = _collab_user(self)
            self.send_json({"ok": True, "authenticated": bool(user), "user": user})
            return
        if path == "/api/collab/status":
            user = _collab_user(self)
            self.send_json({"ok": True, "authenticated": bool(user), "user": user, "db": str(COLLAB_DB_PATH), "multi_user": True, "save_mode": "SQLite WAL + optimistic version lock"})
            return
        if path == "/api/collab/users":
            user = _require_collab_user(self, {"admin"})
            if not user:
                return
            with _collab_connection() as db:
                rows = db.execute("SELECT username, display_name, role, disabled, created_at FROM users ORDER BY username").fetchall()
            self.send_json({"ok": True, "users": [dict(row) for row in rows]})
            return
        if path == "/api/collab/labels":
            self.send_json({"ok": True, "labels": custom_labels(), "lines": production_lines()})
            return
        if path == "/api/collab/annotations":
            user = _require_collab_user(self)
            if not user:
                return
            query = parse_qs(parsed.query)
            video_id = query.get("video", [""])[0] or None
            self.send_json({"ok": True, "items": collab_annotations(video_id), "total": len(collab_annotations(video_id)), "user": user})
            return
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
            if source in {"all", "manual", "interpolated"}:
                items.extend(manual_annotation_items(video_id))
            if source in {"prelabel", "candidate", "manual", "interpolated"}:
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
            user = _require_collab_user(self)
            if not user:
                return
            query = parse_qs(parsed.query)
            status = query.get("status", ["human_confirmed"])[0]
            export_format = query.get("format", ["json"])[0]
            items = exported_annotation_items(status)
            if export_format == "yolo":
                classes = sorted({str(item.get("label", "未分类目标")) for item in items})
                class_ids = {label: index for index, label in enumerate(classes)}
                files: dict[str, list[str]] = {}
                for item in items:
                    x1, y1, x2, y2 = [float(value) for value in item["box"]]
                    line = f"{class_ids[str(item.get('label', '未分类目标'))]} {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} {x2 - x1:.6f} {y2 - y1:.6f}"
                    name = f"{item.get('video_id')}_{int(item.get('frame', 0)):08d}.txt"
                    files.setdefault(name, []).append(line)
                self.send_json({"ok": True, "dataset_version": time.strftime("sop-annotations-%Y%m%d"), "format": "yolo", "review_status": status, "classes": classes, "files": {name: "\n".join(lines) for name, lines in files.items()}, "total_annotations": len(items), "total_label_files": len(files), "truth_boundary": "仅导出指定审核状态；正式训练前需连同对应原图冻结版本并计算校验值。"})
                return
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
        if path == "/api/integrations":
            label_studio_url = os.getenv("LABEL_STUDIO_URL", "http://127.0.0.1:8080").rstrip("/")
            label_available = probe_url(label_studio_url)
            config = cvat_config()
            cvat_available = probe_url(str(config["url"]))
            self.send_json({"label_studio": {"url": label_studio_url, "available": label_available}, "cvat": {**config, "available": cvat_available, "tasks_url": f"{config['url']}/tasks"}})
            return
        if path == "/api/cvat/status":
            config = cvat_config()
            self.send_json({"ok": True, **config, "available": probe_url(str(config["url"])), "tasks_url": f"{config['url']}/tasks"})
            return
        if path == "/api/training/catalog":
            self.send_json({"ok": True, "algorithms": read_config(TRAINING_CATALOG_PATH, []), "datasets": dataset_catalog_with_status(), "production_lines": production_lines()})
            return
        if path == "/api/production-lines":
            self.send_json({"ok": True, "active_line_id": ACTIVE_LINE_ID, "active_line": active_production_line(), "lines": production_lines(), "datasets": dataset_catalog_with_status()})
            return
        if path == "/api/spark/status":
            self.send_json(spark_status())
            return
        if path == "/api/training/report":
            report_path = WEB_ROOT / "analysis" / "model_benchmark" / "benchmark_report.json"
            report = read_config(report_path, {})
            self.send_json({"ok": True, **report, "chart_count": len(report.get("charts", [])) if isinstance(report, dict) else 0})
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
        try:
            payload = self.read_json()
            if path == "/api/auth/login":
                user, token = collab_auth_login(str(payload.get("username", "")).strip(), str(payload.get("password", "")))
                if not user or not token:
                    self.send_json({"ok": False, "message": "用户名或密码错误"}, 401)
                    return
                self.send_json({"ok": True, "user": user, "token": token, "expires_in": COLLAB_SESSION_TTL, "message": "协同工作区登录成功"}, headers={"Set-Cookie": f"sop_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={COLLAB_SESSION_TTL}"})
                return
            if path == "/api/auth/logout":
                token = _request_token(self)
                if token:
                    with _collab_connection() as db:
                        db.execute("DELETE FROM sessions WHERE token=?", (token,))
                self.send_json({"ok": True, "message": "已退出协同工作区"}, headers={"Set-Cookie": "sop_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
                return
            if path == "/api/collab/labels":
                user = _require_collab_user(self, {"annotator", "reviewer"})
                if not user:
                    return
                line_id = str(payload.get("line_id", "")).strip()
                if not any(str(line.get("id")) == line_id for line in production_lines()):
                    self.send_json({"ok": False, "message": "产线不存在"}, 404)
                    return
                labels = save_custom_labels(line_id, payload.get("labels"))
                _collab_audit(str(user["username"]), "labels_update", line_id, labels)
                self.send_json({"ok": True, "line_id": line_id, "labels": labels, "message": "自定义标签体系已保存并对所有标注员生效"})
                return
            if path == "/api/collab/users":
                user = _require_collab_user(self, {"admin"})
                if not user:
                    return
                username = str(payload.get("username", "")).strip()
                display_name = str(payload.get("display_name", "")).strip()
                password = str(payload.get("password", ""))
                role = str(payload.get("role", "annotator"))
                if not username or not display_name or len(password) < 10 or role not in COLLAB_ROLES:
                    self.send_json({"ok": False, "message": "账号、姓名、角色必须有效，密码至少10位"}, 400)
                    return
                with _collab_connection() as db:
                    db.execute(
                        "INSERT INTO users(username, display_name, role, password_hash, created_at) VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT(username) DO UPDATE SET display_name=excluded.display_name, role=excluded.role, password_hash=excluded.password_hash, disabled=0",
                        (username, display_name, role, _password_hash(password), time.strftime("%Y-%m-%d %H:%M:%S")),
                    )
                _collab_audit(str(user["username"]), "user_upsert", username, {"display_name": display_name, "role": role})
                self.send_json({"ok": True, "message": f"账号 {username} 已保存", "username": username, "role": role})
                return
            if path in {"/api/collab/lock", "/api/collab/unlock"}:
                user = _require_collab_user(self, {"annotator", "reviewer"})
                if not user:
                    return
                video_id = str(payload.get("video_id", "")).strip()
                frame = int(payload.get("frame", -1))
                if video_info(video_id) is None or frame < 0:
                    self.send_json({"ok": False, "message": "视频或帧号无效"}, 400)
                    return
                result = acquire_collab_lock(video_id, frame, str(user["username"]), int(payload.get("ttl", 900))) if path.endswith("/lock") else release_collab_lock(video_id, frame, str(user["username"]))
                self.send_json(result, 200 if result.get("ok") else 409)
                return
            if path == "/api/collab/interpolate":
                user = _require_collab_user(self, {"annotator", "reviewer"})
                if not user:
                    return
                video_id = str(payload.get("video_id", ""))
                info = video_info(video_id)
                start, end = payload.get("start"), payload.get("end")
                if info is None or not isinstance(start, dict) or not isinstance(end, dict):
                    self.send_json({"ok": False, "message": "关键帧参数不完整"}, 400)
                    return
                start_frame, end_frame = int(start.get("frame", -1)), int(end.get("frame", -1))
                if start_frame < 0 or end_frame <= start_frame:
                    self.send_json({"ok": False, "message": "结束关键帧必须晚于开始关键帧"}, 400)
                    return
                start_box, _ = normalize_box(start.get("box"), video_id)
                end_box, _ = normalize_box(end.get("box"), video_id)
                stride = max(1, min(30, int(payload.get("stride", 5))))
                fps = float(info.get("fps", 30))
                track_id = str(payload.get("track_id") or f"track-{time.time_ns()}")
                label = str(payload.get("label", "")).strip()
                if not label:
                    self.send_json({"ok": False, "message": "插值候选必须指定标签"}, 400)
                    return
                created = []
                for frame in range(start_frame + stride, end_frame, stride):
                    ratio = (frame - start_frame) / (end_frame - start_frame)
                    box = [round(a + (b - a) * ratio, 6) for a, b in zip(start_box, end_box)]
                    _, pixels = normalize_box(box, video_id)
                    item = {"annotation_id": f"interpolated:{video_id}:{track_id}:{frame}", "video_id": video_id, "frame": frame, "video_time": round(frame / fps, 3), "label": label, "box": box, "box_pixels": pixels, "box_format": "normalized_xyxy", "source_kind": "interpolated", "source": "关键帧线性插值候选", "track_id": track_id, "keyframe": False, "review_status": "pending", "reviewer": str(user["display_name"]), "auto_generated": True}
                    created.append(save_collab_annotation(item, str(user["username"])))
                _collab_audit(str(user["username"]), "interpolate", track_id, {"start": start_frame, "end": end_frame, "stride": stride, "created": len(created)})
                self.send_json({"ok": True, "track_id": track_id, "created": len(created), "items": created, "message": f"已生成 {len(created)} 个中间帧候选，抽检确认后才能进入真值集"})
                return
            if path == "/api/annotations":
                user = _require_collab_user(self, {"annotator", "reviewer"})
                if not user:
                    return
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
                    "reviewer": user["display_name"],
                    "keyframe": bool(payload.get("keyframe", True)),
                    "track_id": str(payload.get("track_id", "")).strip() or None,
                }
                evidence_data_url = payload.get("evidence_data_url")
                if evidence_data_url:
                    evidence_path = save_data_url(
                        evidence_data_url,
                        ANNOTATION_IMAGE_ROOT,
                        f"{video_id}_{annotation['frame']}_{time.strftime('%Y%m%d_%H%M%S')}",
                    )
                    annotation["evidence_path"] = str(evidence_path)
                annotation = save_collab_annotation(annotation, str(user["username"]), payload.get("expected_version"))
                self.send_json({"ok": True, "annotation": annotation, "message": "关键帧标注已保存到协同数据库，可进入质量抽检"})
                return
            if path == "/api/annotations/review":
                user = _require_collab_user(self, {"reviewer"})
                if not user:
                    return
                annotation_id = str(payload.get("annotation_id", "")).strip()
                status = str(payload.get("review_status", "")).strip()
                allowed = {"pending", "human_confirmed", "rejected", "needs_correction"}
                if not annotation_id or status not in allowed:
                    self.send_json({"ok": False, "message": "审核对象或状态无效"}, 400)
                    return
                review = {
                    "annotation_id": annotation_id,
                    "review_status": status,
                    "reviewer": user["display_name"],
                    "comment": payload.get("comment", ""),
                }
                self.append_event("annotation_reviews.jsonl", review)
                _collab_audit(str(user["username"]), "annotation_review", annotation_id, review)
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
                job_id = f"TRAIN-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}"
                algorithm = str(payload.get("algorithm") or payload.get("model") or "YOLO26N")
                dataset = str(payload.get("dataset") or "未选择数据集")
                self.append_event("training_jobs.jsonl", {"job_id": job_id, "status": "待审核", "algorithm": algorithm, "dataset": dataset, "workflow": ["train", "infer", "validate", "test"], **payload})
                self.send_json({"ok": True, "job_id": job_id, "algorithm": algorithm, "dataset": dataset, "workflow": ["train", "infer", "validate", "test"], "message": "一键训练任务已登记：训练、推理、验证、测试完成后生成报告"})
                return
            if path == "/api/train/one-click":
                algorithm = str(payload.get("algorithm") or "YOLO26N")
                dataset = str(payload.get("dataset") or "宁波模塑综合数据集")
                line_id = str(payload.get("line_id") or "automotive")
                lines = read_config(PRODUCTION_LINES_PATH, [])
                line = next((item for item in lines if item.get("id") == line_id), None) if isinstance(lines, list) else None
                if line is None:
                    self.send_json({"ok": False, "message": f"未知产线: {line_id}"}, 400)
                    return
                job_id = f"EVAL-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}"
                record = {"job_id": job_id, "status": "已创建", "line_id": line_id, "line_name": line.get("name"), "algorithm": algorithm, "dataset": dataset, "workflow": ["训练", "推理", "验证", "测试", "可视化报告"], "created_at": time.time()}
                self.append_event("training_jobs.jsonl", record)
                self.send_json({"ok": True, **record, "report_url": "/api/training/report", "transfer_learning": line.get("transfer"), "message": "一键验证流水线已创建；当前报告使用已生成的基线图表，真实精度需冻结真值集后运行"})
                return
            if path == "/api/cvat/task":
                result = cvat_task_create(payload)
                self.send_json(result)
                return
            if path == "/api/production-lines/select":
                line = select_production_line(str(payload.get("line_id") or ""))
                self.append_event("production_line_switches.jsonl", {"line_id": line.get("id"), "line_name": line.get("name"), "primary_model": line.get("primary_model")})
                self.send_json({"ok": True, "active_line": line, "camera_model_path": str(LINE_MODEL_PATHS.get(str(line.get("id")))), "message": f"已切换到 {line.get('name')}；实时相机服务已停止，重新启动后加载对应模型"})
                return
            if path == "/api/spark/sync-models":
                registry = sync_models_to_spark()
                self.append_event("spark_syncs.jsonl", {"target": registry.get("target"), "model_count": len(registry.get("models", [])), "host": registry.get("host")})
                self.send_json({"ok": True, "registry": registry, "message": f"已将模型登记到Spark模型仓库：{registry.get('target')}"})
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
        except PermissionError as exc:
            self.send_json({"ok": False, "message": str(exc)}, HTTPStatus.CONFLICT)
        except RuntimeError as exc:
            self.send_json({"ok": False, "message": str(exc)}, HTTPStatus.CONFLICT)
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
