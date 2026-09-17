"""SurveyCraft 数据质量引擎（Gate 1，DESIGN.md §11）。

用法：
    python scripts/data_quality.py --in data/raw/responses.csv --survey survey.json \
        --out-dir outputs

只标记"疑似低质量样本"，**绝不删除**——删除由用户在 Gate 2（data_clean）确认后执行。

规则（阈值见 references/data-quality.md）：
- straight_liner   同一构念全部量表题项取值完全相同（≥5 个题项）
- speeder          duration_sec < 中位数时长 × 1/3（无时长数据则输出"无法评估"）
- attention_fail   attention_check 题未按题干指令作答
- logic_violation  visibleIf 条件不满足但目标题有作答
- duplicate        全部题目作答完全一致（无唯一 ID 时的保守策略，报告中注明局限）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    MATRIX_TYPES, load_survey, ordered_questions, save_json,
)
from make_synthetic import INSTRUCTED_RE  # noqa: E402  (复用同一指令解析约定)
from scale_score import build_constructs  # noqa: E402

META_COLS = {"respondent_id", "submitted_at", "duration_sec", "duration_raw", "ip"}
SPEEDER_FACTOR = 1 / 3
MIN_SCALE_ITEMS = 5


def _instructed_label(question: dict) -> str | None:
    m = INSTRUCTED_RE.search(question.get("text", ""))
    if m and m.group(1) in question["scale"]["labels"]:
        return m.group(1)
    return None


def check_straight_liner(df: pd.DataFrame, constructs: dict) -> list[dict]:
    """构念题项总数 ≥ MIN_SCALE_ITEMS 时，全部题项取值一致 → 标记。"""
    all_items = [it for entry in constructs.values() for it in entry["items"]]
    if len(all_items) < MIN_SCALE_ITEMS:
        return []
    cols = [it["variable"] for it in all_items if it["variable"] in df.columns]
    flagged = []
    for idx, row in df[cols].dropna(how="all").iterrows():
        values = row.dropna()
        if len(values) >= MIN_SCALE_ITEMS and values.nunique() == 1:
            flagged.append({"respondent_id": df.at[idx, "respondent_id"],
                            "detail": f"{len(values)} 个量表题项全部为「{values.iloc[0]}」"})
    return flagged


def check_speeder(df: pd.DataFrame) -> tuple[list[dict], str | None]:
    if "duration_sec" not in df.columns or df["duration_sec"].notna().sum() == 0:
        return [], "无作答时长数据，速答规则无法评估"
    durations = pd.to_numeric(df["duration_sec"], errors="coerce")
    median = float(durations.median())
    threshold = median * SPEEDER_FACTOR
    flagged = [{"respondent_id": df.at[idx, "respondent_id"],
                "detail": f"时长 {int(d)}s < 阈值 {threshold:.0f}s（中位数 {median:.0f}s × {SPEEDER_FACTOR:.2f}）"}
               for idx, d in durations.dropna().items() if d < threshold]
    return flagged, None


def check_attention(df: pd.DataFrame, survey: dict) -> tuple[list[dict], list[str]]:
    ordered, _ = ordered_questions(survey)
    flagged, notes = [], []
    for q in ordered:
        if not q.get("attention_check"):
            continue
        if q["type"] in MATRIX_TYPES:
            continue
        var = q.get("variable", q["id"].lower())
        if var not in df.columns:
            continue
        instructed = _instructed_label(q)
        if instructed is None:
            notes.append(f"注意力检查题 {q['id']} 题干无明确指令，无法评估")
            continue
        mask = df[var].notna() & (df[var] != instructed)
        for idx in df[mask].index:
            flagged.append({"respondent_id": df.at[idx, "respondent_id"],
                            "detail": f"{q['id']} 应选「{instructed}」，实际作答「{df.at[idx, var]}」"})
    return flagged, notes


def check_logic(df: pd.DataFrame, survey: dict) -> list[dict]:
    ordered, _ = ordered_questions(survey)
    q_index = {q["id"]: q for q in ordered}
    id_to_cols: dict[str, list[str]] = {}
    for q in ordered:
        cols: list[str] = []
        if q["type"] in MATRIX_TYPES:
            cols = [r["variable"] for r in q["rows"]]
        elif q["type"] == "multiple_choice":
            cols = [c for c in df.columns if c.startswith(f"{q['id']}_")]
        else:
            v = q.get("variable", q["id"].lower())
            cols = [v] if v in df.columns else []
        id_to_cols[q["id"]] = cols

    flagged: dict[str, dict] = {}
    for rule in survey.get("logic", []):
        if rule.get("type") != "visibleIf":
            continue
        cond, target = rule.get("condition", {}), rule.get("target")
        cond_q, op = cond.get("question"), cond.get("operator")
        if op not in {"in", "not_in"} or cond_q not in q_index or target not in q_index:
            continue
        cond_var = q_index[cond_q].get("variable", cond_q.lower())
        if cond_var not in df.columns:
            continue
        wanted = set(cond.get("value") or [])
        opt_map = {o["label"]: o["id"] for o in q_index[cond_q].get("options", [])}
        cond_ids = df[cond_var].map(lambda v: opt_map.get(v, v) if pd.notna(v) else None)
        met = cond_ids.map(lambda v: bool(v in wanted)) if op == "in" \
            else cond_ids.map(lambda v: (v is not None) and (v not in wanted))
        target_cols = [c for c in id_to_cols[target] if c in df.columns]
        if not target_cols:
            continue
        answered = df[target_cols].notna().any(axis=1)
        for idx in df[~met & answered].index:
            rid = df.at[idx, "respondent_id"]
            if rid not in flagged:
                flagged[rid] = {
                    "respondent_id": rid,
                    "detail": f"跳题条件不满足（{cond_q} 未按 {rule['id']} 所需选项作答）"
                              f"但仍作答了 {target}",
                    "rules": [rule["id"]]}
            else:
                flagged[rid]["rules"].append(rule["id"])
    return list(flagged.values())


def check_duplicate(df: pd.DataFrame, survey: dict) -> list[dict]:
    ordered, _ = ordered_questions(survey)
    cols = []
    for q in ordered:
        if q["type"] in MATRIX_TYPES:
            cols += [r["variable"] for r in q["rows"] if r["variable"] in df.columns]
        else:
            v = q.get("variable", q["id"].lower())
            if v in df.columns:
                cols.append(v)
    if not cols:
        return []
    dup_mask = df.duplicated(subset=cols, keep=False)
    groups = df[dup_mask].groupby(cols, dropna=False).groups if dup_mask.any() else {}
    flagged = []
    for _, idxs in groups.items():
        if len(idxs) < 2:
            continue
        rids = [df.at[i, "respondent_id"] for i in idxs]
        for rid in rids:
            flagged.append({"respondent_id": rid,
                            "detail": f"与样本 {'、'.join(r for r in rids if r != rid)} 全部题目作答完全一致"})
    return flagged


def run_quality(df: pd.DataFrame, survey: dict) -> dict:
    constructs = build_constructs(survey)
    rules: dict[str, dict] = {}

    sl = check_straight_liner(df, constructs)
    rules["straight_liner"] = {"candidates": sl}
    sp, note = check_speeder(df)
    rules["speeder"] = {"candidates": sp, "note": note}
    att, att_notes = check_attention(df, survey)
    rules["attention_fail"] = {"candidates": att, "note": att_notes or None}
    lv = check_logic(df, survey)
    rules["logic_violation"] = {"candidates": lv}
    dup = check_duplicate(df, survey)
    rules["duplicate"] = {
        "candidates": dup,
        "note": "基于全部题目作答完全一致的保守判断；如问卷含唯一标识题，请在映射确认后以 ID 去重"}

    all_cands = {}
    for rule, payload in rules.items():
        for c in payload["candidates"]:
            rid = c["respondent_id"]
            entry = all_cands.setdefault(rid, {"respondent_id": rid, "rules": []})
            entry["rules"].append({"rule": rule, "detail": c["detail"]})

    return {
        "tool": "data_quality",
        "n_respondents": len(df),
        "n_flagged": len(all_cands),
        "flagged_rate_pct": round(len(all_cands) / max(1, len(df)) * 100, 1),
        "summary": {rule: len(p["candidates"]) for rule, p in rules.items()},
        "rules": rules,
        "candidates": sorted(all_cands.values(), key=lambda c: c["respondent_id"]),
        "gate": "此报告为 Gate 1。清洗须由用户确认后在 data_clean 中执行（Gate 2），本工具绝不删除数据。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft 数据质量引擎（Gate 1）")
    parser.add_argument("--in", dest="src", required=True)
    parser.add_argument("--survey", required=True)
    parser.add_argument("--out-dir", dest="out_dir", default="outputs")
    args = parser.parse_args()

    df = pd.read_csv(args.src, encoding="utf-8-sig", dtype=str,
                     keep_default_na=False).replace("", pd.NA)
    survey = load_survey(args.survey)
    result = run_quality(df, survey)
    result["source"] = args.src
    save_json(result, Path(args.out_dir) / "quality_report.json")
    print(json.dumps({k: result[k] for k in ("tool", "n_respondents", "n_flagged",
                                             "flagged_rate_pct", "summary")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
