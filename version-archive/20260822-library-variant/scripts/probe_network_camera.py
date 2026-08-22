#!/usr/bin/env python3
"""Diagnose a network camera before binding it to the SOP live service.

The probe checks TCP reachability first and only invokes ffprobe when the
camera is reachable. Credentials are never printed in the result.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
from urllib.parse import urlparse


def redact(url: str) -> str:
    parsed = urlparse(url)
    if parsed.username is None and parsed.password is None:
        return url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return parsed._replace(netloc=host).geturl()


def main() -> int:
    parser = argparse.ArgumentParser(description="探测网络摄像头 RTSP 地址和视频流")
    parser.add_argument("url", help="厂家 App 提供的完整 rtsp:// 地址")
    parser.add_argument("--timeout", type=float, default=3.0, help="TCP 连接超时秒数")
    args = parser.parse_args()

    parsed = urlparse(args.url)
    if parsed.scheme not in {"rtsp", "rtsps"} or not parsed.hostname:
        print(json.dumps({"ok": False, "stage": "url", "message": "必须提供包含主机和实际路径的 rtsp:// 地址"}, ensure_ascii=False))
        return 2
    port = parsed.port or (322 if parsed.scheme == "rtsps" else 554)
    result = {"url": redact(args.url), "host": parsed.hostname, "port": port, "path": parsed.path}
    try:
        with socket.create_connection((parsed.hostname, port), timeout=args.timeout):
            result["tcp_reachable"] = True
    except OSError as exc:
        result.update({"ok": False, "tcp_reachable": False, "stage": "tcp", "message": str(exc)})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-show_entries", "stream=codec_name,width,height,avg_frame_rate", "-of", "json", args.url],
        capture_output=True,
        text=True,
        timeout=max(args.timeout, 12.0),
        check=False,
    )
    if probe.returncode:
        result.update({"ok": False, "stage": "rtsp", "message": (probe.stderr or "RTSP 地址未返回视频流").strip().splitlines()[-1]})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 4
    result.update({"ok": True, "streams": json.loads(probe.stdout or "{}")})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
