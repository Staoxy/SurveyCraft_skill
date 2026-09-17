"""SurveyCraft 量表计分引擎（DESIGN.md §6）。

用法：
    python scripts/scale_score.py --in data/raw/responses.csv --survey survey.json \
        --out-dir outputs --min-answered-ratio 0.67

规则：
- 构念分组：`construct` 相同且为量表题（likert/matrix 行/rating）的所有题目合成一个维度；
  `attention_check: true` 的题不参与任何构念
- 反向计分：`reverse_scored: true` 的题项 new = (min + max) − old
- 维度分：prorated mean（已答题项均分）；已答比例 < min-answered-ratio 记缺失
- 输出：data/clean/responses_scored.csv（原始列 + <variable>_num 数值题项列 + score_<构念> 列）
  与 outputs/scoring_log.json（每个构念的题项清单、反向题、缺失处理统计）

反向语义约定：reverse_scored 表示"原始作答越高，构念水平越低"——即该题是构念内的
负向措辞题，计分时翻转。
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
    MATRIX_TYPES, load_survey, ordered_questions, save_json,
)


def build_constructs(survey: dict) -> dict[str, dict]:
    """返回 {构念名: {items: [...], reverse_items: [...], scale: {...}}}。"""
    ordered, _ = ordered_questions(survey)
    constructs: dict[str, dict] = {}
    for q in ordered:
        if q.get("attention_check"):
            continue
        construct = q.get("construct")
        if not construct:
            continue
        items: list[dict] = []
        if q["type"] in MATRIX_TYPES:
            for row in q["rows"]:
                items.append({
                    "variable": row["variable"], "label": f"{q['text']}·{row['text']}",
                    "question_id": q["id"],
                    "reverse": bool(q.get("reverse_scored")),
                    "value_map": dict(zip(q["scale"]["labels"], q["scale"]["values"])),
                    "min": q["scale"]["min"], "max": q["scale"]["max"],
                })
        elif q["type"] in {"likert", "rating", "nps"}:
            items.append({
                "variable": q.get("variable", q["id"].lower()),
                "label": q["text"], "question_id": q["id"],
                "reverse": bool(q.get("reverse_scored")),
                "value_map": dict(zip(q["scale"]["labels"], q["scale"]["values"])),
                "min": q["scale"]["min"], "max": q["scale"]["max"],
            })
        if not items:
            continue
        entry = constructs.setdefault(construct, {"items": [], "scale_range": None})
        entry["items"].extend(items)
        if entry["scale_range"] is None and items:
            entry["scale_range"] = [items[0]["min"], items[0]["max"]]
    return {name: entry for name, entry in constructs.items() if len(entry["items"]) >= 2
            or True}  # 单题构念也输出分数，但记录告警


def score_dataframe(df: pd.DataFrame, constructs: dict[str, dict],
                    min_ratio: float = 0.67) -> tuple[pd.DataFrame, dict]:
    out = df.copy()
    log: dict[str, dict] = {}

    for construct, entry in constructs.items():
        items = entry["items"]
        numeric = pd.DataFrame(index=df.index)
        reversed_flags = []
        for item in items:
            raw = df.get(item["variable"])
            if raw is None:
                numeric[item["variable"]] = np.nan
                continue
            vals = pd.to_numeric(raw.map(item["value_map"]), errors="coerce")
            if item["reverse"]:
                vals = (item["min"] + item["max"]) - vals
                reversed_flags.append(item["variable"])
            numeric[item["variable"]] = vals

        answered_ratio = numeric.notna().sum(axis=1) / len(items)
        prorated = numeric.sum(axis=1) / numeric.notna().sum(axis=1)
        prorated[answered_ratio < min_ratio] = np.nan

        score_col = f"score_{construct}"
        out[score_col] = prorated.round(3)
        for item in items:
            out[f"{item['variable']}_num"] = numeric[item["variable"]]

        total_answered = int(answered_ratio[answered_ratio >= min_ratio].shape[0])
        log[construct] = {
            "score_column": score_col,
            "items": [{"variable": it["variable"], "label": it["label"],
                       "question_id": it["question_id"], "reverse": it["reverse"]}
                      for it in items],
            "reverse_items": reversed_flags,
            "scale_range": entry["scale_range"],
            "min_answered_ratio": min_ratio,
            "n_scored": total_answered,
            "n_below_ratio": int(len(df) - total_answered),
            "missing_before": {it["variable"]: int(df[it["variable"]].isna().sum())
                               for it in items if it["variable"] in df.columns},
        }
    return out, log


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft 量表计分")
    parser.add_argument("--in", dest="src", required=True, help="标准化数据 responses.csv")
    parser.add_argument("--survey", required=True, help="survey.json")
    parser.add_argument("--out-dir", dest="out_dir", default="outputs")
    parser.add_argument("--clean-dir", dest="clean_dir", default="data/clean")
    parser.add_argument("--min-answered-ratio", dest="min_ratio", type=float, default=0.67)
    args = parser.parse_args()

    df = pd.read_csv(args.src, encoding="utf-8-sig", dtype=str,
                     keep_default_na=False).replace("", pd.NA)
    survey = load_survey(args.survey)
    constructs = build_constructs(survey)
    scored, log = score_dataframe(df, constructs, args.min_ratio)

    out_dir = Path(args.out_dir)
    clean_dir = Path(args.clean_dir)
    clean_dir.mkdir(parents=True, exist_ok=True)
    scored_file = clean_dir / "responses_scored.csv"
    scored.to_csv(scored_file, index=False, encoding="utf-8-sig")
    log_file = save_json({"tool": "scale_score", "source": args.src,
                          "constructs": log}, out_dir / "scoring_log.json")

    print(json.dumps({"tool": "scale_score", "scored_file": str(scored_file),
                      "scoring_log": str(log_file),
                      "constructs": {k: {"score_column": v["score_column"],
                                         "n_items": len(v["items"]),
                                         "reverse_items": v["reverse_items"],
                                         "n_scored": v["n_scored"]}
                                     for k, v in log.items()}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
