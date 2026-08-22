#!/usr/bin/env python3
"""打包可交付源代码、配置、文档和模型，不重复打包原始视频数据。"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


EXCLUDE_DIRS = {"__pycache__", ".git", ".venv"}
EXCLUDE_PREFIXES = {"web/media", "web/data", "datasets", "analysis"}
INCLUDE_DIRS = {"config", "docs", "models", "qa", "scripts", "web"}
INCLUDE_FILES = {"server.py", "requirements.txt", "README_运行与交付索引.md", "启动SOP平台.ps1", "yolo26n.pt"}


def files_for(root: Path, full: bool = False):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        relative = path.relative_to(root)
        if full:
            yield path
            continue
        if any(relative.as_posix() == prefix or relative.as_posix().startswith(prefix + "/") for prefix in EXCLUDE_PREFIXES):
            continue
        if path.name in INCLUDE_FILES or relative.parts[0] in INCLUDE_DIRS:
            yield path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("../宁波SOP平台_源代码与模型_接管包.zip"))
    parser.add_argument("--full", action="store_true", help="包含历史视频、数据集、分析报告和运行记录")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files_for(root, full=args.full):
            relative = path.relative_to(root).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest.append({"path": relative, "bytes": path.stat().st_size, "sha256": digest})
            archive.write(path, f"SOP平台/{relative}")
        archive.writestr("SOP平台/MANIFEST.json", json.dumps({"root": root.name, "mode": "full" if args.full else "source-models", "files": manifest}, ensure_ascii=False, indent=2))
    print(f"created: {args.output}")
    print(f"files: {len(manifest)}")
    print(f"bytes: {args.output.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
