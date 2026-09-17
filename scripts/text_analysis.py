"""SurveyCraft 开放题分析（编码统计层，DESIGN.md §14）。

分工：codebook 与逐条编码由 Agent 完成（语义任务），本脚本做确定性统计与溯源。

流程：
1. Agent 生成 codebook.json（主题 → 定义）并对开放题答案逐条编码，
   产出 coding.csv（列：respondent_id, code[, quote]）
2. 本脚本核对编码覆盖率 → 统计各主题频数 → 挑选代表性原文
   → outputs/open_text_results.json

用法：
    python scripts/text_analysis.py --data data/clean/responses_scored.csv \
        --variable improvement_suggestion --coding outputs/open_text_coding.csv \
        --codebook outputs/codebook.json --out outputs/open_text_results.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_json  # noqa: E402


def analyze(df: pd.DataFrame, variable: str, coding: pd.DataFrame,
            codebook: dict | None) -> dict:
    if variable not in df.columns:
        return {"error": f"数据中不存在开放题变量 {variable}"}
    answers = df[["respondent_id", variable]].copy()
    answered = answers[answers[variable].notna() & (answers[variable].astype(str).str.strip() != "")]
    answered_ids = set(answered["respondent_id"])

    coding = coding.copy()
    coding["respondent_id"] = coding["respondent_id"].astype(str)
    unknown_ids = set(coding["respondent_id"]) - answered_ids
    coded_ids = set(coding["respondent_id"])

    text_by_id = dict(zip(answers["respondent_id"].astype(str), answers[variable].astype(str)))

    freq_rows, quotes = [], {}
    for code, group in coding.groupby("code"):
        n = int(group["respondent_id"].nunique())
        reps = []
        for rid in group["respondent_id"].unique():
            text = text_by_id.get(str(rid), "").strip()
            if len(text) >= 10:
                reps.append({"respondent_id": str(rid), "text": text})
            if len(reps) >= 3:
                break
        freq_rows.append({"code": str(code), "n": n})
        quotes[str(code)] = reps

    total_coded = len(coded_ids)
    n_answered = len(answered_ids)
    freq_rows.sort(key=lambda r: -r["n"])
    n_answered = max(n_answered, 1)
    for row in freq_rows:
        row["pct_of_coded"] = round(row["n"] / max(1, total_coded) * 100, 1)

    definitions = {}
    if codebook:
        for item in (codebook.get("codes") or []):
            definitions[str(item.get("code"))] = item.get("definition", "")

    return {
        "tool": "text_analysis",
        "variable": variable,
        "n_answered": n_answered,
        "n_coded": total_coded,
        "coverage_pct": round(total_coded / n_answered * 100, 1),
        "uncoded_answered_ids": sorted(answered_ids - coded_ids)[:20],
        "coding_referenced_unknown_ids": sorted(unknown_ids)[:20],
        "codes": [{"code": row["code"],
                   "definition": definitions.get(row["code"], ""),
                   "n": row["n"], "pct_of_coded": row["pct_of_coded"],
                   "representative": quotes.get(row["code"], [])}
                  for row in freq_rows],
        "traceability_note": "每个主题的原始答案可通过 coding.csv 的 respondent_id 回溯",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft 开放题编码统计")
    parser.add_argument("--data", required=True, help="含开放题列的数据（responses_scored.csv）")
    parser.add_argument("--variable", required=True, help="开放题变量名")
    parser.add_argument("--coding", required=True, help="Agent 产出的 coding.csv")
    parser.add_argument("--codebook", help="Agent 产出的 codebook.json（可选）")
    parser.add_argument("--out", default="outputs/open_text_results.json")
    args = parser.parse_args()

    df = pd.read_csv(args.data, encoding="utf-8-sig", dtype=str,
                     keep_default_na=False).replace("", pd.NA)
    coding = pd.read_csv(args.coding, encoding="utf-8-sig", dtype=str,
                         keep_default_na=False).replace("", pd.NA)
    codebook = json.loads(Path(args.codebook).read_text(encoding="utf-8")) \
        if args.codebook and Path(args.codebook).exists() else None

    result = analyze(df, args.variable, coding, codebook)
    result["source"] = args.data
    result["coding_file"] = args.coding
    save_json(result, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
