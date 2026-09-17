"""SurveyCraft 图表引擎（M1：基础图表，中文字体内置）。

用法：
    python scripts/charts.py --in data/raw/responses.csv --survey survey.json \
        --profile outputs/profile.json --out-dir outputs/charts

按 profile.json 的变量统计生成标准图表集：
- 分类题：条形图（按量表值排序）
- 数值题：直方图
- 每图输出 PNG（300dpi）+ 聚合数据 CSV（DESIGN.md §17：数字可追溯）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_json, save_text, setup_cjk_font  # noqa: E402

FONT = setup_cjk_font()


def _finish(fig, out_png: Path, agg_rows: list[dict], out_csv: Path) -> None:
    fig.tight_layout()
    fig.savefig(out_png, dpi=300)
    plt.close(fig)
    import csv as _csv
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = _csv.DictWriter(f, fieldnames=list(agg_rows[0].keys()))
        writer.writeheader()
        writer.writerows(agg_rows)


def bar_chart(stats: dict, out_png: Path, out_csv: Path, title: str) -> bool:
    freqs = stats.get("frequencies")
    if not freqs:
        return False
    labels = [f["label"] for f in freqs]
    ns = [f["n"] for f in freqs]
    pcts = [f["pct"] for f in freqs]
    fig, ax = plt.subplots(figsize=(7, max(2.4, 0.55 * len(labels) + 1.2)))
    y = range(len(labels))[::-1]
    ax.barh(list(y), ns, color="#4C72B0")
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels)
    for yi, n, p in zip(y, ns, pcts):
        ax.text(n, yi, f"  {n} ({p}%)", va="center", fontsize=9)
    ax.set_xlabel("人数")
    ax.set_title(title, fontsize=12)
    ax.spines[["top", "right"]].set_visible(False)
    agg = [{"label": l, "n": n, "pct": p} for l, n, p in zip(labels, ns, pcts)]
    _finish(fig, out_png, agg, out_csv)
    return True


def hist_chart(series: pd.Series, out_png: Path, out_csv: Path, title: str) -> bool:
    s = pd.to_numeric(series.dropna(), errors="coerce").dropna()
    if s.nunique() < 3:
        return False
    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.hist(s, bins=min(20, max(5, s.nunique())), color="#4C72B0", edgecolor="white")
    ax.set_xlabel(series.name)
    ax.set_ylabel("人数")
    ax.set_title(title, fontsize=12)
    ax.spines[["top", "right"]].set_visible(False)
    counts, edges = numpy_histogram(s)
    agg = [{"bin_low": edges[i], "bin_high": edges[i + 1], "n": int(c)}
           for i, c in enumerate(counts) if c > 0]
    _finish(fig, out_png, agg, out_csv)
    return True


def numpy_histogram(s: pd.Series):
    import numpy as np
    counts, edges = np.histogram(s, bins=min(20, max(5, s.nunique())))
    return counts, edges


def generate_all(df: pd.DataFrame, profile: dict, out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[dict] = []
    stats_by_var = {v["variable"]: v for v in profile["variables"]}
    for col, stats in stats_by_var.items():
        if col not in df.columns or col in {"respondent_id", "submitted_at", "duration_raw", "ip"}:
            continue
        title = stats.get("label") or col
        if stats.get("type") == "binary":
            continue  # 多选题合并为一张图（下段处理）
        if stats.get("frequencies"):
            ok = bar_chart(stats, out_dir / f"CH_{col}.png",
                           out_dir / f"CH_{col}.csv", title)
        elif stats.get("type") == "numeric":
            ok = hist_chart(df[col], out_dir / f"CH_{col}.png",
                            out_dir / f"CH_{col}.csv", title)
        else:
            ok = False
        if ok:
            made.append({"variable": col, "title": title,
                         "png": str(out_dir / f"CH_{col}.png"),
                         "csv": str(out_dir / f"CH_{col}.csv")})

    # 多选题：每题一张合并条形图
    multi: dict[str, list[dict]] = {}
    for v in profile["variables"]:
        if v.get("type") == "binary" and "·" in (v.get("label") or ""):
            stem = v["label"].split("·")[0]
            multi.setdefault(stem, []).append(v)
    for stem, items in multi.items():
        labels = [item["label"].split("·")[1] for item in
                  sorted(items, key=lambda x: x["variable"])]
        ordered_items = sorted(items, key=lambda x: x["variable"])
        ns = []
        for item in ordered_items:
            s = pd.to_numeric(df[item["variable"]], errors="coerce").dropna()
            ns.append(int((s == 1).sum()))
        total = len(df)
        pcts = [round(n / total * 100, 1) for n in ns]
        fig, ax = plt.subplots(figsize=(7, max(2.4, 0.55 * len(labels) + 1.2)))
        y = list(range(len(labels)))[::-1]
        ax.barh(y, ns, color="#DD8452")
        ax.set_yticks(y)
        ax.set_yticklabels(labels)
        for yi, n, p in zip(y, ns, pcts):
            ax.text(n, yi, f"  {n} ({p}%)", va="center", fontsize=9)
        ax.set_xlabel("选择人数")
        ax.set_title(stem, fontsize=12)
        ax.spines[["top", "right"]].set_visible(False)
        name = "CH_multi_" + str(abs(hash(stem)) % 10000)
        agg_rows = [{"option": l, "n": n, "pct": p} for l, n, p in zip(labels, ns, pcts)]
        _finish(fig, out_dir / f"{name}.png", agg_rows, out_dir / f"{name}.csv")
        made.append({"variable": stem, "title": stem,
                     "png": str(out_dir / f"{name}.png"),
                     "csv": str(out_dir / f"{name}.csv")})
    return made


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft 图表生成")
    parser.add_argument("--in", dest="src", required=True, help="标准化数据 responses.csv")
    parser.add_argument("--profile", required=True, help="profile.json 路径")
    parser.add_argument("--out-dir", dest="out_dir", default="outputs/charts")
    args = parser.parse_args()

    df = pd.read_csv(args.src, encoding="utf-8-sig", dtype=str,
                     keep_default_na=False).replace("", pd.NA)
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    made = generate_all(df, profile, Path(args.out_dir))
    save_json(made, Path(args.out_dir) / "charts.json")
    print(json.dumps({"tool": "charts", "font": FONT, "n_charts": len(made),
                      "charts": made}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
