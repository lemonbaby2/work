from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT.parent
CODE_ZIP = OUT_DIR / "宁波SOP分析平台_五视频前沿算法_全部代码_2026-08-16.zip"
FULL_ZIP = OUT_DIR / "宁波SOP分析平台_五视频前沿算法_完整交付_2026-08-16.zip"
HASH_FILE = OUT_DIR / "五视频前沿算法压缩包_SHA256校验值_2026-08-16.txt"

SKIP_PARTS = {"__pycache__", ".git", ".pytest_cache", ".mypy_cache"}
CODE_SKIP_ROOTS = {"models", "datasets", "analysis", "runs"}
CODE_SKIP_WEB = {"media", "snapshots"}
STORE_EXTENSIONS = {".mp4", ".pt", ".onnx", ".ts", ".jpg", ".jpeg", ".png", ".zip"}


def include(path: Path, code_only: bool) -> bool:
    relative = path.relative_to(ROOT)
    if any(part in SKIP_PARTS for part in relative.parts) or path.suffix.lower() in {".pyc", ".tmp"}:
        return False
    if code_only and relative.parts:
        if relative.parts[0] in CODE_SKIP_ROOTS:
            return False
        if len(relative.parts) > 1 and relative.parts[0] == "web" and relative.parts[1] in CODE_SKIP_WEB:
            return False
        if relative.parts[0] == "qa" and path.suffix.lower() in {".jpg", ".png", ".mp4"}:
            return False
    return True


def build(target: Path, code_only: bool) -> tuple[int, int]:
    if target.exists():
        raise FileExistsError(f"为避免覆盖已有交付包，已停止：{target}")
    files = [path for path in ROOT.rglob("*") if path.is_file() and include(path, code_only)]
    with zipfile.ZipFile(target, "w", allowZip64=True) as archive:
        for path in files:
            compression = zipfile.ZIP_STORED if path.suffix.lower() in STORE_EXTENSIONS else zipfile.ZIP_DEFLATED
            archive.write(path, arcname=(ROOT.name / path.relative_to(ROOT)) if False else str(Path(ROOT.name) / path.relative_to(ROOT)), compress_type=compression, compresslevel=None if compression == zipfile.ZIP_STORED else 6)
    return len(files), target.stat().st_size


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    code_count, code_size = build(CODE_ZIP, True)
    print(f"代码包完成：{code_count}个文件，{code_size / 1024 / 1024:.1f}MB")
    full_count, full_size = build(FULL_ZIP, False)
    print(f"完整包完成：{full_count}个文件，{full_size / 1024 / 1024:.1f}MB")
    lines = [
        f"{sha256(CODE_ZIP)}  {CODE_ZIP.name}",
        f"{sha256(FULL_ZIP)}  {FULL_ZIP.name}",
        "",
        "说明：代码包不含视频、模型、数据集、训练运行目录和大图；完整包包含全部交付工程。",
    ]
    HASH_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(HASH_FILE)


if __name__ == "__main__":
    main()
