#!/usr/bin/env python3
"""Expand the recorded model benchmark into a ten-chart evidence pack.

This consumes benchmark_report.json and does not rerun GPU inference, so it is
safe to use on a review machine without changing the recorded measurements.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "analysis" / "model_benchmark"
REPORT = OUT / "benchmark_report.json"


def save(fig: plt.Figure, name: str, charts: list[str]) -> None:
    fig.savefig(OUT / name, dpi=160, bbox_inches="tight")
    plt.close(fig)
    charts.append(name)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    rows = payload["models"]
    names = [row["name"] for row in rows]
    colors = ["#0d8f79", "#297ba5", "#d48716", "#7a5aa6", "#c94842"][: len(rows)]
    charts = [name for name in payload.get("charts", []) if (OUT / name).exists()]

    fig, ax = plt.subplots(figsize=(10, 5.5)); ax.bar(names, [row["detections_per_image"] for row in rows], color=colors); ax.set_title("平均每图检测框数量"); ax.set_ylabel("框 / 图"); ax.tick_params(axis="x", rotation=20); ax.grid(axis="y", alpha=.25); save(fig, "05_平均检测数量对比.png", charts)
    fig, ax = plt.subplots(figsize=(10, 5.5)); ax.bar(names, [row["non_empty_ratio"] * 100 for row in rows], color=colors); ax.axhline(50, color="#8a9aa0", ls="--"); ax.set_title("非空帧比例：输出稳定性信号"); ax.set_ylabel("百分比"); ax.tick_params(axis="x", rotation=20); ax.grid(axis="y", alpha=.25); save(fig, "06_非空帧比例.png", charts)
    fig, ax = plt.subplots(figsize=(10, 5.5)); ax.bar(names, [row["mean_confidence"] * 100 for row in rows], color=colors); ax.set_title("平均置信度对比（运行信号）"); ax.set_ylabel("百分比"); ax.set_ylim(0, 100); ax.tick_params(axis="x", rotation=20); ax.grid(axis="y", alpha=.25); save(fig, "07_平均置信度对比.png", charts)
    fig, ax = plt.subplots(figsize=(10, 5.5)); x = np.arange(len(rows)); width=.36; ax.bar(x-width/2, [row["latency_ms"] for row in rows], width, label="延迟 ms", color="#d16d45"); ax2=ax.twinx(); ax2.bar(x+width/2, [row["fps"] for row in rows], width, label="FPS", color="#35a98f"); ax.set_xticks(x, names, rotation=20); ax.set_title("延迟与吞吐双轴对比"); ax.set_ylabel("毫秒"); ax2.set_ylabel("帧/秒"); save(fig, "08_延迟吞吐双轴.png", charts)
    fig, ax = plt.subplots(figsize=(8, 7)); metrics = ["速度", "稳定性", "置信度", "部署余量"]; angles = np.linspace(0, 2*np.pi, len(metrics), endpoint=False).tolist(); angles += angles[:1]
    for row, color in zip(rows, colors):
        speed = max(0, 100 * (1 - row["latency_ms"] / max(r["latency_ms"] for r in rows))); values = [speed, row["non_empty_ratio"]*100, row["mean_confidence"]*100, min(100, row["fps"] / max(r["fps"] for r in rows) * 100)]; values += values[:1]; ax.plot(angles, values, color=color, label=row["name"]); ax.fill(angles, values, color=color, alpha=.06)
    ax.set_xticks(angles[:-1], metrics); ax.set_ylim(0, 100); ax.set_title("模型部署雷达图（相对运行信号）"); ax.legend(fontsize=8, loc="upper right", bbox_to_anchor=(1.3, 1.1)); save(fig, "09_模型部署雷达图.png", charts)
    fig, ax = plt.subplots(figsize=(10, 5.5)); x = np.arange(len(rows)); ax.scatter([row["latency_ms"] for row in rows], [row["non_empty_ratio"]*100 for row in rows], s=[max(90, row["fps"]*2) for row in rows], c=colors); [ax.annotate(row["name"], (row["latency_ms"], row["non_empty_ratio"]*100), xytext=(6,6), textcoords="offset points") for row in rows]; ax.set_xlabel("延迟（ms，越低越好）"); ax.set_ylabel("非空帧比例（%）"); ax.set_title("实时性与稳定性权衡"); ax.grid(alpha=.25); save(fig, "10_实时性稳定性权衡.png", charts)
    payload["charts"] = charts
    payload["chart_count"] = len(charts)
    payload["chart_truth_boundary"] = "十张图均来自同一份运行记录；预标注一致性指标和运行信号不能替代人工冻结测试集量产精度。"
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"chart_count": len(charts), "charts": charts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
