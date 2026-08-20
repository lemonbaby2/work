from __future__ import annotations

import argparse
import json
import shutil
import time
import traceback
import zipfile
from pathlib import Path


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def update(status_path: Path, base: dict, **values: object) -> dict:
    base.update(values)
    base["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    atomic_json(status_path, base)
    return base


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an audited SOP object-detection training job")
    parser.add_argument("--job-dir", required=True, type=Path)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--epochs", required=True, type=int)
    parser.add_argument("--batch", required=True, type=int)
    parser.add_argument("--imgsz", required=True, type=int)
    parser.add_argument("--device", required=True)
    parser.add_argument("--workers", required=True, type=int)
    parser.add_argument("--patience", required=True, type=int)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--optimizer", default="auto")
    parser.add_argument("--lr0", type=float, default=0.01)
    parser.add_argument("--weight-decay", type=float, default=0.0005)
    parser.add_argument("--close-mosaic", type=int, default=2)
    parser.add_argument("--freeze", type=int, default=0)
    parser.add_argument("--amp", choices=("true", "false"), default="true")
    parser.add_argument("--cache", choices=("false", "ram", "disk"), default="false")
    parser.add_argument("--target", choices=("jetson", "cuda-server", "onnx-server"), default="jetson")
    parser.add_argument("--truth-mode", choices=("human-confirmed", "candidate-pretrain"), required=True)
    args = parser.parse_args()

    args.job_dir.mkdir(parents=True, exist_ok=True)
    status_path = args.job_dir / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    started = time.time()
    try:
        from ultralytics import YOLO

        update(status_path, status, status="running", stage="训练", progress=5, started_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        model = YOLO(args.model)
        result = model.train(
            data=str(args.data), epochs=args.epochs, batch=args.batch, imgsz=args.imgsz,
            device=args.device, workers=args.workers, patience=args.patience, seed=args.seed,
            deterministic=True, optimizer=args.optimizer, lr0=args.lr0, weight_decay=args.weight_decay,
            close_mosaic=args.close_mosaic, freeze=args.freeze, amp=args.amp == "true" and args.device != "cpu",
            cache=False if args.cache == "false" else args.cache, plots=True, val=True,
            project=str(args.job_dir / "runs"), name="train", exist_ok=True, verbose=True,
        )
        save_dir = Path(result.save_dir)
        best = save_dir / "weights" / "best.pt"
        if not best.is_file():
            raise RuntimeError(f"训练结束但没有生成最佳权重: {best}")
        artifacts = args.job_dir / "artifacts"
        artifacts.mkdir(exist_ok=True)
        best_copy = artifacts / "best.pt"
        shutil.copy2(best, best_copy)

        update(status_path, status, stage="验证", progress=76, best_weight=str(best_copy))
        validated = YOLO(str(best_copy))
        metrics = validated.val(
            data=str(args.data), split="val", batch=args.batch, imgsz=args.imgsz,
            device=args.device, workers=args.workers, project=str(args.job_dir / "runs"),
            name="validation", exist_ok=True, plots=True, verbose=True,
        )
        metric_values = {key: float(value) for key, value in metrics.results_dict.items()}

        update(status_path, status, stage="导出 ONNX", progress=88, metrics=metric_values)
        exported = validated.export(format="onnx", imgsz=args.imgsz, dynamic=True, simplify=False, device=args.device)
        exported_path = Path(str(exported))
        onnx_copy = artifacts / "best.onnx"
        if exported_path.is_file():
            shutil.copy2(exported_path, onnx_copy)

        manifest = {
            "job_id": status["job_id"], "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "target": args.target, "data": str(args.data), "truth_mode": args.truth_mode,
            "parameters": {"epochs": args.epochs, "batch": args.batch, "imgsz": args.imgsz, "device": args.device, "workers": args.workers, "patience": args.patience, "seed": args.seed, "optimizer": args.optimizer, "lr0": args.lr0, "weight_decay": args.weight_decay, "close_mosaic": args.close_mosaic, "freeze": args.freeze, "amp": args.amp == "true", "cache": args.cache},
            "metrics": metric_values, "artifacts": {"pytorch": "best.pt", "onnx": "best.onnx" if onnx_copy.exists() else None},
            "deployment_note": "Jetson TensorRT engine must be built on the target Jetson because engines are GPU/JetPack specific. Use best.onnx or best.pt on the target and run the included command.",
            "truth_notice": "Metrics are production validation only when truth_mode is human-confirmed and train/val/test are frozen by complete video or product SN. Candidate pretraining metrics measure candidate-label agreement only.",
        }
        (artifacts / "deployment_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (artifacts / "JETSON_DEPLOY.txt").write_text(
            "Jetson deployment\n\n"
            "1. Install a JetPack-compatible Ultralytics/TensorRT environment.\n"
            "2. Copy best.pt and run: yolo export model=best.pt format=engine imgsz=%d half=True device=0\n"
            "3. Validate the generated engine on the target camera before production release.\n" % args.imgsz,
            encoding="utf-8",
        )
        package = args.job_dir / f"{status['job_id']}_deployment.zip"
        with zipfile.ZipFile(package, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
            for path in artifacts.iterdir():
                if path.is_file():
                    archive.write(path, path.name)
        update(
            status_path, status, status="completed", stage="完成", progress=100,
            metrics=metric_values, deployment_package=str(package), elapsed_seconds=round(time.time() - started, 1),
            message="真实训练、验证和模型导出已完成",
        )
    except Exception as exc:
        (args.job_dir / "error_traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        update(
            status_path, status, status="failed", stage="失败", progress=status.get("progress", 0),
            elapsed_seconds=round(time.time() - started, 1), message=str(exc),
        )
        raise


if __name__ == "__main__":
    main()
