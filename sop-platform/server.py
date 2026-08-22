from __future__ import annotations

import json
import gzip
import base64
import csv
import hashlib
import hmac
import importlib.util
import io
import math
import mimetypes
import os
import secrets
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile
from bisect import bisect_left
from shutil import which
from urllib.request import ProxyHandler, Request, build_opener, urlopen
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
TRAINING_CATALOG_PATH = ROOT / "config" / "training_catalog.json"
DATASET_CATALOG_PATH = ROOT / "config" / "dataset_catalog.json"
PRODUCTION_LINES_PATH = ROOT / "config" / "production_lines.json"
SPARK_DEPLOYMENT_PATH = ROOT / "config" / "spark_deployment.json"
PCB_MODEL_REGISTRY_PATH = ROOT / "config" / "pcb_model_registry.json"
ACTIVE_MODEL_SELECTION_PATH = RUNTIME_ROOT / "active_pcb_model.json"
ANNOTATION_SCOPE_PATH = RUNTIME_ROOT / "station_annotation_scope.json"
ACTIVE_LINE_ID = os.getenv("SOP_PRODUCTION_LINE", "pcb")
SPARK_MODEL_ROOT = Path(os.getenv("SOP_SPARK_MODEL_DIR", "/home/xjai/sop-model-store"))


def deployed_model(filename: str) -> Path:
    spark_path = SPARK_MODEL_ROOT / filename
    return spark_path if spark_path.exists() else ROOT / "models" / filename


LINE_MODEL_PATHS = {
    "pcb": deployed_model("yolo26n_PCB插装0265_50轮_待人工验收.pt"),
    "automotive": deployed_model("yolo26n_两视频小目标蒸馏_待人工验收.pt"),
    "molding-coating": deployed_model("yolo26n.pt"),
}
FRAME_CACHE: dict[str, list[dict]] = {}
DESKTOP_ROOT = Path(os.getenv("SOP_DESKTOP_DIR", "/home/xjai/Desktop/sop xjai"))
EVIDENCE_ROOT = DESKTOP_ROOT / "摄像头证据"
ANNOTATION_IMAGE_ROOT = DESKTOP_ROOT / "标注图片"
NETWORK_CAMERAS_PATH = ROOT / "config" / "network_cameras.json"
CAMERA_SLOT_COUNT = max(1, int(os.getenv("SOP_CAMERA_COUNT", "8")))
ANNOTATION_DB_PATH = Path(os.getenv("SOP_ANNOTATION_DB", str(RUNTIME_ROOT / "sop_annotations.sqlite3")))
ANNOTATION_AUTOSAVE_ROOT = RUNTIME_ROOT / "annotation_autosave"
ANNOTATION_VIDEO_CACHE_ROOT = RUNTIME_ROOT / "annotation_video_cache"
CLOUD_INBOX_ROOT = RUNTIME_ROOT / "cloud_inbox"
ANNOTATION_EXPORT_ROOT = RUNTIME_ROOT / "annotation_exports"
ANNOTATION_DB_LOCK = threading.RLock()
CAMERA_INFERENCE_GATE = threading.BoundedSemaphore(max(1, int(os.getenv("SOP_CAMERA_GPU_CONCURRENCY", "1"))))
AUTH_SESSION_SECONDS = max(900, int(os.getenv("SOP_AUTH_SESSION_SECONDS", "28800")))
AUTH_SECURE_COOKIE = os.getenv("SOP_SECURE_COOKIE", "0").strip().lower() in {"1", "true", "yes", "on"}
ROLE_LABELS = {"admin": "开发者", "manager": "管理者", "worker": "普通员工"}
ROLE_VIEWS = {
    "admin": ["overview", "monitor", "decision", "studio", "annotation", "training", "mes", "quality"],
    "manager": ["overview", "monitor", "decision", "studio", "annotation", "training", "mes", "quality"],
    "worker": ["overview", "monitor", "annotation"],
}

GOOGLE_SOURCE_FOLDER_URL = os.getenv("SOP_GOOGLE_SOURCE_URL", "").strip()
GOOGLE_OUTPUT_FOLDER_URL = os.getenv("SOP_GOOGLE_OUTPUT_URL", "").strip()
BAIDU_SOURCE_URL = os.getenv("SOP_BAIDU_SOURCE_URL", "").strip()
CLOUD_SYNC_JOBS: dict[str, dict[str, object]] = {}
CLOUD_SYNC_LOCK = threading.Lock()
ANNOTATION_RENDER_JOBS: dict[str, dict[str, object]] = {}
ANNOTATION_RENDER_LOCK = threading.Lock()
AI_PRELABEL_STATUS_PATH = RUNTIME_ROOT / "ai_prelabel_status.json"
AI_PRELABEL_LOCK = threading.Lock()
AI_PRELABEL_PROCESS: subprocess.Popen | None = None


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${derived.hex()}"


def _password_matches(password: str, encoded: str) -> bool:
    try:
        name, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        if name != "pbkdf2_sha256":
            return False
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(derived.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def annotation_db() -> sqlite3.Connection:
    ANNOTATION_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(ANNOTATION_DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def initialize_annotation_db() -> None:
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS annotations (
                annotation_id TEXT PRIMARY KEY,
                video_id TEXT NOT NULL,
                frame INTEGER NOT NULL,
                video_time REAL NOT NULL,
                label TEXT NOT NULL,
                region TEXT NOT NULL DEFAULT '未分区',
                track_id TEXT,
                source_kind TEXT NOT NULL DEFAULT 'manual',
                review_status TEXT NOT NULL DEFAULT 'pending',
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_annotations_video_frame ON annotations(video_id, frame);
            CREATE INDEX IF NOT EXISTS idx_annotations_region ON annotations(video_id, region);
            CREATE TABLE IF NOT EXISTS annotation_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                annotation_id TEXT NOT NULL,
                review_status TEXT NOT NULL,
                reviewer TEXT,
                comment TEXT,
                payload_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reviews_annotation ON annotation_reviews(annotation_id, id);
            CREATE TABLE IF NOT EXISTS annotation_deletions (
                annotation_id TEXT PRIMARY KEY,
                video_id TEXT NOT NULL,
                track_id TEXT,
                deleted_by TEXT,
                reason TEXT,
                payload_json TEXT NOT NULL,
                deleted_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_annotation_deletions_video ON annotation_deletions(video_id, deleted_at);
            CREATE TABLE IF NOT EXISTS annotation_checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                video_id TEXT NOT NULL,
                operator TEXT,
                annotation_count INTEGER NOT NULL,
                current_frame INTEGER NOT NULL,
                current_time REAL NOT NULL,
                regions_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ai_prelabel_frames (
                video_id TEXT NOT NULL,
                frame INTEGER NOT NULL,
                detection_count INTEGER NOT NULL,
                model_name TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(video_id, frame)
            );
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cvat_tasks (
                local_task_id TEXT PRIMARY KEY,
                cvat_task_id INTEGER,
                name TEXT NOT NULL,
                dataset_id TEXT,
                line_id TEXT,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                url TEXT NOT NULL,
                labels_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cvat_tasks_created ON cvat_tasks(created_at DESC);
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'manager', 'worker')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at REAL NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires ON auth_sessions(expires_at);
            """
        )
        user_count = int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        if user_count == 0:
            credentials = []
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            for username, display_name, role in (
                ("admin", "系统开发者", "admin"),
                ("manager", "生产管理者", "manager"),
                ("worker", "现场员工", "worker"),
            ):
                password = secrets.token_urlsafe(12)
                connection.execute(
                    "INSERT INTO users(username, password_hash, display_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
                    (username, _password_hash(password), display_name, role, now),
                )
                credentials.append(f"{ROLE_LABELS[role]}\t{username}\t{password}")
            credential_path = RUNTIME_ROOT / "initial_credentials.txt"
            credential_path.write_text(
                "SOP平台首次登录账号（请登录后由开发者妥善保管并线下修改）\n\n" + "\n".join(credentials) + "\n",
                encoding="utf-8",
            )
            credential_path.chmod(0o600)


def authenticate_user(username: str, password: str) -> dict[str, object] | None:
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        row = connection.execute(
            "SELECT user_id, username, password_hash, display_name, role FROM users WHERE username = ? AND active = 1",
            (username,),
        ).fetchone()
        if row is None or not _password_matches(password, row["password_hash"]):
            return None
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        connection.execute("UPDATE users SET last_login_at = ? WHERE user_id = ?", (now, row["user_id"]))
        return {key: row[key] for key in ("user_id", "username", "display_name", "role")}


def create_auth_session(user_id: int) -> str:
    session_id = secrets.token_urlsafe(32)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (time.time(),))
        connection.execute(
            "INSERT INTO auth_sessions(session_id, user_id, expires_at, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, time.time() + AUTH_SESSION_SECONDS, now, now),
        )
    return session_id


def auth_user_for_session(session_id: str) -> dict[str, object] | None:
    if not session_id:
        return None
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        row = connection.execute(
            """SELECT u.user_id, u.username, u.display_name, u.role
               FROM auth_sessions s JOIN users u ON u.user_id = s.user_id
               WHERE s.session_id = ? AND s.expires_at > ? AND u.active = 1""",
            (session_id, time.time()),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            "UPDATE auth_sessions SET last_seen_at = ?, expires_at = ? WHERE session_id = ?",
            (time.strftime("%Y-%m-%d %H:%M:%S"), time.time() + AUTH_SESSION_SECONDS, session_id),
        )
        return {**{key: row[key] for key in row.keys()}, "role_label": ROLE_LABELS.get(row["role"], row["role"]), "views": ROLE_VIEWS.get(row["role"], [])}


def db_upsert_annotation(annotation: dict) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    annotation = {**annotation, "recorded_at": annotation.get("recorded_at", now)}
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        connection.execute("DELETE FROM annotation_deletions WHERE annotation_id = ?", (annotation["annotation_id"],))
        connection.execute(
            """
            INSERT INTO annotations (
                annotation_id, video_id, frame, video_time, label, region, track_id,
                source_kind, review_status, payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(annotation_id) DO UPDATE SET
                video_id=excluded.video_id, frame=excluded.frame, video_time=excluded.video_time,
                label=excluded.label, region=excluded.region, track_id=excluded.track_id,
                source_kind=excluded.source_kind, review_status=excluded.review_status,
                payload_json=excluded.payload_json, updated_at=excluded.updated_at
            """,
            (
                annotation["annotation_id"], annotation["video_id"], annotation["frame"],
                annotation["video_time"], annotation["label"], annotation.get("region") or "未分区",
                annotation.get("track_id"), annotation.get("source_kind", "manual"),
                annotation.get("review_status", "pending"), json.dumps(annotation, ensure_ascii=False),
                now, now,
            ),
        )


def db_manual_annotations(video_id: str | None = None, frame: int | None = None) -> list[dict]:
    query = "SELECT payload_json FROM annotations"
    clauses: list[str] = []
    params: list[object] = []
    if video_id is not None:
        clauses.append("video_id = ?")
        params.append(video_id)
    if frame is not None:
        clauses.append("frame = ?")
        params.append(frame)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY video_id, frame, annotation_id"
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        deleted = {str(row[0]) for row in connection.execute("SELECT annotation_id FROM annotation_deletions")}
        return [item for item in (json.loads(row["payload_json"]) for row in connection.execute(query, tuple(params))) if str(item.get("annotation_id")) not in deleted]


def db_deleted_annotation_ids() -> set[str]:
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        return {str(row[0]) for row in connection.execute("SELECT annotation_id FROM annotation_deletions")}


def db_delete_annotations(annotation_ids: list[str], deleted_by: str, reason: str = "人工删除") -> dict[str, object]:
    cleaned_ids = list(dict.fromkeys(item.strip() for item in annotation_ids if item.strip()))
    if not cleaned_ids:
        raise ValueError("未指定要删除的标注")
    deleted: list[dict] = []
    skipped: list[str] = []
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        for annotation_id in cleaned_ids:
            row = connection.execute(
                "SELECT video_id, track_id, source_kind, payload_json FROM annotations WHERE annotation_id = ?",
                (annotation_id,),
            ).fetchone()
            if row is None:
                skipped.append(annotation_id)
                continue
            payload = json.loads(row["payload_json"])
            tombstone = {
                "annotation_id": annotation_id,
                "video_id": row["video_id"],
                "track_id": row["track_id"],
                "deleted_by": deleted_by,
                "reason": reason,
                "deleted_at": now,
            }
            connection.execute(
                "INSERT OR REPLACE INTO annotation_deletions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (annotation_id, row["video_id"], row["track_id"], deleted_by, reason, json.dumps({**payload, **tombstone}, ensure_ascii=False), now),
            )
            connection.execute("DELETE FROM annotation_reviews WHERE annotation_id = ?", (annotation_id,))
            connection.execute("DELETE FROM annotations WHERE annotation_id = ?", (annotation_id,))
            deleted.append(tombstone)
    return {"deleted": deleted, "deleted_count": len(deleted), "skipped": skipped}


def db_delete_generated_annotation(annotation: dict, deleted_by: str, reason: str = "人工删除AI候选框") -> dict[str, object]:
    annotation_id = str(annotation.get("annotation_id") or "").strip()
    source_kind = str(annotation.get("source_kind") or "")
    if not annotation_id or source_kind not in {"prelabel", "candidate"}:
        raise ValueError("只能删除存在的AI预标注或小目标候选框")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    tombstone = {
        "annotation_id": annotation_id,
        "video_id": str(annotation.get("video_id") or ""),
        "track_id": annotation.get("track_id"),
        "source_kind": source_kind,
        "deleted_by": deleted_by,
        "reason": reason,
        "deleted_at": now,
    }
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO annotation_deletions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                annotation_id,
                tombstone["video_id"],
                tombstone["track_id"],
                deleted_by,
                reason,
                json.dumps({**annotation, **tombstone}, ensure_ascii=False),
                now,
            ),
        )
        connection.execute("DELETE FROM annotation_reviews WHERE annotation_id = ?", (annotation_id,))
    return {"deleted": [tombstone], "deleted_count": 1, "skipped": []}


def db_delete_track(video_id: str, track_id: str, deleted_by: str) -> dict[str, object]:
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        ids = [str(row[0]) for row in connection.execute(
            "SELECT annotation_id FROM annotations WHERE video_id = ? AND track_id = ? AND source_kind = 'manual'",
            (video_id, track_id),
        )]
    if not ids:
        raise ValueError("该轨迹没有可删除的人工标注")
    return db_delete_annotations(ids, deleted_by, "删除整条轨迹")


def db_delete_track_segment(video_id: str, track_id: str, start_frame: int, end_frame: int, deleted_by: str) -> dict[str, object]:
    if start_frame < 0 or end_frame < start_frame:
        raise ValueError("轨迹删除区间无效")
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        ids = [str(row[0]) for row in connection.execute(
            """SELECT annotation_id FROM annotations
               WHERE video_id = ? AND track_id = ? AND source_kind = 'manual'
                 AND frame BETWEEN ? AND ? ORDER BY frame""",
            (video_id, track_id, start_frame, end_frame),
        )]
    return db_delete_annotations(ids, deleted_by, f"删除轨迹区间 {start_frame}-{end_frame} 帧")


def db_annotation_tracks(video_id: str) -> list[dict[str, object]]:
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        rows = list(connection.execute(
            """SELECT track_id, label, region, MIN(frame) AS start_frame, MAX(frame) AS end_frame,
                      COUNT(*) AS frame_count, MAX(updated_at) AS updated_at
               FROM annotations
               WHERE video_id = ? AND track_id IS NOT NULL AND track_id != '' AND source_kind = 'manual'
               GROUP BY track_id, label, region ORDER BY updated_at DESC""",
            (video_id,),
        ))
        results = []
        for row in rows:
            shape_rows = list(connection.execute(
                "SELECT frame, payload_json FROM annotations WHERE video_id = ? AND track_id = ? ORDER BY frame LIMIT 2000",
                (video_id, row["track_id"]),
            ))
            shapes = []
            for shape_row in shape_rows:
                payload = json.loads(shape_row["payload_json"])
                shapes.append({
                    "frame": int(shape_row["frame"]),
                    "box": payload.get("box"),
                    "annotation_id": payload.get("annotation_id"),
                    "tracking_method": payload.get("tracking_method"),
                    "tracking_confidence": payload.get("tracking_confidence"),
                })
            results.append({**dict(row), "frames": [item["frame"] for item in shapes], "shapes": shapes})
    return results


def annotation_scope() -> dict[str, object]:
    default = {
        "station_name": "PCB固定工位",
        "station_type": "assembly",
        "quality_goal": "确认取料、插装位置和工序是否正确",
        "material_a": "二极管插件",
        "material_b": "其他插件",
        "distinguish_materials": True,
        "required_labels": ["PCB板", "操作人员手部", "物料框", "手持插件", "插件位置"],
        "excluded_labels": ["逐个电容", "逐个电阻", "与本工位质量目标无关的元件"],
        "verification_points": ["步骤顺序", "手势与操作模式", "装配内容", "标签信息"],
        "policy": "只标注能判断本工位取料、插装位置和工序正确性的目标。只有当不同插件会影响工艺判定时才分类。",
    }
    saved = read_config(ANNOTATION_SCOPE_PATH, {})
    return {**default, **saved} if isinstance(saved, dict) else default


def save_annotation_scope(payload: dict[str, object]) -> dict[str, object]:
    current = annotation_scope()
    allowed = {"station_name", "station_type", "quality_goal", "material_a", "material_b", "distinguish_materials", "required_labels", "excluded_labels", "verification_points", "policy"}
    scope = {**current, **{key: payload[key] for key in allowed if key in payload}}
    if not str(scope.get("station_name") or "").strip() or not str(scope.get("quality_goal") or "").strip():
        raise ValueError("工位名称和质量判定目标不能为空")
    ANNOTATION_SCOPE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = ANNOTATION_SCOPE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(scope, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, ANNOTATION_SCOPE_PATH)
    return scope


def db_annotation_reviews() -> dict[str, dict]:
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        rows = connection.execute(
            """SELECT payload_json FROM annotation_reviews
               WHERE id IN (SELECT MAX(id) FROM annotation_reviews GROUP BY annotation_id)"""
        )
        return {str(item["annotation_id"]): item for item in (json.loads(row["payload_json"]) for row in rows)}


def db_save_review(review: dict) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    review = {**review, "recorded_at": now}
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        connection.execute(
            "INSERT INTO annotation_reviews(annotation_id, review_status, reviewer, comment, payload_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (review["annotation_id"], review["review_status"], review.get("reviewer"), review.get("comment", ""), json.dumps(review, ensure_ascii=False), now),
        )


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{secrets.token_hex(6)}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def atomic_write_gzip_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    temporary.replace(path)


def cache_annotation_video(video_id: str) -> dict[str, object]:
    source = annotation_video_path(video_id)
    ANNOTATION_VIDEO_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    safe_video_id = "".join(character if character.isalnum() or character in "-_." else "-" for character in video_id)
    destination = ANNOTATION_VIDEO_CACHE_ROOT / f"{safe_video_id}_{source.name}"
    cache_mode = "hardlink"
    if not destination.exists() or destination.stat().st_size != source.stat().st_size:
        destination.unlink(missing_ok=True)
        try:
            os.link(source, destination)
        except OSError:
            destination.symlink_to(source)
            cache_mode = "symlink"
    elif destination.is_symlink():
        cache_mode = "symlink"
    return {
        "source": str(source),
        "path": str(destination),
        "mode": cache_mode,
        "size_bytes": source.stat().st_size,
    }


def save_annotation_draft(
    payload: dict,
    checkpoint_id: str,
    recorded_at: str,
    annotations: list[dict],
) -> dict[str, object]:
    video_id = str(payload.get("video_id") or "").strip()
    draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else None
    snapshot = {
        "schema_version": 2,
        "checkpoint_id": checkpoint_id,
        "video_id": video_id,
        "current_time": float(payload.get("current_time", 0)),
        "current_frame": int(payload.get("current_frame", 0)),
        "draft": draft,
        "annotation_count": len(annotations),
        "annotations": annotations,
        "recorded_at": recorded_at,
    }
    video_root = ANNOTATION_AUTOSAVE_ROOT / video_id
    summary = {key: value for key, value in snapshot.items() if key != "annotations"}
    atomic_write_json(video_root / "latest.json", summary)
    history_path = video_root / "history" / f"{checkpoint_id}.json.gz"
    atomic_write_gzip_json(history_path, snapshot)
    history = sorted((video_root / "history").glob("*.json*"), key=lambda path: path.stat().st_mtime, reverse=True)
    for stale in history[120:]:
        stale.unlink(missing_ok=True)
    return summary


def annotation_checkpoint_snapshot(video_id: str, checkpoint_id: str) -> dict[str, object] | None:
    if not checkpoint_id or Path(checkpoint_id).name != checkpoint_id:
        return None
    compressed_path = ANNOTATION_AUTOSAVE_ROOT / video_id / "history" / f"{checkpoint_id}.json.gz"
    legacy_path = ANNOTATION_AUTOSAVE_ROOT / video_id / "history" / f"{checkpoint_id}.json"
    try:
        if compressed_path.is_file():
            with gzip.open(compressed_path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
        else:
            payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("video_id") != video_id or payload.get("checkpoint_id") != checkpoint_id:
        return None
    return payload


def latest_annotation_draft(video_id: str) -> dict[str, object] | None:
    path = ANNOTATION_AUTOSAVE_ROOT / video_id / "latest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def db_checkpoint(payload: dict) -> dict:
    video_id = str(payload.get("video_id") or "")
    if not video_id:
        raise ValueError("保存进度时必须指定视频")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    checkpoint_id = f"SAVE-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}"
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        annotation_rows = list(connection.execute(
            "SELECT payload_json FROM annotations WHERE video_id = ? ORDER BY frame, annotation_id",
            (video_id,),
        ))
        annotations = [json.loads(row["payload_json"]) for row in annotation_rows]
        count = len(annotations)
        regions = [row[0] for row in connection.execute("SELECT DISTINCT region FROM annotations WHERE video_id = ? ORDER BY region", (video_id,))]
        connection.execute(
            "INSERT INTO annotation_checkpoints VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (checkpoint_id, video_id, payload.get("operator", "本地标注员"), count, int(payload.get("current_frame", 0)), float(payload.get("current_time", 0)), json.dumps(regions, ensure_ascii=False), now),
        )
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
    backup_root = RUNTIME_ROOT / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    temporary = backup_root / ".sop_annotations_latest.sqlite3.tmp"
    destination = backup_root / "sop_annotations_latest.sqlite3"
    with ANNOTATION_DB_LOCK, annotation_db() as source, sqlite3.connect(temporary) as target:
        source.backup(target)
    temporary.replace(destination)
    video_cache = cache_annotation_video(video_id)
    autosave = save_annotation_draft(payload, checkpoint_id, now, annotations)
    return {"checkpoint_id": checkpoint_id, "video_id": video_id, "annotation_count": count, "regions": regions, "recorded_at": now, "backup": str(destination), "video_cache": video_cache, "autosave": autosave}


def annotation_db_health() -> dict:
    try:
        with ANNOTATION_DB_LOCK, annotation_db() as connection:
            integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            annotations = int(connection.execute("SELECT COUNT(*) FROM annotations").fetchone()[0])
            checkpoints = int(connection.execute("SELECT COUNT(*) FROM annotation_checkpoints").fetchone()[0])
        return {"ok": integrity == "ok", "integrity": integrity, "annotations": annotations, "checkpoints": checkpoints, "path": str(ANNOTATION_DB_PATH), "journal_mode": "WAL", "synchronous": "FULL"}
    except sqlite3.Error as exc:
        return {"ok": False, "message": str(exc), "path": str(ANNOTATION_DB_PATH)}


def db_annotation_history(video_id: str) -> dict[str, object]:
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        checkpoint_rows = list(connection.execute(
            """SELECT checkpoint_id, operator, annotation_count, current_frame, current_time,
                      regions_json, recorded_at
               FROM annotation_checkpoints WHERE video_id = ? ORDER BY recorded_at, checkpoint_id""",
            (video_id,),
        ))
        recent_rows = list(connection.execute(
            """SELECT annotation_id, frame, video_time, label, region, review_status, updated_at
               FROM annotations WHERE video_id = ? ORDER BY updated_at DESC LIMIT 50""",
            (video_id,),
        ))
        region_rows = list(connection.execute(
            """SELECT region, COUNT(*) AS annotation_count, MAX(updated_at) AS last_saved_at
               FROM annotations WHERE video_id = ? GROUP BY region ORDER BY region""",
            (video_id,),
        ))
    checkpoints = []
    for index, row in enumerate(checkpoint_rows, 1):
        checkpoints.append({
            "round": index,
            "checkpoint_id": row["checkpoint_id"],
            "operator": row["operator"],
            "annotation_count": row["annotation_count"],
            "current_frame": row["current_frame"],
            "current_time": row["current_time"],
            "regions": json.loads(row["regions_json"]),
            "recorded_at": row["recorded_at"],
        })
    return {
        "video_id": video_id,
        "save_rounds": len(checkpoints),
        "checkpoints": list(reversed(checkpoints[-20:])),
        "regions": [dict(row) for row in region_rows],
        "recent_annotations": [dict(row) for row in recent_rows],
    }


def db_record_cvat_task(payload: dict[str, object], result: dict[str, object]) -> dict[str, object]:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    cvat_task_id = result.get("task_id")
    local_task_id = f"CVAT-{cvat_task_id}" if cvat_task_id is not None else f"CVAT-LINK-{time.time_ns()}"
    record = {
        "local_task_id": local_task_id,
        "cvat_task_id": cvat_task_id,
        "name": str(payload.get("name") or "宁波模塑 SOP 标注任务"),
        "dataset_id": str(payload.get("dataset_id") or ""),
        "line_id": str(payload.get("line_id") or ""),
        "mode": str(result.get("mode") or "link"),
        "status": str(result.get("status") or ("已创建" if cvat_task_id is not None else "待配置令牌")),
        "url": str(result["url"]),
        "labels": [str(item) for item in (payload.get("labels") or [])],
        "created_at": now,
        "updated_at": now,
    }
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        connection.execute(
            """INSERT OR REPLACE INTO cvat_tasks
               (local_task_id, cvat_task_id, name, dataset_id, line_id, mode, status, url,
                labels_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["local_task_id"], record["cvat_task_id"], record["name"],
                record["dataset_id"], record["line_id"], record["mode"], record["status"],
                record["url"], json.dumps(record["labels"], ensure_ascii=False), now, now,
            ),
        )
    return record


def db_cvat_tasks() -> list[dict[str, object]]:
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        rows = list(connection.execute(
            """SELECT local_task_id, cvat_task_id, name, dataset_id, line_id, mode, status,
                      url, labels_json, created_at, updated_at
               FROM cvat_tasks ORDER BY created_at DESC, local_task_id DESC LIMIT 50"""
        ))
    items = []
    for row in rows:
        item = dict(row)
        item["labels"] = json.loads(item.pop("labels_json"))
        items.append(item)
    return items


initialize_annotation_db()


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
    configured = []
    try:
        value = json.loads(NETWORK_CAMERAS_PATH.read_text(encoding="utf-8")) if NETWORK_CAMERAS_PATH.exists() else []
        configured = value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        configured = []
    environment_urls = [url.strip() for url in os.getenv("SOP_NETWORK_CAMERA_URLS", "").split(";") if url.strip()]
    for item in configured + [{"url": url} for url in environment_urls]:
        source = str(item.get("url", "")).strip() if isinstance(item, dict) else ""
        if source.startswith(("rtsp://", "rtsps://", "http://", "https://")) and source not in candidates:
            candidates.append(source)
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
    configured_names = {}
    try:
        configured_names = {str(item.get("url")): str(item.get("name") or "网络摄像头") for item in json.loads(NETWORK_CAMERAS_PATH.read_text(encoding="utf-8")) if isinstance(item, dict)} if NETWORK_CAMERAS_PATH.exists() else {}
    except (OSError, json.JSONDecodeError, TypeError):
        configured_names = {}
    effective_sources = {
        camera_id: os.getenv(f"SOP_CAMERA_SOURCE_{camera_id}") or DEFAULT_CAMERA_SOURCES.get(camera_id, "")
        for camera_id in range(CAMERA_SLOT_COUNT)
    }
    for camera_id, source in effective_sources.items():
        if not source:
            continue
        try:
            resolved = str(Path(source).resolve())
        except OSError:
            resolved = source
        matched = next((item for item in videos if item.get("device") == resolved), None)
        is_network = str(source).startswith(("rtsp://", "rtsps://", "http://", "https://"))
        camera_name = os.getenv(f"SOP_CAMERA_NAME_{camera_id}") or (matched.get("name") if matched else configured_names.get(str(source), f"摄像头{camera_id}"))
        camera_sources.append({"camera_id": camera_id, "camera_name": camera_name, "source": source, "transport": "network" if is_network else "usb"})
    return {
        "ok": True,
        "host": socket.gethostname(),
        "videos": videos,
        "serials": serials,
        "network": interfaces,
        "camera_network_note": "USB/UVC 摄像头没有独立 IP；RTSP/HTTP 网络相机可写入 config/network_cameras.json。串口只用于控制和身份，不能承载视频。局域网客户端统一访问本采集主机。",
        "desktop_root": str(DESKTOP_ROOT),
        "camera_sources": camera_sources,
        "insta360": {
            "present": any("insta" in str(item.get("name", "")).lower() or "insta" in str(item.get("model", "")).lower() for item in videos),
            "message": "影石设备已在 USB/UVC 总线上枚举" if any("insta" in str(item.get("name", "")).lower() or "insta" in str(item.get("model", "")).lower() for item in videos) else "未检测到影石 USB/UVC 枚举；请检查数据线、供电、UVC模式和扩展坞后点击重新扫描",
        },
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
    """Low-latency camera pipeline backed by a capacity-one latest-frame mailbox."""

    def __init__(self, camera_id: int | None = None) -> None:
        self.camera_id = int(os.getenv("SOP_CAMERA_ID", "0")) if camera_id is None else camera_id
        configured_source = os.getenv(f"SOP_CAMERA_SOURCE_{self.camera_id}")
        if configured_source is None and self.camera_id == 0:
            configured_source = os.getenv("SOP_CAMERA_SOURCE")
        self.source = configured_source or DEFAULT_CAMERA_SOURCES.get(self.camera_id, "")
        self.camera_name = os.getenv(f"SOP_CAMERA_NAME_{self.camera_id}", f"摄像头{self.camera_id}")
        default_model = LINE_MODEL_PATHS.get(ACTIVE_LINE_ID, ROOT / "models" / "yolo11n.pt")
        self.model_path = Path(os.getenv("SOP_CAMERA_MODEL", str(default_model)))
        self.device = os.getenv("SOP_CAMERA_DEVICE", "0")
        self.confidence = float(os.getenv("SOP_CAMERA_CONFIDENCE", "0.35"))
        self.max_fps = float(os.getenv("SOP_CAMERA_MAX_FPS", "15"))
        self.inference_fps = max(0.5, float(os.getenv("SOP_CAMERA_INFERENCE_FPS", "5")))
        self.width = int(os.getenv(f"SOP_CAMERA_WIDTH_{self.camera_id}", os.getenv("SOP_CAMERA_WIDTH", "1280")))
        self.height = int(os.getenv(f"SOP_CAMERA_HEIGHT_{self.camera_id}", os.getenv("SOP_CAMERA_HEIGHT", "720")))
        self.capture_fps = float(os.getenv(f"SOP_CAMERA_CAPTURE_FPS_{self.camera_id}", "30"))
        self.gpu_wait_timeout = max(0.0, float(os.getenv("SOP_CAMERA_GPU_WAIT_MS", "25")) / 1000.0)
        self.read_failure_limit = max(2, int(os.getenv("SOP_CAMERA_READ_FAILURE_LIMIT", "12")))
        self.reconnect_delay = max(0.2, float(os.getenv("SOP_CAMERA_RECONNECT_DELAY", "1.0")))
        self.stream_width = max(0, int(os.getenv("SOP_CAMERA_STREAM_WIDTH", "960")))
        self.jpeg_quality = min(95, max(40, int(os.getenv("SOP_CAMERA_JPEG_QUALITY", "68"))))
        self.output_root = EVIDENCE_ROOT
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None
        self._capture_thread: threading.Thread | None = None
        self._generation = 0
        self._capture = None
        self._model = None
        self._loaded_model_path: Path | None = None
        self._latest_raw_frame = None
        self._latest_raw_at = 0.0
        self._raw_sequence = 0
        self._processed_raw_sequence = 0
        self._captured_frames = 0
        self._dropped_frames = 0
        self._capture_fps_ema = 0.0
        self._output_fps_ema = 0.0
        self._reconnects = 0
        self._read_failures = 0
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
            "camera_name": self.camera_name,
            "model_path": str(self.model_path),
            "source": self.source,
            "pixel_format": "MJPG",
            "capture_fps": self.capture_fps,
            "inference_fps_target": self.inference_fps,
            "stream_width": self.stream_width,
            "jpeg_quality": self.jpeg_quality,
            "device": self.device,
            "fps": 0.0,
            "capture_fps_actual": 0.0,
            "output_fps": 0.0,
            "inference_ms": 0.0,
            "pipeline_ms": 0.0,
            "gpu_wait_ms": 0.0,
            "inference_age_ms": None,
            "dropped_frames": 0,
            "read_failures": 0,
            "reconnects": 0,
            "queue_capacity": 1,
            "queue_depth": 0,
            "buffering_strategy": "latest-frame-mailbox",
            "scheduler": "15fps-display/5fps-inference/25ms-bounded-gpu-wait/latest-box-reuse",
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

    def _open_capture(self):
        import cv2

        source = self._source(self.source)
        if isinstance(source, str) and source.startswith("/dev/"):
            capture = cv2.VideoCapture(source, cv2.CAP_V4L2)
        elif isinstance(source, str) and source.startswith(("rtsp://", "rtsps://", "http://", "https://")):
            capture = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
        else:
            capture = cv2.VideoCapture(source)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f"无法打开摄像头: {self.source}")
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
            capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000)
        if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
            capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 2000)
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        capture.set(cv2.CAP_PROP_FPS, min(self.capture_fps or 30, 30))
        return capture

    def start(self) -> None:
        with self._lock:
            if self._status.get("running") and self._thread and self._thread.is_alive() and self._capture_thread and self._capture_thread.is_alive():
                return
            try:
                import cv2
                from ultralytics import YOLO

                if not self.source:
                    raise RuntimeError(f"摄像头槽位{self.camera_id}尚未绑定视频采集设备")
                if not self.model_path.exists():
                    raise FileNotFoundError(f"YOLOv11模型不存在: {self.model_path}")
                capture = self._open_capture()
                self._capture = capture
                if self._model is None or self._loaded_model_path != self.model_path:
                    self._model = YOLO(str(self.model_path))
                    self._loaded_model_path = self.model_path
                self._latest_raw_frame = None
                self._latest_jpeg = None
                self._sequence = 0
                self._raw_sequence = 0
                self._processed_raw_sequence = 0
                self._captured_frames = 0
                self._dropped_frames = 0
                self._capture_fps_ema = 0.0
                self._output_fps_ema = 0.0
                self._read_failures = 0
                self._status.update({
                    "running": True, "error": None, "started_at": time.time(),
                    "dropped_frames": 0, "queue_depth": 0, "pipeline_ms": 0.0,
                    "read_failures": 0, "reconnecting": False,
                })
                self._generation += 1
                generation = self._generation
                self._capture_thread = threading.Thread(target=self._capture_loop, args=(generation,), name=f"camera-capture-{self.camera_id}", daemon=True)
                self._thread = threading.Thread(target=self._run, args=(generation,), name=f"camera-inference-{self.camera_id}", daemon=True)
                self._capture_thread.start()
                self._thread.start()
            except Exception as exc:
                self._status.update({"running": False, "error": str(exc)})
                self._release_capture()
                raise

    def stop(self) -> None:
        with self._lock:
            self._status["running"] = False
            self._generation += 1
            self._status["error"] = None
            self._condition.notify_all()
            self._finalize_recording_locked()
            self._release_capture()
            self._latest_raw_frame = None
            self._status["queue_depth"] = 0
            self._condition.notify_all()
            capture_thread = self._capture_thread
            inference_thread = self._thread
        current = threading.current_thread()
        for thread in (capture_thread, inference_thread):
            if thread and thread is not current and thread.is_alive():
                thread.join(timeout=3.0)

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

    @staticmethod
    def _ema(previous: float, current: float, alpha: float = 0.16) -> float:
        return current if previous <= 0 else previous + alpha * (current - previous)

    def _capture_loop(self, generation: int) -> None:
        previous_at = 0.0
        capture = self._capture
        consecutive_failures = 0
        while True:
            with self._lock:
                if generation != self._generation or not self._status.get("running"):
                    break
                capture = self._capture
            if capture is None:
                try:
                    capture = self._open_capture()
                except Exception as exc:
                    with self._condition:
                        self._status.update({
                            "running": True,
                            "reconnecting": True,
                            "error": f"摄像头重连中: {exc}",
                        })
                        self._condition.notify_all()
                    time.sleep(self.reconnect_delay)
                    continue
                with self._condition:
                    if generation != self._generation or not self._status.get("running"):
                        capture.release()
                        break
                    self._capture = capture
                    self._reconnects += 1
                    consecutive_failures = 0
                    previous_at = 0.0
                    self._status.update({
                        "reconnecting": False,
                        "reconnects": self._reconnects,
                        "error": None,
                    })
                    self._condition.notify_all()
            try:
                ok, frame = capture.read()
            except Exception as exc:
                ok, frame = False, None
                failure_message = f"摄像头坏帧或正在重枚举: {exc}"
            else:
                failure_message = "摄像头返回空帧"
            captured_at = time.perf_counter()
            if not ok or frame is None or getattr(frame, "size", 0) == 0:
                consecutive_failures += 1
                self._read_failures += 1
                with self._condition:
                    self._status.update({
                        "read_failures": self._read_failures,
                        "error": f"{failure_message}，正在恢复（{consecutive_failures}/{self.read_failure_limit}）",
                    })
                    self._condition.notify_all()
                if consecutive_failures < self.read_failure_limit:
                    time.sleep(0.03)
                    continue
                with self._condition:
                    if self._capture is capture:
                        capture.release()
                        self._capture = None
                    self._status["reconnecting"] = True
                    self._condition.notify_all()
                capture = None
                consecutive_failures = 0
                time.sleep(self.reconnect_delay)
                continue
            consecutive_failures = 0
            with self._condition:
                if generation != self._generation or not self._status.get("running") or self._capture is not capture:
                    break
                if self._raw_sequence > self._processed_raw_sequence:
                    self._dropped_frames += 1
                self._latest_raw_frame = frame
                self._latest_raw_at = captured_at
                self._raw_sequence += 1
                self._captured_frames += 1
                if previous_at > 0 and captured_at > previous_at:
                    self._capture_fps_ema = self._ema(self._capture_fps_ema, 1.0 / (captured_at - previous_at))
                previous_at = captured_at
                self._status.update({
                    "capture_fps_actual": round(self._capture_fps_ema, 1),
                    "dropped_frames": self._dropped_frames,
                    "queue_depth": 1,
                    "reconnecting": False,
                    "error": None,
                })
                self._condition.notify_all()
        with self._lock:
            if self._capture is capture and (generation != self._generation or not self._status.get("running")):
                self._finalize_recording_locked()
                self._release_capture()
            self._condition.notify_all()

    def _run(self, generation: int) -> None:
        import cv2

        last_output_at = 0.0
        last_inference_at = 0.0
        last_raw_sequence = 0
        cached_detections: list[dict[str, object]] = []
        inference_ms = 0.0
        gpu_wait_ms = 0.0
        while True:
            loop_started = time.perf_counter()
            with self._condition:
                self._condition.wait_for(
                    lambda: self._raw_sequence > last_raw_sequence or generation != self._generation or not self._status.get("running"),
                    timeout=2.0,
                )
                capture = self._capture
                model = self._model
                if generation != self._generation or not self._status.get("running") or model is None or self._raw_sequence <= last_raw_sequence:
                    if generation != self._generation or not self._status.get("running"):
                        break
                    continue
                frame = self._latest_raw_frame
                captured_at = self._latest_raw_at
                raw_sequence = self._raw_sequence
                self._processed_raw_sequence = raw_sequence
                self._status["queue_depth"] = 0
            if frame is None:
                continue
            now = time.perf_counter()
            should_infer = not cached_detections or now - last_inference_at >= 1.0 / self.inference_fps
            annotated = frame.copy()
            if should_infer:
                wait_started = time.perf_counter()
                acquired_inference_slot = CAMERA_INFERENCE_GATE.acquire(timeout=self.gpu_wait_timeout)
                gpu_wait_ms = (time.perf_counter() - wait_started) * 1000
                if acquired_inference_slot:
                    started = time.perf_counter()
                    try:
                        quantize = None if self.device == "cpu" else 16
                        result = model.predict(source=frame, imgsz=640, conf=self.confidence, device=self.device, quantize=quantize, max_det=50, verbose=False)[0]
                        inference_ms = (time.perf_counter() - started) * 1000
                        cached_detections = []
                        names = getattr(result, "names", {}) or {}
                        for box in result.boxes or []:
                            class_id = int(box.cls.item())
                            cached_detections.append({
                                "xyxy": [int(value) for value in box.xyxy[0].tolist()],
                                "label": str(names.get(class_id, class_id)),
                                "confidence": float(box.conf.item()),
                            })
                        last_inference_at = time.perf_counter()
                    except Exception as exc:
                        with self._condition:
                            self._status["inference_error"] = str(exc)
                            self._condition.notify_all()
                    finally:
                        CAMERA_INFERENCE_GATE.release()
            for detection in cached_detections:
                x1, y1, x2, y2 = detection["xyxy"]
                color = (66, 214, 174)
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
                label = f"{detection['label']} {float(detection['confidence']):.2f}"
                cv2.putText(annotated, label, (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 1, cv2.LINE_AA)
            try:
                detections = len(cached_detections)
                stream_frame = annotated
                if self.stream_width and annotated.shape[1] > self.stream_width:
                    stream_height = round(annotated.shape[0] * self.stream_width / annotated.shape[1])
                    stream_frame = cv2.resize(annotated, (self.stream_width, stream_height), interpolation=cv2.INTER_AREA)
                encoded, buffer = cv2.imencode(".jpg", stream_frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
                if not encoded:
                    last_raw_sequence = raw_sequence
                    continue
                completed_at = time.perf_counter()
                pipeline_ms = (completed_at - captured_at) * 1000
                if last_output_at > 0 and completed_at > last_output_at:
                    self._output_fps_ema = self._ema(self._output_fps_ema, 1.0 / (completed_at - last_output_at))
                last_output_at = completed_at
                last_raw_sequence = raw_sequence
                with self._lock:
                    if generation != self._generation or not self._status.get("running"):
                        break
                    self._latest_jpeg = buffer.tobytes()
                    self._sequence += 1
                    if self._recording:
                        if self._record_writer is None:
                            self._record_writer = cv2.VideoWriter(
                                str(self._record_path), cv2.VideoWriter_fourcc(*"mp4v"),
                                max(1.0, min(self.max_fps or 12.0, 30.0)),
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
                        "pipeline_ms": round(pipeline_ms, 1),
                        "gpu_wait_ms": round(gpu_wait_ms, 1),
                        "inference_age_ms": round((completed_at - last_inference_at) * 1000, 1) if last_inference_at else None,
                        "inference_fps_target": self.inference_fps,
                        "fps": round(self._output_fps_ema, 1),
                        "output_fps": round(self._output_fps_ema, 1),
                        "capture_fps_actual": round(self._capture_fps_ema, 1),
                        "dropped_frames": self._dropped_frames,
                        "width": int(frame.shape[1]),
                        "height": int(frame.shape[0]),
                        "stream_output": f"{stream_frame.shape[1]}x{stream_frame.shape[0]}",
                        "last_frame_at": time.time(),
                        "recording": self._recording,
                        "error": self._status.get("error") if self._status.get("reconnecting") else None,
                    })
                    self._condition.notify_all()
            except Exception as exc:
                with self._lock:
                    if generation == self._generation:
                        self._status.update({"running": False, "error": f"视频编码失败: {exc}"})
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
        try:
            handler.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)
            handler.connection.settimeout(2.0)
        except OSError:
            pass
        last_sequence = 0
        try:
            while True:
                with self._condition:
                    changed = self._condition.wait_for(
                        lambda: self._sequence > last_sequence or not self._status.get("running"),
                        timeout=2.0,
                    )
                    if not changed or self._sequence <= last_sequence:
                        if not self._status.get("running"):
                            break
                        continue
                    jpeg = self._latest_jpeg
                    last_sequence = self._sequence
                    if jpeg is None:
                        if not self._status.get("running"):
                            break
                        continue
                header = (
                    b"--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-store\r\nX-Frame-Sequence: "
                    + str(last_sequence).encode() + b"\r\nContent-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                )
                handler.wfile.write(header + jpeg + b"\r\n")
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError, socket.timeout):
            pass


LIVE_CAMERAS = {camera_id: LiveCameraService(camera_id) for camera_id in range(CAMERA_SLOT_COUNT)}


def refresh_camera_services() -> dict[int, str]:
    """Re-scan hot-plugged UVC/network sources without disrupting running streams."""
    discovered = _discover_camera_sources()
    DEFAULT_CAMERA_SOURCES.clear()
    DEFAULT_CAMERA_SOURCES.update(discovered)
    for camera_id, service in LIVE_CAMERAS.items():
        with service._lock:
            if service._status.get("running"):
                continue
            configured = os.getenv(f"SOP_CAMERA_SOURCE_{camera_id}")
            source = configured or discovered.get(camera_id, "")
            service.source = source
            service.camera_name = os.getenv(f"SOP_CAMERA_NAME_{camera_id}", f"摄像头{camera_id}")
            service._status.update({"source": source, "camera_name": service.camera_name, "error": None if source else "摄像头槽位尚未绑定设备"})
    return discovered


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


def pcb_model_registry() -> dict[str, object]:
    registry = read_config(PCB_MODEL_REGISTRY_PATH, {"models": []})
    registry = dict(registry) if isinstance(registry, dict) else {"models": []}
    selected = read_config(ACTIVE_MODEL_SELECTION_PATH, {})
    selected_id = selected.get("model_id") if isinstance(selected, dict) else None
    enriched = []
    for configured in registry.get("models", []):
        item = dict(configured)
        weight = ROOT / str(item.get("weight", ""))
        report_path = ROOT / str(item.get("report", ""))
        report = read_config(report_path, {}) if report_path.is_file() else {}
        test = report.get("test_teacher_agreement", {}) if isinstance(report, dict) else {}
        item.update({
            "weight_exists": weight.is_file(),
            "report_exists": report_path.is_file(),
            "selectable": weight.is_file() and report_path.is_file(),
            "selected": item.get("id") == selected_id,
            "metrics": {
                "precision": test.get("metrics/precision(B)"),
                "recall": test.get("metrics/recall(B)"),
                "map50": test.get("metrics/mAP50(B)"),
                "map50_95": test.get("metrics/mAP50-95(B)"),
            },
        })
        enriched.append(item)
    registry["models"] = enriched
    registry["selected_model_id"] = selected_id
    comparison_path = ROOT / "qa" / "pcb_model_comparison_report.json"
    registry["comparison"] = read_config(comparison_path, {}) if comparison_path.is_file() else {}
    return registry


def restore_selected_pcb_model() -> None:
    registry = pcb_model_registry()
    selected = next((item for item in registry.get("models", []) if item.get("selected") and item.get("selectable")), None)
    if selected is None:
        return
    model_path = ROOT / str(selected["weight"])
    LINE_MODEL_PATHS["pcb"] = model_path
    for service in LIVE_CAMERAS.values():
        service.model_path = model_path
        service._status.update({"model_path": str(model_path), "model": selected.get("name")})


def cvat_token() -> str:
    environment_token = os.getenv("CVAT_TOKEN", "").strip()
    if environment_token:
        return environment_token
    token_path = RUNTIME_ROOT / "cvat_token"
    try:
        return token_path.read_text(encoding="utf-8").strip() if token_path.is_file() else ""
    except OSError:
        return ""


def cvat_config() -> dict[str, object]:
    url = os.getenv("CVAT_PUBLIC_URL", os.getenv("CVAT_URL", f"http://{primary_lan_address()}:8081")).rstrip("/")
    api_url = os.getenv("CVAT_API_URL", "http://localhost:8081").rstrip("/")
    token = cvat_token()
    return {"url": url, "api_url": api_url, "token_configured": bool(token)}


def cvat_bulk_status() -> dict[str, object]:
    state_path = RUNTIME_ROOT / "cvat_bulk_upload_state.jsonl"
    project = read_config(RUNTIME_ROOT / "cvat_bulk_project.json", {})
    latest: dict[str, dict] = {}
    for item in read_jsonl(state_path):
        source = str(item.get("source") or "")
        if source and source != "__batch__":
            latest[source] = item
    counts: dict[str, int] = {}
    for item in latest.values():
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    config = cvat_config()
    bulk_items = []
    for item in sorted(latest.values(), key=lambda value: str(value.get("recorded_at", "")), reverse=True):
        task_id = item.get("task_id")
        if task_id is None:
            continue
        bulk_items.append({
            "local_task_id": f"CVAT-BULK-{task_id}", "cvat_task_id": task_id,
            "name": f"全量视频｜{item.get('relative') or Path(str(item.get('source'))).name}",
            "dataset_id": "full-video-corpus", "line_id": "pcb", "mode": "bulk-video",
            "status": item.get("status"), "url": f"{config['url']}/tasks/{task_id}",
            "labels": [], "created_at": item.get("recorded_at", ""), "updated_at": item.get("recorded_at", ""),
        })
        if len(bulk_items) >= 50:
            break
    return {"project": project, "counts": counts, "total_seen": len(latest), "items": bulk_items, "state_path": str(state_path)}


def probe_url(url: str) -> bool:
    try:
        with build_opener(ProxyHandler({})).open(url, timeout=4.0) as response:
            return 200 <= response.status < 500
    except Exception:
        return False


def _process_is_running(pid: object) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def ai_prelabel_status() -> dict[str, object]:
    status = read_config(AI_PRELABEL_STATUS_PATH, {})
    videos = video_catalog().get("videos", [])
    available = []
    missing_video_ids = []
    for item in videos:
        try:
            annotation_video_path(str(item.get("id")))
            available.append(item)
        except ValueError:
            missing_video_ids.append(str(item.get("id")))
    total = sum((max(0, int(item.get("frames") or 0) - 1) // 5) + 1 for item in available)
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        completed = int(connection.execute("SELECT COUNT(*) FROM ai_prelabel_frames").fetchone()[0])
        detections = int(connection.execute("SELECT COALESCE(SUM(detection_count), 0) FROM ai_prelabel_frames").fetchone()[0])
    running = _process_is_running(status.get("pid"))
    state_name = str(status.get("status") or "not_started")
    if state_name in {"running", "loading_model", "queued"} and not running:
        state_name = "interrupted"
    return {
        **status,
        "ok": True,
        "status": state_name,
        "running": running,
        "stride": 5,
        "videos_total": len(videos),
        "videos_available": len(available),
        "missing_video_ids": missing_video_ids,
        "sampled_frames_total": total,
        "sampled_frames_completed": completed,
        "detections_total": detections,
        "progress": round(min(100, completed * 100 / max(1, total)), 2),
        "truth_policy": "AI框均为待人工复核候选；开放词汇不能保证识别未知物体，漏检须人工补框。",
    }


def control_ai_prelabel(action: str, operator: str) -> dict[str, object]:
    global AI_PRELABEL_PROCESS
    if action not in {"start", "pause", "resume"}:
        raise ValueError("AI预标注操作无效")
    with AI_PRELABEL_LOCK:
        status = ai_prelabel_status()
        if action == "pause":
            if not status["running"]:
                raise ValueError("AI预标注任务当前没有运行")
            atomic_write_json(AI_PRELABEL_STATUS_PATH, {**status, "control": "pause", "message": "正在完成当前批次后暂停", "operator": operator})
            return ai_prelabel_status()
        if status["running"]:
            return status
        log_path = RUNTIME_ROOT / "ai_prelabel.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("ab", buffering=0)
        command = [sys.executable, str(ROOT / "scripts" / "prelabel_all_videos.py"), "--stride", "5"]
        AI_PRELABEL_PROCESS = subprocess.Popen(command, cwd=str(ROOT), stdout=log_handle, stderr=subprocess.STDOUT)
        queued = {
            **status,
            "status": "queued",
            "control": "run",
            "pid": AI_PRELABEL_PROCESS.pid,
            "operator": operator,
            "message": "AI预标注任务已启动，正在加载模型",
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        atomic_write_json(AI_PRELABEL_STATUS_PATH, queued)
        return ai_prelabel_status()


def cloud_sync_status() -> dict[str, object]:
    with CLOUD_SYNC_LOCK:
        jobs = sorted(CLOUD_SYNC_JOBS.values(), key=lambda item: str(item.get("created_at", "")), reverse=True)[:20]
    latest_exports = sorted(ANNOTATION_EXPORT_ROOT.glob("*.mp4"), key=lambda path: path.stat().st_mtime, reverse=True) if ANNOTATION_EXPORT_ROOT.exists() else []
    cached_files = [path for path in CLOUD_INBOX_ROOT.rglob("*") if path.is_file()] if CLOUD_INBOX_ROOT.exists() else []
    return {
        "ok": True,
        "google": {
            "source_url": GOOGLE_SOURCE_FOLDER_URL,
            "output_url": GOOGLE_OUTPUT_FOLDER_URL,
            "rclone_installed": bool(which("rclone")),
            "source_configured": bool(which("rclone") and os.getenv("SOP_GOOGLE_SOURCE", "").strip()),
            "output_configured": bool(which("rclone") and os.getenv("SOP_GOOGLE_OUTPUT", "").strip()),
        },
        "baidu": {
            "source_url": BAIDU_SOURCE_URL,
            "configured": bool(os.getenv("SOP_BAIDU_SYNC_COMMAND", "").strip()),
            "upload_configured": bool(os.getenv("SOP_BAIDU_UPLOAD_COMMAND", "").strip()),
        },
        "local": {
            "inbox": str(CLOUD_INBOX_ROOT), "cached_files": len(cached_files),
            "video_cache": str(ANNOTATION_VIDEO_CACHE_ROOT),
            "latest_export": str(latest_exports[0]) if latest_exports else None,
            "exports": len(latest_exports),
        },
        "jobs": jobs,
        "security": "网盘仅使用服务器侧 OAuth/rclone 令牌；账号密码不会写入网页、源码或日志。",
    }


def _update_cloud_job(job_id: str, **values: object) -> None:
    with CLOUD_SYNC_LOCK:
        CLOUD_SYNC_JOBS.setdefault(job_id, {}).update(values)


def run_cloud_sync_job(job_id: str, action: str) -> None:
    try:
        CLOUD_INBOX_ROOT.mkdir(parents=True, exist_ok=True)
        if action == "pull_google":
            remote = os.getenv("SOP_GOOGLE_SOURCE", "").strip()
            if not which("rclone") or not remote:
                raise RuntimeError("Google Drive 尚未完成 rclone OAuth 和 SOP_GOOGLE_SOURCE 配置")
            command = ["rclone", "copy", remote, str(CLOUD_INBOX_ROOT / "google"), "--create-empty-src-dirs"]
        elif action == "push_google":
            remote = os.getenv("SOP_GOOGLE_OUTPUT", "").strip()
            if not which("rclone") or not remote:
                raise RuntimeError("Google Drive 尚未完成 rclone OAuth 和 SOP_GOOGLE_OUTPUT 配置")
            exports = list(ANNOTATION_EXPORT_ROOT.glob("*.mp4")) if ANNOTATION_EXPORT_ROOT.exists() else []
            if not exports:
                raise RuntimeError("尚无标注成片，请先点击“生成标注成片”")
            command = ["rclone", "copy", str(ANNOTATION_EXPORT_ROOT), remote, "--include", "*.mp4", "--include", "*.json"]
        elif action == "pull_baidu":
            configured = os.getenv("SOP_BAIDU_SYNC_COMMAND", "").strip()
            if not configured:
                raise RuntimeError("百度网盘同步器尚未授权；请先配置 SOP_BAIDU_SYNC_COMMAND")
            command = [part.replace("{destination}", str(CLOUD_INBOX_ROOT / "baidu")) for part in shlex.split(configured)]
        elif action == "push_baidu":
            configured = os.getenv("SOP_BAIDU_UPLOAD_COMMAND", "").strip()
            if not configured:
                raise RuntimeError("百度网盘上传器尚未授权；请先配置 SOP_BAIDU_UPLOAD_COMMAND")
            exports = list(ANNOTATION_EXPORT_ROOT.glob("*.mp4")) if ANNOTATION_EXPORT_ROOT.exists() else []
            if not exports:
                raise RuntimeError("尚无标注成片，请先点击“生成标注成片”")
            command = [part.replace("{source}", str(ANNOTATION_EXPORT_ROOT)) for part in shlex.split(configured)]
        else:
            raise RuntimeError("不支持的云盘同步操作")
        _update_cloud_job(job_id, status="running", message="同步进行中")
        completed = subprocess.run(command, capture_output=True, text=True, timeout=7200, check=False)
        if completed.returncode:
            raise RuntimeError((completed.stderr or completed.stdout or "同步失败")[-1200:])
        imported = ingest_cloud_videos() if action.startswith("pull_") else []
        _update_cloud_job(job_id, status="completed", finished_at=time.strftime("%Y-%m-%d %H:%M:%S"), message=f"同步完成，当前云盘视频 {len(imported)} 段")
    except Exception as exc:
        _update_cloud_job(job_id, status="failed", finished_at=time.strftime("%Y-%m-%d %H:%M:%S"), message=str(exc))


def start_cloud_sync(action: str, operator: str) -> dict[str, object]:
    job_id = f"CLOUD-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}"
    job = {"job_id": job_id, "action": action, "operator": operator, "status": "queued", "message": "等待同步", "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with CLOUD_SYNC_LOCK:
        CLOUD_SYNC_JOBS[job_id] = job
    threading.Thread(target=run_cloud_sync_job, args=(job_id, action), daemon=True, name=job_id).start()
    return job


def cvat_task_create(payload: dict[str, object]) -> dict[str, object]:
    config = cvat_config()
    url = str(config["url"])
    api_url = str(config["api_url"])
    token = cvat_token()
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
    request = Request(f"{api_url}/api/tasks", data=body, method="POST", headers={"Content-Type": "application/json", "Authorization": f"Token {token}"})
    with build_opener(ProxyHandler({})).open(request, timeout=8) as response:
        result = json.loads(response.read().decode("utf-8"))
    task_id = result.get("id")
    video_id = str(payload.get("video_id") or "").strip()
    upload_video = bool(payload.get("upload_video", True)) and video_id
    status = "已创建"
    message = "CVAT任务已创建"
    if upload_video and task_id is not None:
        info = video_info(video_id)
        if info is None:
            status, message = "任务已创建/视频不存在", f"CVAT任务已创建，但平台视频不存在: {video_id}"
        else:
            video_path = (WEB_ROOT / str(info.get("source_video") or info.get("video") or "")).resolve()
            if not video_path.is_file() or WEB_ROOT.resolve() not in video_path.parents:
                status, message = "任务已创建/待上传", "CVAT任务已创建，但整段代理视频尚未生成"
            else:
                try:
                    import requests

                    with video_path.open("rb") as handle:
                        response = requests.post(
                            f"{api_url}/api/tasks/{task_id}/data",
                            headers={"Authorization": f"Token {token}"},
                            data={"image_quality": "90", "use_zip_chunks": "true"},
                            files={"client_files[0]": (video_path.name, handle, "video/mp4")},
                            timeout=(15, 1800),
                        )
                    response.raise_for_status()
                    status = "整段视频处理中"
                    message = f"CVAT任务已创建，整段视频 {video_path.name} 已提交"
                except Exception as exc:
                    status = "任务已创建/视频上传失败"
                    message = f"CVAT任务已创建，但整段视频上传失败: {exc}"
    return {"ok": True, "mode": "api", "task_id": task_id, "url": f"{url}/tasks/{task_id}", "status": status, "message": message}


def cvat_push_annotations(task_id: int, video_id: str) -> dict[str, object]:
    token = cvat_token()
    if not token:
        raise ValueError("CVAT 尚未配置接口令牌")
    if video_info(video_id) is None:
        raise ValueError("视频不存在")
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("缺少 CVAT 同步依赖 requests") from exc
    api_url = str(cvat_config()["api_url"])
    headers = {"Authorization": f"Token {token}", "Content-Type": "application/json"}
    labels_response = requests.get(
        f"{api_url}/api/labels", headers=headers, params={"task_id": task_id, "page_size": 1000}, timeout=20,
    )
    labels_response.raise_for_status()
    label_payload = labels_response.json()
    labels = label_payload.get("results", []) if isinstance(label_payload, dict) else label_payload
    label_ids = {str(item.get("name")): int(item["id"]) for item in labels if item.get("id") is not None}
    width, height = video_size(video_id)
    source_items = [
        item for item in manual_annotation_items(video_id)
        if item.get("review_status") != "rejected" and item.get("label") in label_ids
    ]
    shapes = []
    for item in source_items:
        x1, y1, x2, y2 = [float(value) for value in item.get("box", [0, 0, 0, 0])]
        shapes.append({
            "type": "rectangle",
            "frame": int(item.get("frame", 0)),
            "label_id": label_ids[str(item["label"])],
            "points": [round(x1 * width, 2), round(y1 * height, 2), round(x2 * width, 2), round(y2 * height, 2)],
            "occluded": False,
            "outside": False,
            "z_order": 0,
            "rotation": 0,
            "attributes": [],
            "source": "auto" if item.get("source_kind") in {"prelabel", "candidate"} else "manual",
        })
    response = requests.put(
        f"{api_url}/api/tasks/{task_id}/annotations",
        headers=headers,
        json={"version": 0, "tags": [], "shapes": shapes, "tracks": []},
        timeout=120,
    )
    response.raise_for_status()
    total = len(manual_annotation_items(video_id))
    return {
        "ok": True,
        "task_id": task_id,
        "video_id": video_id,
        "uploaded": len(shapes),
        "skipped": max(0, total - len(shapes)),
        "labels_available": sorted(label_ids),
        "url": f"{cvat_config()['url']}/tasks/{task_id}",
        "message": f"已把 {len(shapes)} 个框保存到 CVAT 任务 #{task_id}",
    }


def production_lines() -> list[dict[str, object]]:
    value = read_config(PRODUCTION_LINES_PATH, [])
    return value if isinstance(value, list) else []


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
    "pcb-components": [
        ROOT / "datasets" / "PCB插装0264_YOLOE_ROI增强_待人工复核",
        ROOT / "datasets" / "PCB插装0265_YOLOE_ROI增强_待人工复核",
        ROOT / "datasets" / "PCB插装0264_0265联合_YOLOE_ROI增强_待人工复核",
        ROOT / "datasets" / "PCB插装0264_YOLOE关键帧预标注_待人工复核",
        ROOT / "datasets" / "PCB插装0265_YOLOE关键帧预标注_待人工复核",
        ROOT / "datasets" / "PCB插装0264_0265联合_YOLOE关键帧预标注_待人工复核",
        WEB_ROOT / "assets" / "datasets" / "pcb",
    ],
    "automotive-fasteners": [ROOT / "datasets" / "新增两视频_YOLOE26_SAHI细粒度预标注_待人工复核", ROOT / "datasets" / "三视频多物体预标注_待人工复核"],
    "automotive-sop-ng": [DATA_ROOT],
}

TRAINING_OUTPUT_ROOTS = {
    "platform": RUNTIME_ROOT / "training_jobs",
    "model-store": SPARK_MODEL_ROOT / "training-runs",
    "desktop": DESKTOP_ROOT / "训练结果",
}


def training_dataset_options() -> list[dict[str, object]]:
    items = []
    for dataset_id, candidates in DATASET_LOCAL_HINTS.items():
        for root in candidates:
            yaml_path = root / "data.yaml"
            if not yaml_path.is_file():
                continue
            labels = sum(1 for _ in root.glob("labels/**/*.txt"))
            images = sum(1 for extension in ("*.jpg", "*.jpeg", "*.png", "*.bmp") for _ in root.glob(f"images/**/{extension}"))
            confirmed = (root / ".human_confirmed").is_file() and "待人工复核" not in root.name
            items.append({
                "id": f"{dataset_id}:{root.name}", "catalog_id": dataset_id, "name": root.name,
                "data_yaml": str(yaml_path), "images": images, "labels": labels,
                "truth_ready": confirmed, "truth_status": "人工真值已冻结" if confirmed else "自动候选/待人工复核",
            })
    return items


def resolve_training_dataset(selection: str) -> dict[str, object]:
    option = next((item for item in training_dataset_options() if item["id"] == selection), None)
    if option is None:
        raise ValueError("训练数据集不存在或没有 data.yaml")
    return option


def resolve_training_model(model_id: str) -> Path:
    catalog = read_config(TRAINING_CATALOG_PATH, [])
    entry = next((item for item in catalog if item.get("id") == model_id), None) if isinstance(catalog, list) else None
    if entry is None:
        raise ValueError("未知训练算法")
    if model_id not in {"yolo26n", "yoloe26"}:
        raise ValueError("当前真实训练执行器仅支持 Ultralytics YOLO26/YOLOE；其他算法需安装对应训练后端")
    model_path = Path(str(entry.get("model_path") or ""))
    if not model_path.is_absolute():
        model_path = ROOT / model_path
    if not model_path.is_file():
        raise ValueError(f"预训练权重不存在: {model_path}")
    return model_path


def training_job_dirs() -> list[Path]:
    roots = list(TRAINING_OUTPUT_ROOTS.values())
    return [path for root in roots if root.exists() for path in root.iterdir() if path.is_dir() and (path / "status.json").is_file()]


def training_job(job_id: str) -> dict[str, object] | None:
    for path in training_job_dirs():
        if path.name != job_id:
            continue
        status = read_config(path / "status.json", {})
        log_path = path / "training.log"
        if log_path.is_file():
            try:
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                status["log_tail"] = lines[-80:]
            except OSError:
                status["log_tail"] = []
        package = path / f"{job_id}_deployment.zip"
        status["download_url"] = f"/api/train/jobs/{quote(job_id)}/download" if package.is_file() else None
        return status
    return None


def start_training_job(payload: dict[str, object], operator: str) -> dict[str, object]:
    dataset = resolve_training_dataset(str(payload.get("dataset") or ""))
    truth_mode = str(payload.get("truth_mode") or "human-confirmed")
    if truth_mode == "human-confirmed" and not dataset.get("truth_ready"):
        raise ValueError("该数据集仍是自动候选，不能作为正式真值训练。请先在 CVAT 完成人工复核并冻结版本，或明确选择“候选预训练”。")
    if truth_mode not in {"human-confirmed", "candidate-pretrain"}:
        raise ValueError("未知真值模式")
    model_id = str(payload.get("algorithm") or "yolo26n")
    model_path = resolve_training_model(model_id)
    epochs = max(1, min(500, int(payload.get("epochs", 5))))
    batch = max(1, min(128, int(payload.get("batch", 4))))
    imgsz = max(320, min(2048, int(payload.get("imgsz", 960))))
    workers = max(0, min(16, int(payload.get("workers", 2))))
    patience = max(0, min(100, int(payload.get("patience", min(20, epochs)))))
    seed = int(payload.get("seed", 20260820))
    optimizer = str(payload.get("optimizer") or "auto")
    if optimizer not in {"auto", "SGD", "Adam", "AdamW", "NAdam", "RAdam", "RMSProp"}:
        raise ValueError("未知优化器")
    lr0 = max(0.000001, min(1.0, float(payload.get("lr0", 0.01))))
    weight_decay = max(0.0, min(0.1, float(payload.get("weight_decay", 0.0005))))
    close_mosaic = max(0, min(50, int(payload.get("close_mosaic", min(2, epochs)))))
    freeze = max(0, min(50, int(payload.get("freeze", 0))))
    amp = bool(payload.get("amp", True))
    cache = str(payload.get("cache") or "false")
    if cache not in {"false", "ram", "disk"}:
        raise ValueError("缓存模式只允许 false、ram 或 disk")
    device = str(payload.get("device") or "0")
    if device not in {"0", "1", "cpu"}:
        raise ValueError("运行设备只允许 GPU 0、GPU 1 或 CPU")
    output_key = str(payload.get("output") or "platform")
    output_root = TRAINING_OUTPUT_ROOTS.get(output_key)
    if output_root is None:
        raise ValueError("未知输出位置")
    output_root.mkdir(parents=True, exist_ok=True)
    job_id = f"TRAIN-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}"
    job_dir = output_root / job_id
    job_dir.mkdir()
    image_count = int(dataset.get("images") or dataset.get("labels") or 0)
    seconds_mid = max(90, int(max(1, image_count) * epochs * (imgsz / 640) ** 2 / (max(1, batch) * 5.5)))
    status = {
        "ok": True, "job_id": job_id, "status": "queued", "stage": "排队", "progress": 0,
        "operator": operator, "algorithm": model_id, "model_path": str(model_path), "dataset": dataset,
        "truth_mode": truth_mode, "parameters": {"epochs": epochs, "batch": batch, "imgsz": imgsz, "workers": workers, "patience": patience, "seed": seed, "device": device, "optimizer": optimizer, "lr0": lr0, "weight_decay": weight_decay, "close_mosaic": close_mosaic, "freeze": freeze, "amp": amp, "cache": cache},
        "output_dir": str(job_dir), "target": str(payload.get("target") or "jetson"),
        "estimated_seconds": seconds_mid, "estimated_range_seconds": [int(seconds_mid * 0.65), int(seconds_mid * 1.6)],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "truth_notice": "human-confirmed 才可作为量产精度；candidate-pretrain 只衡量候选标签一致性。",
    }
    (job_dir / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    command = [
        sys.executable, str(ROOT / "scripts" / "run_training_job.py"), "--job-dir", str(job_dir),
        "--data", str(dataset["data_yaml"]), "--model", str(model_path), "--epochs", str(epochs),
        "--batch", str(batch), "--imgsz", str(imgsz), "--device", device, "--workers", str(workers),
        "--patience", str(patience), "--seed", str(seed), "--target", str(payload.get("target") or "jetson"),
        "--truth-mode", truth_mode, "--optimizer", optimizer, "--lr0", str(lr0), "--weight-decay", str(weight_decay),
        "--close-mosaic", str(close_mosaic), "--freeze", str(freeze), "--amp", "true" if amp else "false", "--cache", cache,
    ]
    log_handle = (job_dir / "training.log").open("a", encoding="utf-8")
    process = subprocess.Popen(command, cwd=ROOT, stdout=log_handle, stderr=subprocess.STDOUT, start_new_session=True)
    log_handle.close()
    status["pid"] = process.pid
    temporary = job_dir / "status.tmp"
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(job_dir / "status.json")
    return status


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
    catalog = json.loads((DATA_ROOT / "videos.json").read_text(encoding="utf-8"))
    cloud_manifest = read_config(RUNTIME_ROOT / "cloud_video_catalog.json", {"videos": []})
    cloud_videos = cloud_manifest.get("videos", []) if isinstance(cloud_manifest, dict) else []
    known_ids = {str(item.get("id")) for item in catalog.get("videos", [])}
    additions = [item for item in cloud_videos if str(item.get("id")) not in known_ids]
    if additions:
        catalog["videos"] = [*catalog.get("videos", []), *additions]
        totals = catalog.setdefault("totals", {})
        totals["videos"] = len(catalog["videos"])
        totals["duration_s"] = round(sum(float(item.get("duration_s", 0)) for item in catalog["videos"]), 2)
        totals["frames"] = sum(int(item.get("frames", 0)) for item in catalog["videos"])
        totals["steps"] = sum(len(item.get("steps", [])) for item in catalog["videos"])
    return catalog


def ingest_cloud_videos() -> list[dict[str, object]]:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("加载云盘视频需要 opencv-python") from exc
    media_root = WEB_ROOT / "media" / "cloud"
    media_root.mkdir(parents=True, exist_ok=True)
    existing = read_config(RUNTIME_ROOT / "cloud_video_catalog.json", {"videos": []})
    existing_items = existing.get("videos", []) if isinstance(existing, dict) else []
    by_source = {str(item.get("cloud_source")): item for item in existing_items}
    supported = {".mp4", ".mov", ".mkv", ".avi", ".m4v"}
    for source in sorted(CLOUD_INBOX_ROOT.rglob("*")) if CLOUD_INBOX_ROOT.exists() else []:
        if not source.is_file() or source.suffix.lower() not in supported:
            continue
        source_key = str(source.resolve())
        if source_key in by_source:
            continue
        digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:12]
        destination = media_root / f"{digest}_{source.name}"
        if not destination.exists():
            try:
                os.link(source, destination)
            except OSError:
                shutil.copy2(source, destination)
        capture = cv2.VideoCapture(str(destination))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 30)
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720)
        capture.release()
        duration = frames / fps if fps > 0 else 0
        boundaries = [0.0, duration * 0.2, duration * 0.5, duration * 0.82, duration]
        labels = ["取料与准备流程", "对位、插电或上料流程", "装配、测试、烧录或紧固流程", "标签与完成复核"]
        steps = [
            {"id": f"S{index + 1:02d}", "label": label, "start_s": round(boundaries[index], 3), "end_s": round(boundaries[index + 1], 3), "roi": [0.05, 0.05, 0.95, 0.95]}
            for index, label in enumerate(labels)
        ]
        relative = str(destination.relative_to(WEB_ROOT))
        item = {
            "id": f"video_cloud_{digest}", "display_name": f"每日云盘视频｜{source.name}",
            "video": relative, "source_video": relative, "presentation_video": relative,
            "duration_s": round(duration, 3), "fps": round(fps, 3), "frames": frames,
            "resolution": f"{width}x{height}", "presentation_resolution": f"{width}x{height}",
            "algorithm": "待标注 · 流程优先", "steps": steps, "cloud_source": source_key,
            "truth_policy": "云盘新视频需先核对流程、手势、测试屏幕、烧录/插电、标签和紧固步骤。",
        }
        by_source[source_key] = item
    videos = sorted(by_source.values(), key=lambda item: str(item.get("cloud_source")))
    atomic_write_json(RUNTIME_ROOT / "cloud_video_catalog.json", {"videos": videos, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    return videos


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


def annotation_video_path(video_id: str) -> Path:
    info = video_info(video_id)
    if info is None:
        raise ValueError("视频不存在")
    candidates = []
    for key in ("source_video", "video"):
        value = str(info.get(key) or "").strip()
        if value:
            candidates.append(WEB_ROOT / value)
    source = str(info.get("source") or "").strip()
    if source:
        candidates.append(Path(source))
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    raise ValueError("找不到该视频的原始文件，无法执行自动跟踪")


def _bounded_tracking_box(box: list[float], width: int, height: int) -> list[float]:
    x1, y1, x2, y2 = box
    x1 = min(float(width - 2), max(0.0, x1))
    y1 = min(float(height - 2), max(0.0, y1))
    x2 = min(float(width), max(x1 + 2.0, x2))
    y2 = min(float(height), max(y1 + 2.0, y2))
    return [x1, y1, x2, y2]


def track_video_box(
    video_id: str,
    start_frame: int,
    end_frame: int,
    start_box: list[float],
    output_frames: list[int],
    end_box: list[float] | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Track one normalized box through real video frames using LK flow with template fallback."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("自动跟踪需要安装 opencv-python 和 numpy") from exc

    video_path = annotation_video_path(video_id)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError(f"无法读取跟踪视频: {video_path.name}")
    capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    ok, first_frame = capture.read()
    if not ok or first_frame is None:
        capture.release()
        raise ValueError(f"无法读取起始帧 {start_frame}")

    source_height, source_width = first_frame.shape[:2]
    analysis_scale = min(1.0, 960.0 / max(source_width, source_height))
    analysis_width = max(2, int(round(source_width * analysis_scale)))
    analysis_height = max(2, int(round(source_height * analysis_scale)))

    def analysis_frame(frame):
        if analysis_scale < 0.999:
            frame = cv2.resize(frame, (analysis_width, analysis_height), interpolation=cv2.INTER_AREA)
        return frame

    def gray_frame(frame):
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    def feature_points(gray, box):
        mask = np.zeros(gray.shape, dtype=np.uint8)
        x1, y1, x2, y2 = [int(round(value)) for value in box]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(gray.shape[1], x2), min(gray.shape[0], y2)
        if x2 - x1 < 4 or y2 - y1 < 4:
            return None
        mask[y1:y2, x1:x2] = 255
        return cv2.goodFeaturesToTrack(
            gray, mask=mask, maxCorners=100, qualityLevel=0.008,
            minDistance=3, blockSize=3,
        )

    def template_update(previous, current, box):
        x1, y1, x2, y2 = [int(round(value)) for value in box]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(previous.shape[1], x2), min(previous.shape[0], y2)
        template = previous[y1:y2, x1:x2]
        if template.shape[0] < 4 or template.shape[1] < 4:
            return None, 0.0
        margin_x = max(16, int(template.shape[1] * 0.7))
        margin_y = max(16, int(template.shape[0] * 0.7))
        sx1, sy1 = max(0, x1 - margin_x), max(0, y1 - margin_y)
        sx2 = min(current.shape[1], x2 + margin_x)
        sy2 = min(current.shape[0], y2 + margin_y)
        search = current[sy1:sy2, sx1:sx2]
        if search.shape[0] < template.shape[0] or search.shape[1] < template.shape[1]:
            return None, 0.0
        scores = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
        _, maximum, _, location = cv2.minMaxLoc(scores)
        next_x1, next_y1 = sx1 + location[0], sy1 + location[1]
        return [next_x1, next_y1, next_x1 + template.shape[1], next_y1 + template.shape[0]], float(maximum)

    previous_analysis = analysis_frame(first_frame)
    previous_gray = gray_frame(previous_analysis)
    current_box = _bounded_tracking_box(
        [
            start_box[0] * analysis_width,
            start_box[1] * analysis_height,
            start_box[2] * analysis_width,
            start_box[3] * analysis_height,
        ],
        analysis_width,
        analysis_height,
    )
    requested = set(output_frames)
    results = [{"frame": start_frame, "box": list(start_box), "confidence": 1.0, "method": "keyframe"}]
    method_counts = {"optical_flow": 0, "optical_flow+template": 0, "template": 0, "hold": 0}
    confidence_total = 1.0

    try:
        for frame_index in range(start_frame + 1, end_frame + 1):
            ok, frame = capture.read()
            if not ok or frame is None:
                raise ValueError(f"视频在第 {frame_index} 帧前结束，未能完成自动跟踪")
            current_analysis = analysis_frame(frame)
            current_gray = gray_frame(current_analysis)
            points = feature_points(previous_gray, current_box)
            next_box = None
            confidence = 0.0
            method = "hold"
            if points is not None and len(points) >= 4:
                moved, status, errors = cv2.calcOpticalFlowPyrLK(
                    previous_gray,
                    current_gray,
                    points,
                    None,
                    winSize=(31, 31),
                    maxLevel=3,
                    criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 24, 0.01),
                )
                if moved is not None and status is not None:
                    valid = status.reshape(-1) == 1
                    if errors is not None:
                        valid &= errors.reshape(-1) < 45.0
                    before = points.reshape(-1, 2)[valid]
                    after = moved.reshape(-1, 2)[valid]
                    if len(before) >= 4:
                        transform, inliers = cv2.estimateAffinePartial2D(
                            before,
                            after,
                            method=cv2.RANSAC,
                            ransacReprojThreshold=3.0,
                            maxIters=300,
                            confidence=0.97,
                        )
                        if transform is not None:
                            x1, y1, x2, y2 = current_box
                            old_width, old_height = x2 - x1, y2 - y1
                            old_center = ((x1 + x2) / 2, (y1 + y2) / 2)
                            transformed_center = cv2.transform(np.array([[[old_center[0], old_center[1]]]], dtype=np.float32), transform)[0][0]
                            raw_scale = math.hypot(float(transform[0, 0]), float(transform[0, 1]))
                            # Partial affine rotation must not inflate an axis-aligned box. Apply only a
                            # damped isotropic scale; user end keyframes handle genuine shape changes.
                            frame_scale = 1.0 + (min(1.06, max(0.94, raw_scale)) - 1.0) * 0.35
                            new_width, new_height = old_width * frame_scale, old_height * frame_scale
                            candidate = [
                                float(transformed_center[0] - new_width / 2),
                                float(transformed_center[1] - new_height / 2),
                                float(transformed_center[0] + new_width / 2),
                                float(transformed_center[1] + new_height / 2),
                            ]
                            new_center = ((candidate[0] + candidate[2]) / 2, (candidate[1] + candidate[3]) / 2)
                            movement = math.hypot(new_center[0] - old_center[0], new_center[1] - old_center[1])
                            size_ok = 0.75 <= raw_scale <= 1.3
                            movement_ok = movement <= max(32.0, max(old_width, old_height) * 1.2)
                            if size_ok and movement_ok:
                                next_box = candidate
                                confidence = float(inliers.mean()) if inliers is not None else min(1.0, len(before) / 12.0)
                                method = "optical_flow"
            template_candidate, template_confidence = template_update(previous_gray, current_gray, current_box)
            if next_box is not None and template_candidate is not None and template_confidence >= 0.35:
                optical_width, optical_height = next_box[2] - next_box[0], next_box[3] - next_box[1]
                optical_center = ((next_box[0] + next_box[2]) / 2, (next_box[1] + next_box[3]) / 2)
                template_center = ((template_candidate[0] + template_candidate[2]) / 2, (template_candidate[1] + template_candidate[3]) / 2)
                blend = min(0.45, max(0.15, template_confidence * 0.4))
                center_x = optical_center[0] * (1 - blend) + template_center[0] * blend
                center_y = optical_center[1] * (1 - blend) + template_center[1] * blend
                next_box = [center_x - optical_width / 2, center_y - optical_height / 2, center_x + optical_width / 2, center_y + optical_height / 2]
                confidence = min(1.0, confidence * 0.7 + template_confidence * 0.3)
                method = "optical_flow+template"
            elif next_box is None and template_candidate is not None and template_confidence >= 0.2:
                next_box = template_candidate
                confidence = max(0.15, template_confidence)
                method = "template"
            if next_box is None:
                next_box = current_box
                confidence = 0.0
            current_box = _bounded_tracking_box(next_box, analysis_width, analysis_height)
            method_counts[method] += 1
            confidence_total += confidence
            if frame_index in requested:
                normalized = [
                    current_box[0] / analysis_width,
                    current_box[1] / analysis_height,
                    current_box[2] / analysis_width,
                    current_box[3] / analysis_height,
                ]
                normalized = [round(min(1.0, max(0.0, value)), 6) for value in normalized]
                results.append({"frame": frame_index, "box": normalized, "confidence": round(confidence, 4), "method": method})
            previous_gray = current_gray
    finally:
        capture.release()

    if len(results) != len(output_frames):
        raise ValueError("自动跟踪未生成完整的目标帧")

    if end_box is not None:
        tracked_end = results[-1]["box"]
        tracked_center = ((tracked_end[0] + tracked_end[2]) / 2, (tracked_end[1] + tracked_end[3]) / 2)
        target_center = ((end_box[0] + end_box[2]) / 2, (end_box[1] + end_box[3]) / 2)
        tracked_size = (tracked_end[2] - tracked_end[0], tracked_end[3] - tracked_end[1])
        target_size = (end_box[2] - end_box[0], end_box[3] - end_box[1])
        for item in results:
            ratio = (int(item["frame"]) - start_frame) / max(1, end_frame - start_frame)
            box = item["box"]
            center_x = (box[0] + box[2]) / 2 + (target_center[0] - tracked_center[0]) * ratio
            center_y = (box[1] + box[3]) / 2 + (target_center[1] - tracked_center[1]) * ratio
            width = (box[2] - box[0]) + (target_size[0] - tracked_size[0]) * ratio
            height = (box[3] - box[1]) + (target_size[1] - tracked_size[1]) * ratio
            item["box"] = [
                round(max(0.0, center_x - width / 2), 6),
                round(max(0.0, center_y - height / 2), 6),
                round(min(1.0, center_x + width / 2), 6),
                round(min(1.0, center_y + height / 2), 6),
            ]
        results[-1]["box"] = list(end_box)
        results[-1]["method"] = "human_end_keyframe"
        results[-1]["confidence"] = 1.0

    processed = max(1, end_frame - start_frame + 1)
    quality = {
        "video_file": video_path.name,
        "processed_frames": processed,
        "output_frames": len(results),
        "analysis_resolution": f"{analysis_width}x{analysis_height}",
        "mean_confidence": round(confidence_total / processed, 4),
        "method_counts": method_counts,
        "end_keyframe_corrected": end_box is not None,
    }
    return results, quality


def annotation_reviews() -> dict[str, dict]:
    legacy = {
        str(item.get("annotation_id")): item
        for item in read_jsonl(RUNTIME_ROOT / "annotation_reviews.jsonl")
        if item.get("annotation_id")
    }
    legacy.update(db_annotation_reviews())
    return legacy


def frame_annotation_items(video_id: str, requested_time: float) -> list[dict]:
    info = video_info(video_id)
    if info is None:
        raise ValueError("视频不存在")
    frame = min(max(0, int(round(requested_time * float(info.get("fps", 30))))), int(info.get("frames", 1)) - 1)
    width, height = video_size(video_id)
    reviews = annotation_reviews()
    deleted_ids = db_deleted_annotation_ids()
    items: list[dict] = []
    sources = (("prelabel", frame_records(video_id), "detections"), ("candidate", frame_records(video_id, "candidates"), "candidates"))
    for source_kind, records, field in sources:
        if not records:
            continue
        record = record_for_frame(records, frame)
        for index, detection in enumerate(record.get(field, [])):
            annotation_id = f"{video_id}:{source_kind}:{record.get('frame', frame)}:{index}"
            if annotation_id in deleted_ids:
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
                "region": detection.get("region") or record.get("region") or "未分区",
                "track_id": detection.get("track_id"),
                "review_status": review.get("review_status") or detection.get("review_status") or "pending",
                "reviewer": review.get("reviewer"),
                "reviewed_at": review.get("recorded_at"),
            })
    return items


def manual_annotation_items(video_id: str | None = None, frame: int | None = None) -> list[dict]:
    reviews = annotation_reviews()
    deleted_ids = db_deleted_annotation_ids()
    records: dict[str, dict] = {}
    # Current-frame review is latency-sensitive and SQLite is authoritative after migration.
    source_records = db_manual_annotations(video_id, frame)
    if frame is None:
        source_records = read_jsonl(RUNTIME_ROOT / "annotations.jsonl") + source_records
    for record in source_records:
        record_video_id = str(record.get("video_id") or video_id_for_source(record.get("video")))
        if video_id and record_video_id != video_id:
            continue
        if frame is not None and int(record.get("frame", -1)) != frame:
            continue
        annotation_id = str(record.get("annotation_id") or f"manual:{record_video_id}:{record.get('_line')}")
        if annotation_id in deleted_ids:
            continue
        records[annotation_id] = record
    items = []
    for annotation_id, record in records.items():
        record_video_id = str(record.get("video_id") or video_id_for_source(record.get("video")))
        normalized, pixels = normalize_box(record.get("box"), record_video_id)
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
            "region": record.get("region", "未分区"),
            "track_id": record.get("track_id"),
            "review_status": review.get("review_status") or record.get("review_status", "human_confirmed"),
            "reviewer": review.get("reviewer") or record.get("reviewer"),
            "reviewed_at": review.get("recorded_at"),
        })
    return items


def migrate_legacy_annotations_to_db() -> None:
    """Import append-only JSONL once so the SQLite backup is self-contained."""
    with ANNOTATION_DB_LOCK, annotation_db() as connection:
        annotation_count = int(connection.execute("SELECT COUNT(*) FROM annotations").fetchone()[0])
        review_count = int(connection.execute("SELECT COUNT(*) FROM annotation_reviews").fetchone()[0])
    if annotation_count == 0:
        for record in read_jsonl(RUNTIME_ROOT / "annotations.jsonl"):
            video_id = str(record.get("video_id") or video_id_for_source(record.get("video")))
            try:
                normalized, pixels = normalize_box(record.get("box"), video_id)
            except ValueError:
                continue
            video_time = float(record.get("video_time", 0))
            fps = float((video_info(video_id) or {}).get("fps", 30))
            annotation = {
                **{key: value for key, value in record.items() if key != "_line"},
                "annotation_id": str(record.get("annotation_id") or f"manual:{video_id}:legacy:{record.get('_line')}"),
                "video_id": video_id, "video_time": video_time,
                "frame": int(record.get("frame", round(video_time * fps))),
                "label": str(record.get("label") or "未分类目标"),
                "region": str(record.get("region") or "未分区"),
                "box": normalized, "box_pixels": pixels, "box_format": "normalized_xyxy",
                "source_kind": "manual", "source": record.get("source", "历史平台人工标注"),
                "review_status": record.get("review_status", "pending"),
            }
            db_upsert_annotation(annotation)
    if review_count == 0:
        for review in read_jsonl(RUNTIME_ROOT / "annotation_reviews.jsonl"):
            if review.get("annotation_id") and review.get("review_status"):
                db_save_review({key: value for key, value in review.items() if key != "_line"})


migrate_legacy_annotations_to_db()


def annotation_stats() -> dict:
    prelabels = 0
    candidates = 0
    for video in video_catalog().get("videos", []):
        video_id = str(video["id"])
        prelabels += sum(len(record.get("detections", [])) for record in frame_records(video_id))
        candidates += sum(len(record.get("candidates", [])) for record in frame_records(video_id, "candidates"))
    stored = manual_annotation_items()
    prelabels += sum(1 for item in stored if item.get("source_kind") == "prelabel")
    candidates += sum(1 for item in stored if item.get("source_kind") == "candidate")
    manual = [item for item in stored if item.get("source_kind") == "manual"]
    reviews = list(annotation_reviews().values())
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
        "database": annotation_db_health(),
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


ANNOTATION_CSV_FIELDS = [
    "annotation_id", "video_id", "video_file", "frame", "time_s", "label", "source_kind", "source",
    "region", "track_id", "confidence", "review_status", "reviewer", "reviewed_at", "x_min", "y_min", "x_max", "y_max",
    "pixel_x_min", "pixel_y_min", "pixel_x_max", "pixel_y_max", "evidence_file",
]


def annotation_csv_bytes(items: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=ANNOTATION_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for item in items:
        info = video_info(str(item.get("video_id"))) or {}
        box = list(item.get("box") or [None] * 4)
        pixels = list(item.get("box_pixels") or [None] * 4)
        writer.writerow({
            "annotation_id": item.get("annotation_id"),
            "video_id": item.get("video_id"),
            "video_file": Path(str(info.get("source_video") or item.get("video") or "")).name,
            "frame": item.get("frame"),
            "time_s": item.get("video_time"),
            "label": item.get("label"),
            "source_kind": item.get("source_kind"),
            "source": item.get("source"),
            "region": item.get("region", "未分区"),
            "track_id": item.get("track_id"),
            "confidence": item.get("confidence"),
            "review_status": item.get("review_status"),
            "reviewer": item.get("reviewer"),
            "reviewed_at": item.get("reviewed_at"),
            "x_min": box[0], "y_min": box[1], "x_max": box[2], "y_max": box[3],
            "pixel_x_min": pixels[0], "pixel_y_min": pixels[1], "pixel_x_max": pixels[2], "pixel_y_max": pixels[3],
            "evidence_file": Path(str(item.get("evidence_path") or "")).name,
        })
    return output.getvalue().encode("utf-8-sig")


def dataset_catalog_csv_bytes(dataset_id: str | None = None) -> bytes:
    fields = [
        "dataset_id", "dataset_name", "production_line", "task", "local_state", "local_message",
        "image_count", "label_count", "local_paths", "source_name", "source_url", "license", "usage_policy",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for item in dataset_catalog_with_status():
        if dataset_id and str(item.get("id")) != dataset_id:
            continue
        sources = item.get("sources") or [{}]
        for source in sources:
            writer.writerow({
                "dataset_id": item.get("id"),
                "dataset_name": item.get("name"),
                "production_line": item.get("line"),
                "task": item.get("task"),
                "local_state": item.get("local_state"),
                "local_message": item.get("local_message"),
                "image_count": item.get("image_count", 0),
                "label_count": item.get("label_count", 0),
                "local_paths": " | ".join(str(value) for value in item.get("local_paths", [])),
                "source_name": source.get("name"),
                "source_url": source.get("url"),
                "license": source.get("license"),
                "usage_policy": item.get("download") or item.get("embedding_policy"),
            })
    return output.getvalue().encode("utf-8-sig")


def build_annotation_package(status: str = "human_confirmed") -> tuple[Path, int]:
    items = exported_annotation_items(status)
    export_root = RUNTIME_ROOT / "exports"
    export_root.mkdir(parents=True, exist_ok=True)
    destination = export_root / f"SOP完整标注数据包_{status}.zip"
    temporary = export_root / f".{destination.name}.tmp"
    manifest = {
        "dataset_version": time.strftime("sop-annotations-%Y%m%d-%H%M%S"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "review_status": status,
        "box_format": "normalized_xyxy",
        "total": len(items),
        "items": items,
        "truth_boundary": "仅导出当前审核状态快照；正式训练前由质量部门锁定版本和校验值。",
    }
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.writestr("标注坐标/已审核标注.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("标注坐标/已审核标注.csv", annotation_csv_bytes(items))
        archive.writestr("数据集清单/数据集与来源.csv", dataset_catalog_csv_bytes())
        archive.writestr(
            "README_导出说明.txt",
            "SOP完整标注数据包\n\n"
            "1. 标注坐标：JSON 和 Excel 可打开的 CSV。\n"
            "2. 标注截图：网页人工框选时保存的证据图。\n"
            "3. 网页视频：标注对应的全部浏览器代理视频；4K原始视频在离线交付目录中单独保存。\n"
            "4. 本地数据集：图片、标签、manifest、data.yaml 和统计文件。\n"
            "5. 此包仅代表导出时审核状态，量产训练前须锁定冻结测试集。\n",
        )
        for path in sorted(ANNOTATION_IMAGE_ROOT.glob("*")):
            if path.is_file():
                archive.write(path, f"标注截图/{path.name}")
        for video in video_catalog().get("videos", []):
            source = (WEB_ROOT / str(video.get("source_video", ""))).resolve()
            if source.is_file() and WEB_ROOT.resolve() in source.parents:
                archive.write(source, f"原始视频/{source.name}")
        dataset_root = ROOT / "datasets"
        for path in sorted(dataset_root.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix not in {".npy", ".cache"}:
                archive.write(path, f"本地数据集/{path.relative_to(dataset_root)}")
        for name in ("annotations.jsonl", "annotation_reviews.jsonl"):
            path = RUNTIME_ROOT / name
            if path.is_file():
                archive.write(path, f"审核记录/{name}")
    temporary.replace(destination)
    return destination, len(items)


def _update_render_job(job_id: str, **values: object) -> None:
    with ANNOTATION_RENDER_LOCK:
        ANNOTATION_RENDER_JOBS.setdefault(job_id, {}).update(values)


def render_annotation_video_job(job_id: str, video_id: str) -> None:
    capture = None
    writer = None
    try:
        import cv2

        source = annotation_video_path(video_id)
        annotations = manual_annotation_items(video_id)
        by_frame: dict[int, list[dict]] = {}
        for annotation in annotations:
            by_frame.setdefault(int(annotation.get("frame", 0)), []).append(annotation)
        ANNOTATION_EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
        destination = ANNOTATION_EXPORT_ROOT / f"{video_id}_标注成片_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise RuntimeError("无法打开源视频")
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 30)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720)
        total = max(1, int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 1))
        writer = cv2.VideoWriter(str(destination), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
        if not writer.isOpened():
            raise RuntimeError("无法创建标注成片")
        frame_index = 0
        _update_render_job(job_id, status="running", message="正在逐帧绘制已保存标注")
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            for item in by_frame.get(frame_index, []):
                x1, y1, x2, y2 = [float(value) for value in item.get("box", [0, 0, 0, 0])]
                left, top, right, bottom = int(x1 * width), int(y1 * height), int(x2 * width), int(y2 * height)
                color = (188, 226, 57) if item.get("review_status") == "human_confirmed" else (102, 189, 255)
                cv2.rectangle(frame, (left, top), (right, bottom), color, max(2, round(width / 640)))
                caption = str(item.get("track_id") or item.get("annotation_id") or "object")[-32:]
                cv2.putText(frame, caption, (left, max(20, top - 7)), cv2.FONT_HERSHEY_SIMPLEX, max(0.45, width / 2400), color, 2, cv2.LINE_AA)
            writer.write(frame)
            frame_index += 1
            if frame_index % max(1, int(fps * 2)) == 0:
                _update_render_job(job_id, progress=round(frame_index * 100 / total, 1))
        capture.release()
        capture = None
        writer.release()
        writer = None
        manifest = {
            "video_id": video_id, "source": str(source), "output": str(destination),
            "frames": frame_index, "fps": fps, "annotation_count": len(annotations),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "annotations": annotations,
        }
        atomic_write_json(destination.with_suffix(".json"), manifest)
        _update_render_job(job_id, status="completed", progress=100, output=str(destination), message="标注成片和同名审计 JSON 已生成")
    except Exception as exc:
        _update_render_job(job_id, status="failed", message=str(exc))
    finally:
        if capture is not None:
            capture.release()
        if writer is not None:
            writer.release()


def start_annotation_render(video_id: str, operator: str) -> dict[str, object]:
    if video_info(video_id) is None:
        raise ValueError("视频不存在")
    job_id = f"RENDER-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}"
    job = {"job_id": job_id, "video_id": video_id, "operator": operator, "status": "queued", "progress": 0, "message": "等待生成", "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with ANNOTATION_RENDER_LOCK:
        ANNOTATION_RENDER_JOBS[job_id] = job
    threading.Thread(target=render_annotation_video_job, args=(job_id, video_id), daemon=True, name=job_id).start()
    return job


class SOPHandler(SimpleHTTPRequestHandler):
    server_version = "NingboSOP/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, format: str, *args) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {format % args}")

    def copyfile(self, source, outputfile) -> None:
        try:
            shutil.copyfileobj(source, outputfile, length=1024 * 1024)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

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
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def send_download(self, body: bytes, filename: str, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file_download(self, path: Path, filename: str | None = None) -> None:
        if not path.is_file():
            self.send_json({"ok": False, "message": "下载文件不存在"}, HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename or path.name)}")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile, length=1024 * 1024)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 12_000_000:
            raise ValueError("请求内容过大")
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def append_event(self, filename: str, payload: dict) -> None:
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        payload = {"recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"), **payload}
        serialized = json.dumps(payload, ensure_ascii=False)
        with ANNOTATION_DB_LOCK:
            with (RUNTIME_ROOT / filename).open("a", encoding="utf-8") as handle:
                handle.write(serialized + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            with annotation_db() as connection:
                connection.execute(
                    "INSERT INTO event_log(event_type, payload_json, recorded_at) VALUES (?, ?, ?)",
                    (filename, serialized, payload["recorded_at"]),
                )

    def auth_session_id(self) -> str:
        cookie = self.headers.get("Cookie", "")
        for item in cookie.split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name == "sop_session":
                return value
        return ""

    def current_user(self) -> dict[str, object] | None:
        return auth_user_for_session(self.auth_session_id())

    def require_api_access(self, path: str, method: str) -> dict[str, object] | None:
        user = self.current_user()
        if user is None:
            self.send_json({"ok": False, "message": "登录已失效，请重新登录"}, HTTPStatus.UNAUTHORIZED)
            return None
        role = str(user.get("role"))
        admin_only = (
            "/api/models/pcb/select", "/api/spark/sync-models", "/api/production-lines/select",
        )
        manager_prefixes = (
            "/api/train", "/api/deploy", "/api/mes", "/api/decision", "/api/sop/save",
            "/api/models", "/api/model-benchmark", "/api/training", "/api/spark", "/api/software", "/api/cloud",
        )
        if method == "POST" and path == "/api/annotations/review":
            manager_prefixes = (*manager_prefixes, "/api/annotations/review")
        if method == "POST" and path == "/api/annotations/scope":
            manager_prefixes = (*manager_prefixes, "/api/annotations/scope")
        if method == "POST" and path == "/api/annotations/prelabel":
            manager_prefixes = (*manager_prefixes, "/api/annotations/prelabel")
        if method == "POST" and path == "/api/cvat/annotations":
            manager_prefixes = (*manager_prefixes, "/api/cvat/annotations")
        if path.startswith(admin_only) and role != "admin":
            self.send_json({"ok": False, "message": "该操作仅开发者可执行"}, HTTPStatus.FORBIDDEN)
            return None
        if path.startswith(manager_prefixes) and role not in {"admin", "manager"}:
            self.send_json({"ok": False, "message": "该功能需要管理者或开发者权限"}, HTTPStatus.FORBIDDEN)
            return None
        return user

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/auth/status":
            user = self.current_user()
            self.send_json({"ok": True, "authenticated": bool(user), "user": user})
            return
        if path.startswith("/media/") and self.current_user() is None:
            self.send_json({"ok": False, "message": "请登录后查看生产视频"}, HTTPStatus.UNAUTHORIZED)
            return
        if path.startswith("/api/") and path != "/api/health" and self.require_api_access(path, "GET") is None:
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
            if source in {"all", "manual", "prelabel", "candidate"}:
                fps = float((video_info(video_id) or {}).get("fps", 30))
                tolerance = max(0.05, 1.1 / fps)
                requested_frame = int(round(requested_time * fps))
                items.extend(item for item in manual_annotation_items(video_id, requested_frame) if abs(float(item.get("video_time", 0)) - requested_time) <= tolerance)
            if source in {"manual", "prelabel", "candidate"}:
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
        if path == "/api/annotations/history":
            query = parse_qs(parsed.query)
            video_id = str(query.get("video", ["video_0265"])[0])
            if video_info(video_id) is None:
                self.send_json({"ok": False, "message": "视频不存在"}, HTTPStatus.NOT_FOUND)
                return
            self.send_json({"ok": True, **db_annotation_history(video_id)})
            return
        if path == "/api/annotations/checkpoint":
            query = parse_qs(parsed.query)
            video_id = str(query.get("video", [""])[0])
            checkpoint_id = str(query.get("checkpoint", [""])[0])
            if video_info(video_id) is None:
                self.send_json({"ok": False, "message": "视频不存在"}, HTTPStatus.NOT_FOUND)
                return
            snapshot = annotation_checkpoint_snapshot(video_id, checkpoint_id)
            if snapshot is None:
                self.send_json({"ok": False, "message": "保存点不存在或快照已过期"}, HTTPStatus.NOT_FOUND)
                return
            self.send_json({"ok": True, "snapshot": snapshot})
            return
        if path == "/api/annotations/tracks":
            query = parse_qs(parsed.query)
            video_id = str(query.get("video", ["video_0265"])[0])
            if video_info(video_id) is None:
                self.send_json({"ok": False, "message": "视频不存在"}, HTTPStatus.NOT_FOUND)
                return
            tracks = db_annotation_tracks(video_id)
            self.send_json({"ok": True, "video_id": video_id, "items": tracks, "total": len(tracks)})
            return
        if path == "/api/annotations/autosave":
            video_id = str(parse_qs(parsed.query).get("video", ["video_0265"])[0])
            self.send_json({"ok": True, "video_id": video_id, "snapshot": latest_annotation_draft(video_id)})
            return
        if path == "/api/annotations/render/status":
            job_id = str(parse_qs(parsed.query).get("job", [""])[0])
            with ANNOTATION_RENDER_LOCK:
                job = ANNOTATION_RENDER_JOBS.get(job_id)
            if job is None:
                self.send_json({"ok": False, "message": "标注成片任务不存在"}, HTTPStatus.NOT_FOUND)
            else:
                self.send_json({"ok": True, "job": job})
            return
        if path == "/api/annotations/scope":
            self.send_json({"ok": True, "scope": annotation_scope()})
            return
        if path == "/api/annotations/stats":
            self.send_json(annotation_stats())
            return
        if path == "/api/annotations/prelabel/status":
            self.send_json(ai_prelabel_status())
            return
        if path == "/api/annotations/database":
            self.send_json(annotation_db_health())
            return
        if path == "/api/annotations/export":
            query = parse_qs(parsed.query)
            status = query.get("status", ["human_confirmed"])[0]
            items = exported_annotation_items(status)
            export_format = query.get("format", ["json"])[0].lower()
            if export_format == "csv":
                self.send_download(annotation_csv_bytes(items), f"SOP标注坐标_{status}.csv", "text/csv; charset=utf-8")
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
        if path == "/api/annotations/package":
            status = parse_qs(parsed.query).get("status", ["human_confirmed"])[0]
            package_path, _ = build_annotation_package(status)
            self.send_file_download(package_path)
            return
        if path == "/api/cloud/status":
            self.send_json(cloud_sync_status())
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
            detected_labels = sorted({str(item.get("label") or "未分类目标") for item in dynamic + fasteners})
            tool_labels = {"电动紧固工具", "电烙铁", "镊子", "刷子"}
            has_tool = any(item.get("label") in tool_labels for item in dynamic)
            has_hand = any(item.get("label") == "操作人员手部" for item in dynamic)
            has_pcb = any("PCB" in str(item.get("label") or "") for item in dynamic)
            expected = ["操作人员手部"]
            if ACTIVE_LINE_ID == "pcb":
                expected.append("PCB/夹具")
            if any(keyword in step["label"] for keyword in ("焊", "紧固", "工具")):
                expected.append("工具")
            missing = []
            if not has_hand:
                missing.append("操作人员手部")
            if "PCB/夹具" in expected and not has_pcb:
                missing.append("PCB或夹具")
            if "工具" in expected and not has_tool:
                missing.append("当前工序工具")
            confidence_values = [float(item.get("confidence") or 0) for item in dynamic + fasteners]
            evidence_score = round(100 * max(confidence_values), 1) if confidence_values else 0.0
            evidence_completeness = max(0, round(100 * (len(expected) - len(missing)) / max(len(expected), 1)))
            risk_score = min(95, 38 + 16 * len(missing) + (12 if not dynamic else 0))
            reasons = [
                f"当前应执行 {step['id']}：{step['label']}",
                f"当前画面载入{len(record.get('parts', []))}个工位区域、{len(dynamic)}个目标框、{len(fasteners)}个小目标候选",
                f"待补证据：{'、'.join(missing) if missing else '视觉必需项已看到'}",
                "自动框仅用于提示和复核；质量放行仍需工艺确认、扭矩/焊接结果与MES回执",
            ]
            action = "质量员复核当前候选框；工艺证据与MES齐全前保持HOLD"
            if missing:
                action = f"现场人员先确认{'、'.join(missing)}，必要时调整遮挡或相机角度"
            visible_dynamic = sorted(dynamic, key=lambda item: float(item.get("confidence") or 1 if item.get("confidence") is None else item.get("confidence")), reverse=True)[:18]
            visible_candidates = sorted(fasteners, key=lambda item: float(item.get("confidence") or 0), reverse=True)[:10]
            self.send_json({
                "ok": True, "video_id": video_id, "time_s": round(requested_time, 2), "frame": index,
                "step": step, "completed_steps": completed, "visual_state": "PASS" if completed >= len(video["steps"]) else "RUNNING",
                "release": "HOLD", "risk_score": min(risk_score, 100), "risk_level": "中高" if risk_score >= 65 else "中",
                "evidence_score": evidence_score, "evidence_completeness": evidence_completeness,
                "objects": {"business_regions": len(record.get("parts", [])), "dynamic": len(dynamic), "fastener_candidates": len(fasteners), "hand_seen": has_hand, "tool_seen": has_tool, "pcb_seen": has_pcb},
                "detections": visible_dynamic, "candidates": visible_candidates,
                "frame_size": {"width": video_size(video_id)[0], "height": video_size(video_id)[1]},
                "detected_labels": detected_labels, "expected_labels": expected, "missing_evidence": missing,
                "reasons": reasons, "recommended_action": action,
                "decision_chain": ["相机/视频", "候选框", "工位ROI", "当前工序", "证据缺口", "人工处置", "MES留痕"],
                "truth_notice": "所有自动框、ROI、小目标和动作均为候选；人工确认前不计入合格数量",
            })
            return
        if path == "/api/health":
            self.send_json({"status": "ok", "service": "宁波SOP分析平台", "time": time.time()})
            return
        if path == "/api/device/inventory":
            refresh_camera_services()
            self.send_json(device_inventory())
            return
        if path == "/api/camera/recommendations":
            self.send_json({"ok": True, "items": read_config(ROOT / "config" / "camera_recommendations.json", [])})
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
            self.send_json({"ok": True, **config, "available": probe_url(str(config["url"])), "tasks_url": f"{config['url']}/tasks", "bulk": cvat_bulk_status()})
            return
        if path == "/api/cvat/tasks":
            bulk = cvat_bulk_status()
            self.send_json({"ok": True, "items": bulk["items"] + db_cvat_tasks(), "bulk": bulk})
            return
        if path == "/api/train/jobs":
            items = [read_config(job_dir / "status.json", {}) for job_dir in sorted(training_job_dirs(), key=lambda item: item.stat().st_mtime, reverse=True)[:30]]
            self.send_json({"ok": True, "items": items})
            return
        if path.startswith("/api/train/jobs/"):
            remainder = path.removeprefix("/api/train/jobs/")
            job_id, separator, action = remainder.partition("/")
            job = training_job(unquote(job_id))
            if job is None:
                self.send_json({"ok": False, "message": "训练任务不存在"}, HTTPStatus.NOT_FOUND)
                return
            if separator and action == "download":
                package = Path(str(job.get("output_dir"))) / f"{job['job_id']}_deployment.zip"
                self.send_file_download(package)
            else:
                self.send_json(job)
            return
        if path == "/api/training/catalog":
            self.send_json({
                "ok": True, "algorithms": read_config(TRAINING_CATALOG_PATH, []),
                "datasets": dataset_catalog_with_status(), "training_datasets": training_dataset_options(),
                "production_lines": production_lines(),
                "outputs": [{"id": key, "path": str(value)} for key, value in TRAINING_OUTPUT_ROOTS.items()],
            })
            return
        if path == "/api/datasets/export.csv":
            dataset_id = parse_qs(parsed.query).get("dataset", [""])[0] or None
            filename = f"数据集清单_{dataset_id or '全部'}.csv"
            self.send_download(dataset_catalog_csv_bytes(dataset_id), filename, "text/csv; charset=utf-8")
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
        if path == "/api/models/pcb":
            self.send_json({"ok": True, **pcb_model_registry()})
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
                username = str(payload.get("username") or "").strip()
                password = str(payload.get("password") or "")
                user = authenticate_user(username, password)
                if user is None:
                    time.sleep(0.35)
                    self.send_json({"ok": False, "message": "账号或密码错误"}, HTTPStatus.UNAUTHORIZED)
                    return
                session_id = create_auth_session(int(user["user_id"]))
                full_user = auth_user_for_session(session_id)
                self.send_json(
                    {"ok": True, "message": "登录成功", "user": full_user},
                    headers={"Set-Cookie": f"sop_session={session_id}; Path=/; HttpOnly; SameSite=Strict; Max-Age={AUTH_SESSION_SECONDS}{'; Secure' if AUTH_SECURE_COOKIE else ''}"},
                )
                return
            if path == "/api/auth/logout":
                session_id = self.auth_session_id()
                if session_id:
                    with ANNOTATION_DB_LOCK, annotation_db() as connection:
                        connection.execute("DELETE FROM auth_sessions WHERE session_id = ?", (session_id,))
                self.send_json(
                    {"ok": True, "message": "已安全退出"},
                    headers={"Set-Cookie": f"sop_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0{'; Secure' if AUTH_SECURE_COOKIE else ''}"},
                )
                return
            if path.startswith("/api/") and self.require_api_access(path, "POST") is None:
                return
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
                    "region": str(payload.get("region") or "未分区"),
                    "track_id": str(payload.get("track_id") or "").strip() or None,
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
                db_upsert_annotation(annotation)
                supersedes_id = str(payload.get("supersedes_annotation_id") or "").strip()
                supersedes_kind = str(payload.get("supersedes_source_kind") or "").strip()
                if supersedes_id and supersedes_kind in {"prelabel", "candidate"}:
                    db_delete_generated_annotation(
                        {
                            "annotation_id": supersedes_id,
                            "video_id": video_id,
                            "source_kind": supersedes_kind,
                            "frame": annotation["frame"],
                        },
                        str((self.current_user() or {}).get("username") or "unknown"),
                        f"由人工标注 {annotation['annotation_id']} 修正替代",
                    )
                self.append_event("annotations.jsonl", annotation)
                self.send_json({"ok": True, "annotation": annotation, "database": annotation_db_health(), "message": f"当前帧标注已保存到区域“{annotation['region']}”，可进入质量抽检"})
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
                db_save_review(review)
                self.append_event("annotation_reviews.jsonl", review)
                self.send_json({"ok": True, "review": review, "message": "审核结果已留痕"})
                return
            if path == "/api/annotations/delete":
                annotation_id = str(payload.get("annotation_id") or "").strip()
                user = self.current_user() or {}
                deleted_by = str(user.get("username") or "unknown")
                reason = str(payload.get("reason") or "人工删除")
                result = db_delete_annotations([annotation_id], deleted_by, reason)
                if not result["deleted_count"]:
                    parts = annotation_id.rsplit(":", 3)
                    generated = None
                    if len(parts) == 4 and parts[1] in {"prelabel", "candidate"}:
                        video_id, _, frame_text, _ = parts
                        info = video_info(video_id)
                        try:
                            requested_time = int(frame_text) / float((info or {}).get("fps", 30))
                        except (TypeError, ValueError, ZeroDivisionError):
                            requested_time = -1
                        if info is not None and requested_time >= 0:
                            generated = next(
                                (item for item in frame_annotation_items(video_id, requested_time) if item.get("annotation_id") == annotation_id),
                                None,
                            )
                    if generated is None:
                        self.send_json({"ok": False, "message": "标注不存在、已被删除或当前用户无权删除"}, 404)
                        return
                    result = db_delete_generated_annotation(generated, deleted_by, reason)
                self.append_event("annotation_deletions.jsonl", result)
                deleted_kind = str(result["deleted"][0].get("source_kind") or "manual")
                kind_name = {"prelabel": "AI预标注", "candidate": "小目标候选", "manual": "人工标注"}.get(deleted_kind, "标注")
                self.send_json({"ok": True, **result, "message": f"已删除该{kind_name}框，删除记录已留存"})
                return
            if path == "/api/annotations/tracks/delete":
                video_id = str(payload.get("video_id") or "").strip()
                track_id = str(payload.get("track_id") or "").strip()
                if not video_id or not track_id:
                    raise ValueError("请指定要删除的视频和轨迹")
                user = self.current_user() or {}
                start_value, end_value = payload.get("start_frame"), payload.get("end_frame")
                if start_value is None and end_value is None:
                    result = db_delete_track(video_id, track_id, str(user.get("username") or "unknown"))
                    message = f"已删除轨迹 {track_id} 及其 {result['deleted_count']} 个标注框"
                else:
                    if start_value is None or end_value is None:
                        raise ValueError("删除轨迹片段必须同时指定起始帧和结束帧")
                    start_frame, end_frame = int(start_value), int(end_value)
                    result = db_delete_track_segment(video_id, track_id, start_frame, end_frame, str(user.get("username") or "unknown"))
                    message = f"已删除轨迹 {track_id} 的第 {start_frame}-{end_frame} 帧，共 {result['deleted_count']} 个框"
                self.append_event("annotation_deletions.jsonl", {**result, "track_id": track_id, "start_frame": start_value, "end_frame": end_value})
                self.send_json({"ok": True, **result, "track_id": track_id, "message": message})
                return
            if path == "/api/annotations/scope":
                scope = save_annotation_scope(payload)
                self.append_event("annotation_scope_changes.jsonl", scope)
                self.send_json({"ok": True, "scope": scope, "message": "工位标注范围已保存，标注员会看到同一套口径"})
                return
            if path == "/api/annotations/checkpoint":
                checkpoint = db_checkpoint(payload)
                self.append_event("annotation_checkpoints.jsonl", checkpoint)
                self.send_json({"ok": True, "checkpoint": checkpoint, "database": annotation_db_health(), "message": f"标注进度已保存并备份：{checkpoint['annotation_count']} 条、{len(checkpoint['regions'])} 个区域"})
                return
            if path == "/api/annotations/prelabel":
                user = self.current_user() or {}
                job = control_ai_prelabel(str(payload.get("action") or "start"), str(user.get("username") or "unknown"))
                self.append_event("ai_prelabel_jobs.jsonl", {"action": payload.get("action") or "start", "operator": user.get("username"), "status": job.get("status")})
                self.send_json({"ok": True, "job": job, "message": str(job.get("message") or "AI预标注任务状态已更新")})
                return
            if path == "/api/annotations/render":
                video_id = str(payload.get("video_id") or "").strip()
                user = self.current_user() or {}
                job = start_annotation_render(video_id, str(user.get("username") or "unknown"))
                self.send_json({"ok": True, "job": job, "message": "标注成片已进入后台生成队列"})
                return
            if path == "/api/cloud/sync":
                action = str(payload.get("action") or "").strip()
                if action not in {"pull_google", "pull_baidu", "push_google", "push_baidu"}:
                    raise ValueError("云盘同步操作无效")
                user = self.current_user() or {}
                job = start_cloud_sync(action, str(user.get("username") or "unknown"))
                self.append_event("cloud_sync_jobs.jsonl", job)
                self.send_json({"ok": True, "job": job, "message": "云盘同步任务已启动"})
                return
            if path == "/api/annotations/interpolate":
                required = {"video_id", "start_frame", "end_frame", "start_box", "label"}
                if not required.issubset(payload):
                    raise ValueError("自动跟踪参数不完整")
                video_id = str(payload["video_id"])
                info = video_info(video_id)
                if info is None:
                    raise ValueError("视频不存在")
                start_frame, end_frame = int(payload["start_frame"]), int(payload["end_frame"])
                if end_frame <= start_frame:
                    raise ValueError("当前关键帧必须晚于起始关键帧")
                if end_frame - start_frame > 1800:
                    raise ValueError("单次自动跟踪最多跨 1800 帧，请分段设置关键帧")
                start_box, _ = normalize_box(payload["start_box"], video_id)
                end_box = None
                if payload.get("end_box") is not None:
                    end_box, _ = normalize_box(payload["end_box"], video_id)
                step = max(1, int(payload.get("frame_step", 1)))
                fps = float(info.get("fps", 30))
                track_id = str(payload.get("track_id") or f"track:{video_id}:{time.time_ns()}")
                frames = list(range(start_frame, end_frame + 1, step))
                if frames[-1] != end_frame:
                    frames.append(end_frame)
                tracked, quality = track_video_box(video_id, start_frame, end_frame, start_box, frames, end_box)
                generated = []
                start_annotation_id = str(payload.get("start_annotation_id") or "").strip()
                for tracked_item in tracked:
                    frame = int(tracked_item["frame"])
                    box = tracked_item["box"]
                    _, pixels = normalize_box(box, video_id)
                    annotation = {
                        "annotation_id": start_annotation_id if frame == start_frame and start_annotation_id else f"tracked:{video_id}:{track_id}:{frame}",
                        "video_id": video_id, "video_time": round(frame / fps, 3), "frame": frame,
                        "label": str(payload["label"]), "region": str(payload.get("region") or "未分区"),
                        "track_id": track_id, "box": box, "box_pixels": pixels,
                        "box_format": "normalized_xyxy", "source_kind": "manual",
                        "source": "视频画面自动跟踪候选", "review_status": "pending",
                        "tracking_method": tracked_item["method"],
                        "tracking_confidence": tracked_item["confidence"],
                        "reviewer": payload.get("reviewer", "本地标注员"),
                    }
                    db_upsert_annotation(annotation)
                    generated.append(annotation)
                event = {"video_id": video_id, "track_id": track_id, "start_frame": start_frame, "end_frame": end_frame, "generated": len(generated), "region": payload.get("region"), "quality": quality}
                self.append_event("annotation_tracking.jsonl", event)
                correction = "，并按人工结束框校正" if end_box is not None else ""
                self.send_json({"ok": True, "generated": len(generated), "generated_frames": frames, "items": generated, "start_frame": start_frame, "end_frame": end_frame, "track_id": track_id, "quality": quality, "message": f"轨迹 {track_id} 已自动跟踪生成 {len(generated)} 个框{correction}，仍需逐段人工复核"})
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
                user = self.current_user() or {}
                job = start_training_job(payload, str(user.get("username") or "unknown"))
                self.append_event("training_jobs.jsonl", {"event": "started", **job})
                self.send_json({**job, "message": "真实训练进程已启动，页面可持续查看日志和预计耗时"})
                return
            if path == "/api/train/one-click":
                user = self.current_user() or {}
                job = start_training_job(payload, str(user.get("username") or "unknown"))
                self.append_event("training_jobs.jsonl", {"event": "started", **job})
                self.send_json({**job, "message": "真实训练进程已启动"})
                return
            if path == "/api/cvat/task":
                result = cvat_task_create(payload)
                task = db_record_cvat_task(payload, result)
                self.append_event("cvat_tasks.jsonl", task)
                self.send_json({**result, "task": task})
                return
            if path == "/api/cvat/annotations":
                task_id = int(payload.get("task_id") or 0)
                video_id = str(payload.get("video_id") or "").strip()
                if task_id <= 0 or not video_id:
                    raise ValueError("请指定 CVAT 任务和视频")
                result = cvat_push_annotations(task_id, video_id)
                self.append_event("cvat_annotation_sync.jsonl", result)
                self.send_json(result)
                return
            if path == "/api/production-lines/select":
                line = select_production_line(str(payload.get("line_id") or ""))
                self.append_event("production_line_switches.jsonl", {"line_id": line.get("id"), "line_name": line.get("name"), "primary_model": line.get("primary_model")})
                self.send_json({"ok": True, "active_line": line, "camera_model_path": str(LINE_MODEL_PATHS.get(str(line.get("id")))), "message": f"已切换到 {line.get('name')}；实时相机服务已停止，重新启动后加载对应模型"})
                return
            if path == "/api/models/pcb/select":
                model_id = str(payload.get("model_id") or "")
                registry = pcb_model_registry()
                model = next((item for item in registry.get("models", []) if item.get("id") == model_id), None)
                if model is None:
                    self.send_json({"ok": False, "message": "模型不存在"}, 404)
                    return
                if not model.get("selectable"):
                    self.send_json({"ok": False, "message": "该模型权重或验证报告尚未完成，不能选择"}, 409)
                    return
                selection = {"model_id": model_id, "weight": model["weight"], "selected_at": time.strftime("%Y-%m-%d %H:%M:%S"), "release": "HOLD"}
                ACTIVE_MODEL_SELECTION_PATH.parent.mkdir(parents=True, exist_ok=True)
                temporary = ACTIVE_MODEL_SELECTION_PATH.with_suffix(".tmp")
                temporary.write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temporary, ACTIVE_MODEL_SELECTION_PATH)
                LINE_MODEL_PATHS["pcb"] = ROOT / str(model["weight"])
                for service in LIVE_CAMERAS.values():
                    service.stop()
                    service.model_path = LINE_MODEL_PATHS["pcb"]
                self.append_event("model_selections.jsonl", {**selection, "dataset": model.get("dataset"), "metrics": model.get("metrics")})
                self.send_json({"ok": True, "model": model, "message": f"已选择 {model.get('name')}；保持HOLD，实时检测重启后生效"})
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
                refresh_camera_services()
                services = ([service for service in LIVE_CAMERAS.values() if service.source]
                            if selected == "all" else [camera_service(int(selected))])
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
            if path == "/api/camera/refresh":
                sources = refresh_camera_services()
                inventory = device_inventory()
                self.send_json({"ok": True, "message": f"已重新扫描，发现 {len(sources)} 路视频源", "sources": sources, "insta360": inventory.get("insta360")})
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
    restore_selected_pcb_model()
    host, port = os.getenv("SOP_HOST", "0.0.0.0"), int(os.getenv("SOP_PORT", "8096"))
    if os.getenv("SOP_CAMERA_AUTOSTART", "0").strip().lower() in {"1", "true", "yes", "on"}:
        refresh_camera_services()
        for service in LIVE_CAMERAS.values():
            if not service.source:
                continue
            try:
                service.start()
            except Exception as exc:
                print(f"摄像头 {service.camera_id} 自动启动失败：{exc}", file=sys.stderr)
    print(f"宁波SOP分析平台已启动：http://127.0.0.1:{port}")
    print(f"局域网访问地址：http://{primary_lan_address()}:{port}")
    print(f"证据保存目录：{EVIDENCE_ROOT}")
    print("按 Ctrl+C 停止服务")
    ThreadingHTTPServer((host, port), SOPHandler).serve_forever()


if __name__ == "__main__":
    main()
