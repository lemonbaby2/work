#!/usr/bin/env python3
"""Build a safe, local-only inventory of SOP code and delivery versions.

The manifest intentionally excludes raw videos, runtime databases, tokens and
passwords. It is suitable for committing to a private Git repository.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
OUTPUT = ROOT / "docs" / "version_manifest.json"
VERSION_ROOTS = [
    ROOT,
    HOME / "Desktop/sop xjai/work-delivery-repo/sop-platform",
    HOME / "Desktop/sop xjai/SOP分析平台_摄像头接入与低延迟标注增强版_20260819/SOP分析平台_老板汇报版",
    HOME / "Desktop/sop_xjai_clean_export2",
    HOME / "Desktop/sop_xjai_clean_export5/SOP分析平台_摄像头接入与低延迟标注增强版_20260819/SOP分析平台_老板汇报版",
    HOME / "宁波SOP平台_源代码与模型_接管包/SOP平台",
]


def git_info(path: Path) -> dict[str, object]:
    if not (path / ".git").exists() and not (path.parent / ".git").exists():
        return {}
    def run(*args: str) -> str:
        result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=False)
        return result.stdout.strip()
    return {"branch": run("branch", "--show-current"), "commit": run("rev-parse", "--short", "HEAD"), "remote": run("remote", "get-url", "origin")}


def summarize(path: Path) -> dict[str, object]:
    files = {"server.py": (path / "server.py").is_file(), "web/index.html": (path / "web/index.html").is_file(), "web/app.js": (path / "web/app.js").is_file(), "web/styles.css": (path / "web/styles.css").is_file()}
    docs = len(list((path / "docs").glob("*") )) if (path / "docs").is_dir() else 0
    scripts = len(list((path / "scripts").glob("*.py"))) if (path / "scripts").is_dir() else 0
    return {"path": str(path), "exists": path.exists(), "entrypoints": files, "doc_files": docs, "script_files": scripts, "git": git_info(path)}


def main() -> None:
    payload = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "policy": "代码/文档可同步；原始视频、runtime数据库、账号密码和访问令牌不进入GitHub", "versions": [summarize(path) for path in VERSION_ROOTS]}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
