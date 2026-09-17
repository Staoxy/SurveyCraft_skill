"""SurveyCraft 数据体检（数据字典 + 描述统计）。

用法：
    python scripts/data_profile.py --in data/raw/responses.csv \
        --survey survey.json --out-dir outputs

输入为 data_import 产出的标准化数据。输出：
- outputs/dictionary.csv   数据字典（DESIGN.md §10.3）
- outputs/profile.json     机器可读描述统计（report_build / 后续分析的基础）

规则：
- 所有数字由程序计算，报告生成器只准引用 profile.json（禁止 LLM 手打数字）
- 量表题保留文本标签统计，并记录 label→value 映射供 M2 计分使用
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    MATRIX_TYPES, derived_variable, load_survey, ordered_questions, save_json,
)

NUMERIC_LEVELS = {"interval", "ratio"}


def _to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _frequency(series: pd.Series, order: list[str] | None = None) -> list[dict]:
    counts = series.value_counts(dropna=True)
    if order:
        # 量表/有序题：完整输出所有刻度点（未观测的记 0），保证图表与报告不缺刻度
        labels = list(order) + [l for l in counts.index if l not in order]
    else:
        labels = list(counts.index)
    total = int(counts.sum())
    rows = []
    for label in labels:
        n = int(counts.get(label, 0))
        rows.append({"label": str(label), "n": n,
                     "pct": round(n / total * 100, 1) if total else 0.0})
    return rows


def _numeric_stats(series: pd.Series) -> dict:
    s = _to_numeric(series.dropna())
    if s.empty:
        return {}
    return {
        "mean": round(float(s.mean()), 3),
        "std": round(float(s.std(ddof=1)), 3) if len(s) > 1 else None,
        "median": round(float(s.median()), 3),
        "min": round(float(s.min()), 3),
        "max": round(float(s.max()), 3),
        "q1": round(float(s.quantile(0.25)), 3),
        "q3": round(float(s.quantile(0.75)), 3),
    }


def profile_variable(series: pd.Series, spec: dict | None) -> dict:
    """对单个变量生成字典行 + 统计。spec 来自 survey.json（可为 None → 数据推断）。"""
    spec = spec or {}
    name = spec.get("variable", str(series.name))
    label = spec.get("label", "")
    level = spec.get("measurement_level")
    vtype = spec.get("type")

    missing = int(series.isna().sum())
    entry = {
        "variable": name, "label": label, "type": vtype, "level": level,
        "missing": missing, "n_valid": int(series.notna().sum()),
        "unique": int(series.dropna().nunique()),
    }
    # 透传分析所需的 spec 元数据
    for key in ("question_id", "construct", "analysis_role", "attention_check",
                "reverse_scored", "value_order", "value_map", "scale_item"):
        if key in spec:
            entry[key] = spec[key]

    is_numeric = vtype == "numeric" or (
        vtype is None and not series.dropna().empty
        and pd.to_numeric(series.dropna(), errors="coerce").notna().all()
        and series.dropna().nunique() > 5)

    if is_numeric:
        stats = _numeric_stats(series)
        entry.update({"type": vtype or "numeric",
                      "level": level or "scale",
                      **stats})
    elif vtype in {"text", "meta_text"} or (vtype is None and series.dropna().astype(str).str.len().mean() > 20):
        entry["type"] = vtype or "text"
        entry["level"] = level or "nominal"
        non_empty = series.dropna().astype(str)
        entry["non_empty"] = int((non_empty.str.strip() != "").sum())
    else:
        order = spec.get("value_order")
        entry["type"] = vtype or "categorical"
        entry["level"] = level or "nominal"
        entry["frequencies"] = _frequency(series, order)
        if spec.get("value_map"):
            entry["value_map"] = spec["value_map"]
            total = sum(f["n"] for f in entry["frequencies"])
            if total:
                weighted = sum(f["n"] * v for f in entry["frequencies"]
                               for v in [spec["value_map"].get(str(f["label"]))]
                               if v is not None)
                mapped_n = sum(f["n"] for f in entry["frequencies"]
                               if str(f["label"]) in spec["value_map"])
                if mapped_n:
                    entry["mapped_mean"] = round(weighted / mapped_n, 2)
    return entry


def build_specs(survey: dict | None, df: pd.DataFrame) -> dict[str, dict]:
    """构造 df 每列的字典 spec：survey 优先，其余按列名推断。"""
    specs: dict[str, dict] = {}
    if survey:
        ordered, _ = ordered_questions(survey)
        for q in ordered:
            label = q["text"]
            if q["type"] in MATRIX_TYPES:
                for row in q["rows"]:
                    specs[row["variable"]] = {
                        "variable": row["variable"], "label": f"{label}·{row['text']}",
                        "type": "categorical", "measurement_level": "ordinal",
                        "scale_item": True,
                        "question_id": q["id"], "construct": q.get("construct"),
                        "value_order": q["scale"]["labels"],
                        "value_map": dict(zip(q["scale"]["labels"], q["scale"]["values"])),
                    }
            elif q["type"] == "multiple_choice":
                for o in q.get("options", []):
                    specs[derived_variable(q["id"], o["id"])] = {
                        "variable": derived_variable(q["id"], o["id"]),
                        "label": f"{label}·{o['label']}", "type": "binary",
                        "measurement_level": "nominal", "question_id": q["id"],
                        "construct": q.get("construct"),
                    }
                for o in q.get("options", []):
                    if o.get("other_text"):
                        from common import other_variable
                        specs[other_variable(q["id"], o["id"])] = {
                            "variable": other_variable(q["id"], o["id"]),
                            "label": f"{label}·{o['label']}(填空)", "type": "text",
                            "measurement_level": "nominal", "question_id": q["id"],
                        }
            else:
                level = q.get("measurement_level")
                if level is None and q["type"] in {"likert", "rating", "nps"}:
                    level = "ordinal"
                if level is None and q["type"] in {"number", "integer", "decimal"}:
                    level = "scale"
                spec = {
                    "variable": q.get("variable", q["id"].lower()), "label": label,
                    "question_id": q["id"], "construct": q.get("construct"),
                    "analysis_role": q.get("analysis_role"),
                    "attention_check": q.get("attention_check", False),
                    "reverse_scored": q.get("reverse_scored", False),
                }
                if q["type"] in {"single_choice", "yes_no"}:
                    spec.update({"type": "categorical", "measurement_level": level or "nominal",
                                 "value_order": [o["label"] for o in q.get("options", [])],
                                 "value_map": {o["label"]: o.get("value")
                                               for o in q.get("options", [])}})
                elif q["type"] in {"likert", "rating", "nps"}:
                    spec.update({"type": "categorical", "measurement_level": level or "ordinal",
                                 "scale_item": True,
                                 "value_order": q["scale"]["labels"],
                                 "value_map": dict(zip(q["scale"]["labels"],
                                                       q["scale"]["values"]))})
                elif q["type"] in {"number", "integer", "decimal"}:
                    spec.update({"type": "numeric", "measurement_level": level or "scale"})
                else:
                    spec.update({"type": "text", "measurement_level": "nominal"})
                specs[spec["variable"]] = spec
    # 数据列缺 spec 的（如 meta 列）
    for col in df.columns:
        if col not in specs:
            specs[col] = {"variable": col, "label": ""}
    return specs


def profile(df: pd.DataFrame, survey: dict | None = None,
            source: str = "") -> dict:
    specs = build_specs(survey, df)
    variables = []
    for col in df.columns:
        spec = specs.get(col, {"variable": col, "label": ""})
        variables.append(profile_variable(df[col], spec))

    n = len(df)
    for v in variables:
        v["missing_pct"] = round(v.get("missing", 0) / max(1, n) * 100, 1)
    summary = {
        "n_respondents": n,
        "n_variables": int(df.shape[1]),
        "total_missing_cells": int(df.isna().sum().sum()),
        "missing_rate_pct": round(float(df.isna().sum().sum()) / max(1, df.size) * 100, 1),
    }
    if "duration_sec" in df:
        summary["duration_sec"] = _numeric_stats(df["duration_sec"])

    result = {
        "tool": "data_profile",
        "source": source,
        "survey_id": survey["survey"]["id"] if survey else None,
        "summary": summary,
        "variables": variables,
    }
    return result


def write_dictionary(result: dict, path: str | Path) -> Path:
    rows = []
    for v in result["variables"]:
        rows.append({
            "Variable": v["variable"], "Label": v.get("label", ""),
            "Type": v.get("type", ""), "Level": v.get("level", ""),
            "Missing": v.get("missing", ""),
            "Unique": v.get("unique", ""),
            "Min": v.get("min", ""), "Max": v.get("max", ""),
            "Mean": v.get("mean", ""),
        })
    df = pd.DataFrame(rows)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft 数据体检")
    parser.add_argument("--in", dest="src", required=True, help="标准化数据 responses.csv")
    parser.add_argument("--survey", help="survey.json 路径（强烈建议）")
    parser.add_argument("--out-dir", dest="out_dir", default="outputs")
    args = parser.parse_args()

    df = pd.read_csv(args.src, encoding="utf-8-sig", dtype=str,
                     keep_default_na=False).replace("", pd.NA)
    survey = load_survey(args.survey) if args.survey else None
    result = profile(df, survey, source=args.src)

    out_dir = Path(args.out_dir)
    save_json(result, out_dir / "profile.json")
    write_dictionary(result, out_dir / "dictionary.csv")
    print(json.dumps({
        "tool": "data_profile",
        "profile": str(out_dir / "profile.json"),
        "dictionary": str(out_dir / "dictionary.csv"),
        "summary": result["summary"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
