from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets" / "新增两视频_YOLOE26_SAHI细粒度预标注_待人工复核"
OUT = ROOT / "analysis" / "新增两视频_前沿算法可视化"
RUN = ROOT / "runs" / "frontier_yolo26" / "两视频小目标蒸馏" / "results.csv"
CLASSES = [
    "仪表板总成", "仪表板骨架", "饰板总成", "线束", "电气接插件", "电动紧固工具",
    "操作人员手部", "螺钉头候选", "螺栓头候选", "塑料卡扣候选", "线束插头候选", "紧固孔候选",
]


def configure_font() -> None:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def pil_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def contact_sheet(video_id: str, title: str, output: Path) -> None:
    paths = sorted((ROOT / "web" / "snapshots" / video_id).glob("*.jpg"))
    cards = []
    for path in paths[:6]:
        image = Image.open(path).convert("RGB")
        image.thumbnail((760, 338), Image.Resampling.LANCZOS)
        card = Image.new("RGB", (780, 390), "white")
        card.paste(image, ((780 - image.width) // 2, 12))
        ImageDraw.Draw(card).text((20, 352), path.stem, fill="#17384a", font=pil_font(24))
        cards.append(card)
    canvas = Image.new("RGB", (1600, 1280), "#edf2f4")
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 30), title, fill="#102231", font=pil_font(42))
    draw.text((55, 88), "左侧为完整检测画面，右侧为独立SOP决策栏；顶部无任何遮挡层", fill="#526671", font=pil_font(24))
    for index, card in enumerate(cards):
        x = 20 + (index % 2) * 790
        y = 140 + (index // 2) * 375
        canvas.paste(card, (x, y))
    canvas.save(output, quality=94)


def detail_sheet(video_id: str, title: str, output: Path) -> None:
    paths = sorted((ROOT / "web" / "snapshots" / video_id).glob("*.jpg"))
    canvas = Image.new("RGB", (1600, 1050), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text((50, 28), title, fill="#102231", font=pil_font(42))
    draw.text((50, 85), "放大查看卡扣、紧固孔、插头和工具候选；所有候选仍需人工确认", fill="#8a5b18", font=pil_font(24))
    positions = [(30, 150), (815, 150), (30, 590), (815, 590)]
    for index, pos in enumerate(positions):
        path = paths[min(index + 1, len(paths) - 1)]
        image = Image.open(path).convert("RGB")
        # 只取左侧1280×720检测画面，再按不同区域放大，避免右侧面板进入细节图。
        image = image.crop((0, 0, min(1280, image.width), image.height))
        w, h = image.size
        regions = [
            (int(w * .05), int(h * .12), int(w * .55), int(h * .72)),
            (int(w * .42), int(h * .08), int(w * .96), int(h * .70)),
            (int(w * .10), int(h * .38), int(w * .68), int(h * .98)),
            (int(w * .38), int(h * .35), int(w * .98), int(h * .98)),
        ]
        crop = image.crop(regions[index])
        crop.thumbnail((750, 380), Image.Resampling.LANCZOS)
        card = Image.new("RGB", (755, 410), "#f5f8f9")
        card.paste(crop, ((755 - crop.width) // 2, 0))
        ImageDraw.Draw(card).text((15, 377), f"局部{index + 1}｜{path.stem}", fill="#17384a", font=pil_font(21))
        canvas.paste(card, pos)
    canvas.save(output, quality=94)


def read_labels() -> tuple[Counter, list[float], Counter]:
    counts: Counter = Counter()
    areas: list[float] = []
    split_counts: Counter = Counter()
    class_to_id = {name: index for index, name in enumerate(CLASSES)}
    manifest = DATASET / "manifest.jsonl"
    for line in manifest.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        split_counts[row["split"]] += 1
        image_area = 1280 * 720 if row["video_id"] == "video_40b5" else 544 * 960
        for annotation in row.get("annotations", []):
            class_id = class_to_id.get(annotation.get("class"))
            if class_id is None:
                continue
            x1, y1, x2, y2 = annotation["box"]
            counts[class_id] += 1
            areas.append(max(0.0, x2 - x1) * max(0.0, y2 - y1) / image_area * 100)
    return counts, areas, split_counts


def save_bar_chart(counts: Counter, output: Path) -> None:
    values = [counts.get(i, 0) for i in range(len(CLASSES))]
    fig, ax = plt.subplots(figsize=(14, 7.6), dpi=160)
    colors = ["#0d8f79" if i < 7 else "#d48716" for i in range(len(CLASSES))]
    bars = ax.barh(CLASSES[::-1], values[::-1], color=colors[::-1])
    ax.bar_label(bars, padding=4, fontsize=9)
    ax.set_title("新增两视频：12类自动预标注实例分布（待人工复核）", fontsize=17, pad=18)
    ax.set_xlabel("候选框数量（个）")
    ax.grid(axis="x", alpha=.2)
    fig.text(.5, .01, "绿色：大/中目标；橙色：螺钉头、卡扣、插头、紧固孔等小目标候选", ha="center", fontsize=10)
    fig.tight_layout(rect=(0, .04, 1, 1))
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def save_area_chart(areas: list[float], output: Path) -> None:
    data = np.asarray(areas)
    bins = [0, .01, .03, .1, .3, 1, 3, 10, 100]
    hist, edges = np.histogram(data, bins=bins)
    labels = [f"{edges[i]:g}–{edges[i+1]:g}%" for i in range(len(edges) - 1)]
    fig, ax = plt.subplots(figsize=(13, 7), dpi=160)
    bars = ax.bar(labels, hist, color="#297ba5")
    ax.bar_label(bars, padding=3, fontsize=9)
    ax.set_title("目标框面积分布：切片推理重点补足极小目标", fontsize=17, pad=18)
    ax.set_xlabel("候选框占整幅图像面积")
    ax.set_ylabel("候选框数量（个）")
    ax.tick_params(axis="x", rotation=30)
    ax.grid(axis="y", alpha=.2)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def read_training() -> list[dict[str, float]]:
    with RUN.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key.strip(): float(value) for key, value in row.items()} for row in csv.DictReader(handle)]


def save_training_curves(rows: list[dict[str, float]], output: Path) -> None:
    epochs = [row["epoch"] for row in rows]
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.8), dpi=160)
    axes[0].plot(epochs, [row["train/box_loss"] for row in rows], "o-", label="训练框损失", color="#0d8f79")
    axes[0].plot(epochs, [row["val/box_loss"] for row in rows], "s-", label="验证框损失", color="#297ba5")
    axes[0].set_title("边框回归损失")
    axes[1].plot(epochs, [row["train/cls_loss"] for row in rows], "o-", label="训练分类损失", color="#d48716")
    axes[1].plot(epochs, [row["val/cls_loss"] for row in rows], "s-", label="验证分类损失", color="#c94842")
    axes[1].set_title("类别损失")
    for ax in axes:
        ax.set_xlabel("训练轮次")
        ax.set_ylabel("损失值（越低越好）")
        ax.grid(alpha=.2)
        ax.legend()
    fig.suptitle("YOLO26N 两视频小目标学生模型训练过程", fontsize=17)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def save_consistency_curves(rows: list[dict[str, float]], output: Path) -> None:
    epochs = [row["epoch"] for row in rows]
    fig, ax = plt.subplots(figsize=(13, 7), dpi=160)
    for key, label, color in [
        ("metrics/precision(B)", "精确率", "#0d8f79"),
        ("metrics/recall(B)", "召回率", "#d48716"),
        ("metrics/mAP50(B)", "mAP@0.50", "#297ba5"),
        ("metrics/mAP50-95(B)", "mAP@0.50:0.95", "#c94842"),
    ]:
        ax.plot(epochs, [row[key] * 100 for row in rows], marker="o", label=label, color=color)
    ax.set_title("学生模型与教师自动预标注的一致性（不等于量产真实精度）", fontsize=17, pad=18)
    ax.set_xlabel("训练轮次")
    ax.set_ylabel("一致性指标（%）")
    ax.set_ylim(0, 100)
    ax.grid(alpha=.2)
    ax.legend(ncol=2)
    fig.text(.5, .01, "正式精度必须在人工复核后的冻结测试集上重新测量", ha="center", color="#9b3d38", fontsize=11)
    fig.tight_layout(rect=(0, .04, 1, 1))
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def save_split_chart(splits: Counter, output: Path) -> None:
    labels = ["训练集", "验证集", "测试集"]
    values = [splits["train"], splits["val"], splits["test"]]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.8), dpi=160)
    axes[0].pie(values, labels=labels, autopct="%1.1f%%", colors=["#0d8f79", "#297ba5", "#d48716"], startangle=90)
    bars = axes[1].bar(labels, values, color=["#0d8f79", "#297ba5", "#d48716"])
    axes[1].bar_label(bars, labels=[f"{v}张" for v in values], padding=4)
    axes[1].set_ylabel("抽帧图像数量")
    axes[1].grid(axis="y", alpha=.2)
    fig.suptitle("新增两视频数据集划分：共544张", fontsize=17)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def save_flow(output: Path) -> None:
    fig, ax = plt.subplots(figsize=(16, 6), dpi=160)
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 6)
    ax.axis("off")
    nodes = [
        ("固定工业相机", "稳定视角、补光"),
        ("YOLOE-26S", "大零件掩膜紧边框"),
        ("SAHI重叠切片", "螺钉/卡扣/孔候选"),
        ("跟踪与去抖", "连续帧确认"),
        ("SOP状态机", "顺序、超时、错序"),
        ("扭矩+MES门", "缺证据一律HOLD"),
    ]
    for index, (name, note) in enumerate(nodes):
        x = .25 + index * 2.62
        box = FancyBboxPatch((x, 2.1), 2.15, 1.65, boxstyle="round,pad=.08,rounding_size=.12",
                             facecolor="#e8f4f1" if index < 4 else "#fff1dd", edgecolor="#0d8f79" if index < 4 else "#d48716", linewidth=2)
        ax.add_patch(box)
        ax.text(x + 1.075, 3.15, name, ha="center", va="center", fontsize=13, fontweight="bold")
        ax.text(x + 1.075, 2.58, note, ha="center", va="center", fontsize=10, color="#526671")
        if index < len(nodes) - 1:
            ax.annotate("", xy=(x + 2.58, 2.92), xytext=(x + 2.18, 2.92), arrowprops=dict(arrowstyle="->", lw=2, color="#657783"))
    ax.text(8, 5.2, "仪表板装配SOP智能分析决策链", ha="center", fontsize=20, fontweight="bold")
    ax.text(8, .7, "开放词汇模型用于离线预标注；量产在线端采用人工真值训练的闭集模型并支持回滚", ha="center", fontsize=12, color="#9b3d38")
    fig.savefig(output, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def pil_chart_base(title: str, subtitle: str = "") -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (1800, 1050), "white")
    draw = ImageDraw.Draw(image)
    draw.text((70, 38), title, fill="#102231", font=pil_font(42))
    if subtitle:
        draw.text((70, 98), subtitle, fill="#657783", font=pil_font(23))
    draw.line((120, 880, 1720, 880), fill="#526671", width=3)
    draw.line((120, 180, 120, 880), fill="#526671", width=3)
    return image, draw


def pil_bar_chart(counts: Counter, output: Path) -> None:
    image, draw = pil_chart_base("新增两视频：12类自动预标注实例分布（待人工复核）", "绿色为大/中目标，橙色为小目标候选")
    values = [counts.get(i, 0) for i in range(len(CLASSES))]
    maximum = max(values) or 1
    left, top, chart_w, row_h = 410, 180, 1260, 55
    for index, (name, value) in enumerate(zip(CLASSES, values)):
        y = top + index * row_h
        color = "#0d8f79" if index < 7 else "#d48716"
        draw.text((70, y + 7), name, fill="#17384a", font=pil_font(22))
        width = int(chart_w * value / maximum)
        draw.rounded_rectangle((left, y + 6, left + max(3, width), y + 38), radius=7, fill=color)
        draw.text((left + width + 12, y + 6), str(value), fill="#17242d", font=pil_font(20))
    image.save(output)


def pil_hist_chart(areas: list[float], output: Path) -> None:
    bins = [0, .01, .03, .1, .3, 1, 3, 10, 100]
    hist, edges = np.histogram(np.asarray(areas), bins=bins)
    labels = [f"{edges[i]:g}–{edges[i+1]:g}%" for i in range(len(edges) - 1)]
    image, draw = pil_chart_base("目标框面积分布：切片推理重点补足极小目标", "横轴为候选框占整幅图像面积，纵轴为候选数量")
    maximum = max(hist) or 1
    chart_left, chart_top, chart_bottom, chart_w = 150, 190, 850, 1500
    gap = chart_w / len(hist)
    for index, value in enumerate(hist):
        x1 = int(chart_left + index * gap + 18)
        x2 = int(chart_left + (index + 1) * gap - 18)
        height = int((chart_bottom - chart_top) * value / maximum)
        draw.rectangle((x1, chart_bottom - height, x2, chart_bottom), fill="#297ba5")
        draw.text((x1, chart_bottom - height - 35), str(int(value)), fill="#17242d", font=pil_font(19))
        draw.text((x1 - 5, chart_bottom + 18), labels[index], fill="#526671", font=pil_font(17))
    image.save(output)


def pil_line_chart(series: list[tuple[str, list[float], str]], epochs: list[float], title: str, subtitle: str, output: Path, percent: bool = False) -> None:
    image, draw = pil_chart_base(title, subtitle)
    left, top, right, bottom = 150, 200, 1700, 850
    all_values = [value for _, values, _ in series for value in values]
    y_min = 0.0 if percent else min(all_values) * .9
    y_max = 100.0 if percent else max(all_values) * 1.08
    for tick in range(6):
        y = bottom - int((bottom - top) * tick / 5)
        value = y_min + (y_max - y_min) * tick / 5
        draw.line((left, y, right, y), fill="#d9e2e7", width=2)
        draw.text((55, y - 14), f"{value:.0f}" if percent else f"{value:.2f}", fill="#657783", font=pil_font(18))
    for name, values, color in series:
        points = []
        for index, value in enumerate(values):
            x = left + int((right - left) * index / max(1, len(values) - 1))
            y = bottom - int((bottom - top) * (value - y_min) / max(1e-9, y_max - y_min))
            points.append((x, y))
        draw.line(points, fill=color, width=5)
        for point in points:
            draw.ellipse((point[0] - 6, point[1] - 6, point[0] + 6, point[1] + 6), fill=color)
    for index, epoch in enumerate(epochs):
        x = left + int((right - left) * index / max(1, len(epochs) - 1))
        draw.text((x - 8, bottom + 20), str(int(epoch)), fill="#526671", font=pil_font(17))
    legend_x = 180
    for name, _, color in series:
        draw.rectangle((legend_x, 930, legend_x + 35, 950), fill=color)
        draw.text((legend_x + 45, 920), name, fill="#17242d", font=pil_font(20))
        legend_x += 340
    image.save(output)


def pil_split_chart(splits: Counter, output: Path) -> None:
    image, draw = pil_chart_base("新增两视频数据集划分：共544张", "按连续时间块划分，避免相邻帧同时进入训练集和测试集")
    labels = [("训练集", splits["train"], "#0d8f79"), ("验证集", splits["val"], "#297ba5"), ("测试集", splits["test"], "#d48716")]
    maximum = max(value for _, value, _ in labels)
    for index, (name, value, color) in enumerate(labels):
        x1 = 320 + index * 470
        x2 = x1 + 300
        height = int(560 * value / maximum)
        draw.rounded_rectangle((x1, 820 - height, x2, 820), radius=14, fill=color)
        draw.text((x1 + 80, 835), name, fill="#17242d", font=pil_font(28))
        draw.text((x1 + 82, 770 - height), f"{value}张", fill="#17242d", font=pil_font(30))
        draw.text((x1 + 76, 880), f"{value / 544 * 100:.1f}%", fill="#657783", font=pil_font(24))
    image.save(output)


def pil_flow(output: Path) -> None:
    image = Image.new("RGB", (1900, 850), "white")
    draw = ImageDraw.Draw(image)
    draw.text((450, 55), "仪表板装配SOP智能分析决策链", fill="#102231", font=pil_font(48))
    nodes = [("固定工业相机", "稳定视角、补光"), ("YOLOE-26S", "大零件紧边框"), ("重叠切片", "螺钉/卡扣/孔"), ("跟踪去抖", "连续帧确认"), ("SOP状态机", "顺序与超时"), ("扭矩+MES", "缺证据HOLD")]
    for index, (name, note) in enumerate(nodes):
        x = 35 + index * 310
        color = "#e8f4f1" if index < 4 else "#fff1dd"
        edge = "#0d8f79" if index < 4 else "#d48716"
        draw.rounded_rectangle((x, 285, x + 255, 520), radius=22, fill=color, outline=edge, width=5)
        draw.text((x + 28, 340), name, fill="#17242d", font=pil_font(29))
        draw.text((x + 28, 420), note, fill="#526671", font=pil_font(21))
        if index < len(nodes) - 1:
            draw.line((x + 263, 403, x + 302, 403), fill="#657783", width=6)
            draw.polygon([(x + 302, 403), (x + 282, 390), (x + 282, 416)], fill="#657783")
    draw.text((310, 690), "离线开放词汇模型用于预标注；量产采用人工真值训练的闭集模型，并保留灰度发布与回滚", fill="#9b3d38", font=pil_font(27))
    image.save(output)


def main() -> None:
    configure_font()
    OUT.mkdir(parents=True, exist_ok=True)
    contact_sheet("video_40b5", "视频四：六步SOP检测与决策总览", OUT / "01_视频四_六步SOP总览.jpg")
    contact_sheet("video_ecc57", "视频五：六步SOP检测与决策总览", OUT / "02_视频五_六步SOP总览.jpg")
    detail_sheet("video_40b5", "视频四：细粒度小目标候选局部放大", OUT / "03_视频四_小目标细节.jpg")
    detail_sheet("video_ecc57", "视频五：细粒度小目标候选局部放大", OUT / "04_视频五_小目标细节.jpg")
    counts, areas, splits = read_labels()
    pil_bar_chart(counts, OUT / "05_十二类预标注分布.png")
    pil_hist_chart(areas, OUT / "06_目标框尺寸分布.png")
    rows = read_training()
    epochs = [row["epoch"] for row in rows]
    pil_line_chart([("训练框损失", [r["train/box_loss"] for r in rows], "#0d8f79"), ("验证框损失", [r["val/box_loss"] for r in rows], "#297ba5"), ("训练分类损失", [r["train/cls_loss"] for r in rows], "#d48716"), ("验证分类损失", [r["val/cls_loss"] for r in rows], "#c94842")], epochs, "YOLO26N 两视频小目标学生模型训练过程", "纵轴为损失值，训练12轮；分类损失较高说明细粒度类别仍需更多人工真值", OUT / "07_YOLO26训练损失曲线.png")
    pil_line_chart([("精确率", [r["metrics/precision(B)"] * 100 for r in rows], "#0d8f79"), ("召回率", [r["metrics/recall(B)"] * 100 for r in rows], "#d48716"), ("mAP@0.50", [r["metrics/mAP50(B)"] * 100 for r in rows], "#297ba5"), ("mAP@0.50:0.95", [r["metrics/mAP50-95(B)"] * 100 for r in rows], "#c94842")], epochs, "学生模型与教师自动预标注的一致性", "这些指标不等于人工真值量产精度；正式精度必须在冻结测试集重测", OUT / "08_教师伪标签一致性曲线.png", percent=True)
    pil_split_chart(splits, OUT / "09_训练验证测试集划分.png")
    pil_flow(OUT / "10_前沿SOP算法流程图.png")
    report = {
        "outputs": [path.name for path in sorted(OUT.glob("*.*")) if path.suffix.lower() in {".jpg", ".png"}],
        "count": 10,
        "truth_boundary": "图中训练指标是学生模型与自动教师预标注的一致性，不是人工真值量产精度。",
    }
    (OUT / "可视化说明.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
