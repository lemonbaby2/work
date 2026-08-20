from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "spark_deployment.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_file(source: Path, target: Path) -> str:
    if target.exists() and source.stat().st_size == target.stat().st_size:
        return "already-present"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
        return "hard-linked"
    except OSError:
        shutil.copy2(source, target)
        return "copied"


def main() -> None:
    parser = argparse.ArgumentParser(description="Register local SOP models in the DGX Spark model store")
    parser.add_argument("--target", type=Path, default=None)
    parser.add_argument("--apply", action="store_true", help="Create links/copies and write the manifest")
    args = parser.parse_args()
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    target = args.target or Path(os.getenv(config["model_store_env"], config["default_model_store"]))
    records = []
    for model in config["models"]:
        filename = model.get("file")
        source = ROOT / "models" / filename if filename else None
        target_path = target / filename if filename else None
        source_exists = bool(source and source.is_file())
        target_exists = bool(target_path and target_path.is_file())
        artifact = source if source_exists else target_path if target_exists else None
        record = {
            **model,
            "source": str(source) if source_exists else None,
            "exists": bool(artifact),
            "target": str(target_path) if target_path else None,
        }
        if artifact:
            record["bytes"] = artifact.stat().st_size
            record["sha256"] = sha256(artifact)
            if source_exists and target_path:
                record["sync"] = install_file(source, target_path) if args.apply else "dry-run"
            else:
                record["sync"] = "already-present-in-spark"
        else:
            record["sync"] = "not-available"
        records.append(record)
    manifest = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host": socket.gethostname(),
        "target": str(target),
        "apply": args.apply,
        "models": records,
    }
    if args.apply:
        target.mkdir(parents=True, exist_ok=True)
        (target / "registry.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        runtime = ROOT / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "spark_model_registry.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
