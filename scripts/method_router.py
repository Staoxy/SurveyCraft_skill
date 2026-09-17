"""SurveyCraft Method Router（决策表执行 → analysis_plan.json，Gate 3）。

用法：
    python scripts/method_router.py --data data/clean/responses_clean.csv \
        --survey survey.json --requests outputs/analysis_requests.json \
        --out outputs/analysis_plan.json

requests.json 由 Agent 与用户商定（每条含 why 与分析意图，方法留空）：

    {"analyses": [
      {"id": "A01", "why": "比较不同年级满意度", "type": "group_compare",
       "dv": "score_满意度", "iv": "grade"},
      {"id": "A02", "why": "性别与是否使用的关系", "type": "crosstab",
       "v1": "gender", "v2": "used_ai"},
      {"id": "A03", "why": "使用频率与满意度关系", "type": "correlation",
       "v1": "usage_frequency", "v2": "score_满意度"},
      {"id": "A04", "why": "满意度量表信度", "type": "reliability", "construct": "满意度"}
    ]}

Router 按 references/method-router.md 补全 method / fallback / posthoc / effect_size，
并执行前提预检（每组 n、Levene、偏度）。所有 method=status=proposed，
用户确认后 stats_run 才执行（Gate 3）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from scipy import stats as sps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_survey, save_json  # noqa: E402
from data_profile import build_specs  # noqa: E402

ORDINAL_TYPES = {"likert", "rating", "nps"}


def var_level(var: str, specs: dict) -> str:
    """把变量的测量层次归并为决策表用的三档：numeric / ordinal / categorical。"""
    if var.startswith("score_"):
        return "numeric"  # 量表维度分（scale_score 产出，数值型）
    spec = specs.get(var, {})
    level = spec.get("measurement_level") or spec.get("level")
    vtype = spec.get("type")
    if level in {"interval", "ratio"} or level == "scale":
        return "numeric"
    if level == "ordinal":
        # 量表分（数值化后）按数值处理
        if var.startswith("score_"):
            return "numeric"
        return "ordinal"
    if vtype == "numeric":
        return "numeric"
    return "categorical"


def _skew_ok(values: pd.Series) -> float | None:
    v = pd.to_numeric(values.dropna(), errors="coerce").dropna()
    if len(v) < 3:
        return None
    return float(v.skew())


def route_group_compare(req: dict, df: pd.DataFrame, specs: dict) -> dict:
    dv, iv = req["dv"], req["iv"]
    dv_level = var_level(dv, specs)
    groups = df[iv].dropna().unique().tolist()
    n_groups = len(groups)
    plan = {
        "id": req["id"], "why": req.get("why", ""), "type": "group_compare",
        "dv": {"variable": dv, "level": dv_level},
        "iv": {"variable": iv, "groups": n_groups,
               "group_names": [str(g) for g in groups]},
        "p_adjust": "holm",
        "status": "proposed",
    }

    group_series = [pd.to_numeric(df.loc[df[iv] == g, dv].dropna(), errors="coerce")
                    for g in groups]
    ns = [len(s) for s in group_series]
    plan["assumption_results"] = {
        "n_per_group": {str(g): int(len(s)) for g, s in zip(groups, group_series)},
        "min_n_per_group": min(ns) if ns else 0,
        "skew": {str(g): (round(_skew_ok(s), 2) if _skew_ok(s) is not None else None)
                 for g, s in zip(groups, group_series)},
        "levene_p": None, "normal_ok": None, "variance_ok": None,
    }

    if n_groups == 2 and all(ns):
        # Levene 预检
        try:
            _, levene_p = sps.levene(*[s for s in group_series if len(s) >= 3],
                                     center="median")
            plan["assumption_results"]["levene_p"] = round(float(levene_p), 4)
        except Exception:
            pass

    normal_ok = (min(ns) >= 30 if ns else False) and all(
        (s is None) or abs(s) <= 2 for s in plan["assumption_results"]["skew"].values())
    variance_ok = (plan["assumption_results"]["levene_p"] or 1.0) >= .05
    plan["assumption_results"]["normal_ok"] = normal_ok
    plan["assumption_results"]["variance_ok"] = variance_ok
    unequal_n = len(set(ns)) > 1

    if dv_level == "numeric":
        if n_groups == 2:
            if not normal_ok:
                plan.update({"method": "mann_whitney_u", "fallback": "welch_t",
                             "posthoc": None, "effect_size": "r"})
            elif variance_ok:
                plan.update({"method": "independent_t", "fallback": "welch_t",
                             "posthoc": None, "effect_size": "cohens_d"})
            else:
                plan.update({"method": "welch_t", "fallback": "mann_whitney_u",
                             "posthoc": None, "effect_size": "cohens_d"})
        else:
            if not normal_ok:
                plan.update({"method": "kruskal_wallis", "fallback": "welch_anova",
                             "posthoc": "dunn", "effect_size": "epsilon_squared"})
            elif variance_ok and not unequal_n:
                plan.update({"method": "anova", "fallback": "welch_anova",
                             "posthoc": "tukey", "effect_size": "eta_squared"})
            else:
                plan.update({"method": "welch_anova", "fallback": "kruskal_wallis",
                             "posthoc": "games_howell", "effect_size": "omega_squared"})
    else:  # ordinal DV
        if n_groups == 2:
            plan.update({"method": "mann_whitney_u", "fallback": "welch_t",
                         "posthoc": None, "effect_size": "r"})
        elif normal_ok and not unequal_n:
            plan.update({"method": "anova", "fallback": "kruskal_wallis",
                         "posthoc": "tukey", "effect_size": "eta_squared"})
        else:
            plan.update({"method": "kruskal_wallis", "fallback": "welch_anova",
                         "posthoc": "dunn", "effect_size": "epsilon_squared"})
    return plan


def route_crosstab(req: dict, df: pd.DataFrame, specs: dict) -> dict:
    v1, v2 = req["v1"], req["v2"]
    table = pd.crosstab(df[v1], df[v2])
    plan = {
        "id": req["id"], "why": req.get("why", ""), "type": "crosstab",
        "v1": v1, "v2": v2,
        "shape": list(table.shape), "n": int(table.values.sum()),
        "p_adjust": "holm", "status": "proposed",
    }
    try:
        chi2, _, _, expected = __import__("scipy").stats.chi2_contingency(table)
        low = float((expected < 5).mean())
        plan["assumption_results"] = {
            "min_expected": round(float(expected.min()), 2),
            "pct_expected_below_5": round(low * 100, 1),
        }
        if table.shape == (2, 2) and low > 0.2:
            plan.update({"method": "fisher_exact", "fallback": "chi_square",
                         "posthoc": None, "effect_size": "odds_ratio"})
        elif low > 0.2:
            plan.update({"method": "chi_square", "fallback": "merge_categories",
                         "posthoc": "std_residuals", "effect_size": "cramers_v",
                         "warning": "期望频数不足，建议先合并稀疏类别（须说明依据）"})
        else:
            plan.update({"method": "chi_square", "fallback": "fisher_exact" if table.shape == (2, 2) else None,
                         "posthoc": "std_residuals", "effect_size": "cramers_v"})
    except Exception as exc:
        plan.update({"method": "chi_square", "fallback": None, "posthoc": None,
                     "effect_size": "cramers_v", "warning": f"期望频数预检失败：{exc}"})
    return plan


def route_correlation(req: dict, df: pd.DataFrame, specs: dict) -> dict:
    v1, v2 = req["v1"], req["v2"]
    l1, l2 = var_level(v1, specs), var_level(v2, specs)
    if l1 == "numeric" and l2 == "numeric":
        method, fallback, note = "pearson", "spearman", None
    elif l1 == "numeric" or l2 == "numeric":
        method, fallback, note = "spearman", None, "数值+序数组合：Spearman（次序不变量）"
    else:
        method, fallback, note = "spearman", "kendall_tau", None
    n = int((df[v1].notna() & df[v2].notna()).sum())
    return {
        "id": req["id"], "why": req.get("why", ""), "type": "correlation",
        "v1": {"variable": v1, "level": l1}, "v2": {"variable": v2, "level": l2},
        "n_pairs": n,
        "method": method, "fallback": fallback, "posthoc": None,
        "effect_size": None, "note": note, "status": "proposed",
    }


def route_reliability(req: dict, df: pd.DataFrame, specs: dict, survey: dict) -> dict:
    from scale_score import build_constructs
    constructs = build_constructs(survey)
    name = req["construct"]
    entry = constructs.get(name)
    if not entry:
        return {"id": req["id"], "why": req.get("why", ""), "type": "reliability",
                "construct": name, "error": f"构念「{name}」不存在或没有量表题项",
                "status": "proposed"}
    items = [it["variable"] for it in entry["items"]]
    num_cols = [f"{v}_num" for v in items]
    return {
        "id": req["id"], "why": req.get("why", ""), "type": "reliability",
        "construct": name, "items": items, "item_columns": num_cols,
        "method": "cronbach_alpha", "posthoc": None, "effect_size": None,
        "extra": ["kmo_bartlett"],
        "status": "proposed",
    }


def route(df: pd.DataFrame, survey: dict, requests: dict) -> dict:
    specs = build_specs(survey, df)
    plans = []
    for req in requests.get("analyses", []):
        rtype = req.get("type")
        try:
            if rtype == "group_compare":
                plans.append(route_group_compare(req, df, specs))
            elif rtype == "crosstab":
                plans.append(route_crosstab(req, df, specs))
            elif rtype == "correlation":
                plans.append(route_correlation(req, df, specs))
            elif rtype == "reliability":
                plans.append(route_reliability(req, df, specs, survey))
            else:
                plans.append({"id": req.get("id"), "type": rtype,
                              "error": f"未知分析类型 {rtype}", "status": "proposed"})
        except Exception as exc:
            plans.append({"id": req.get("id"), "type": rtype,
                          "error": str(exc), "status": "proposed"})
    return {
        "tool": "method_router",
        "decision_table": "references/method-router.md",
        "n": len(df),
        "analyses": plans,
        "gate": "Gate 3：请用户确认每条分析的 method/fallback/posthoc；"
                "将 status 改为 confirmed_by_user 后才可由 stats_run 执行。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft Method Router（Gate 3）")
    parser.add_argument("--data", required=True, help="清洗后的数据（或标准化数据）")
    parser.add_argument("--survey", required=True)
    parser.add_argument("--requests", required=True, help="analysis_requests.json（Agent 与用户商定）")
    parser.add_argument("--out", default="outputs/analysis_plan.json")
    args = parser.parse_args()

    df = pd.read_csv(args.data, encoding="utf-8-sig", dtype=str,
                     keep_default_na=False).replace("", pd.NA)
    survey = load_survey(args.survey)
    requests = json.loads(Path(args.requests).read_text(encoding="utf-8"))
    plan = route(df, survey, requests)
    plan["data"] = args.data
    save_json(plan, args.out)
    print(json.dumps({k: plan[k] for k in ("tool", "n")}, ensure_ascii=False))
    print(f"分析计划已生成：{args.out}（{len(plan['analyses'])} 条分析，status=proposed）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
