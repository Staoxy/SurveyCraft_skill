"""SurveyCraft 数据清洗（Gate 2）。

用法：
    # 查看 Gate 1 候选后，用户给出清洗范围（二选一）：
    python scripts/data_clean.py --in data/raw/responses.csv \
        --quality outputs/quality_report.json --exclude R023,R087 --out-dir data/clean
    python scripts/data_clean.py --in data/raw/responses.csv \
        --quality outputs/quality_report.json --plan outputs/cleaning_plan.json --out-dir data/clean
    # auto 模式（排除全部候选，仅建议批量预览时使用）：
    python scripts/data_clean.py ... --mode auto

输出：data/clean/responses_clean.csv（清洗后数据）+ outputs/cleaning_log.json
（每个候选样本的规则、明细、最终动作，报告"数据清洗"章节由此生成）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_json  # noqa: E402


def build_plan(quality: dict, exclude: set[str], mode: str) -> list[dict]:
    actions = []
    for cand in quality.get("candidates", []):
        rid = cand["respondent_id"]
        if mode == "auto" or rid in exclude:
            actions.append({"respondent_id": rid, "rules": cand["rules"],
                            "action": "excluded_by_auto" if mode == "auto" else "excluded_by_user"})
        else:
            actions.append({"respondent_id": rid, "rules": cand["rules"], "action": "kept"})
    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft 数据清洗（Gate 2）")
    parser.add_argument("--in", dest="src", required=True)
    parser.add_argument("--quality", required=True, help="quality_report.json（Gate 1）")
    parser.add_argument("--exclude", help="逗号分隔的 respondent_id 列表")
    parser.add_argument("--plan", help="用户编辑过的 cleaning_plan.json")
    parser.add_argument("--mode", choices=["manual", "auto"], default="manual")
    parser.add_argument("--out-dir", dest="out_dir", default="data/clean")
    parser.add_argument("--log-dir", dest="log_dir", default="outputs")
    args = parser.parse_args()

    quality = json.loads(Path(args.quality).read_text(encoding="utf-8"))
    exclude = {r.strip() for r in args.exclude.split(",")} if args.exclude else set()

    if args.plan:
        plan_data = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        actions = plan_data["actions"]
        for a in actions:
            if a["action"].startswith("excluded"):
                exclude.add(a["respondent_id"])
        mode = plan_data.get("mode", args.mode)
    else:
        if not exclude and args.mode != "auto" and quality.get("n_flagged", 0) > 0:
            print(json.dumps({
                "gate_blocked": True,
                "message": f"Gate 2 需要用户确认清洗范围：共 {quality['n_flagged']} 个疑似样本。"
                           "请用 --exclude 指定排除名单、--plan 提供确认后的计划，或 --mode auto。",
                "candidates": [c["respondent_id"] for c in quality.get("candidates", [])],
            }, ensure_ascii=False, indent=2))
            return 2

    actions = build_plan(quality, exclude, args.mode)
    df = pd.read_csv(args.src, encoding="utf-8-sig", dtype=str,
                     keep_default_na=False).replace("", pd.NA)
    excluded_ids = {a["respondent_id"] for a in actions if a["action"].startswith("excluded")}
    before = len(df)
    cleaned = df[~df["respondent_id"].isin(excluded_ids)].reset_index(drop=True)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cleaned_file = out_dir / "responses_clean.csv"
    cleaned.to_csv(cleaned_file, index=False, encoding="utf-8-sig")

    log = {
        "tool": "data_clean",
        "date": date.today().isoformat(),
        "source_file": args.src,
        "quality_report": args.quality,
        "mode": args.mode,
        "policy": {"missing": "prorated_mean", "min_answered_ratio": 0.67,
                   "note": "缺失值处理策略与量表计分一致，见 scoring_log.json"},
        "n_before": before,
        "n_after": len(cleaned),
        "n_excluded": len(excluded_ids),
        "actions": actions,
    }
    log_file = save_json(log, Path(args.log_dir) / "cleaning_log.json")
    print(json.dumps({"tool": "data_clean", "cleaned_file": str(cleaned_file),
                      "cleaning_log": str(log_file),
                      "n_before": before, "n_after": len(cleaned),
                      "n_excluded": len(excluded_ids)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
