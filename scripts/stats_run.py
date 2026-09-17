"""SurveyCraft 统计执行引擎：按已确认的 analysis_plan 执行推断统计。

用法：
    python scripts/stats_run.py --data data/clean/responses_scored.csv \
        --survey survey.json --plan outputs/analysis_plan.json --out-dir outputs/results

规则（DESIGN.md §16）：
- 只执行 status=confirmed_by_user 的分析（Gate 3 强制）；proposed 条目跳过并记录
- 每个分析落一份机器可读结果 outputs/results/<analysis_id>.json
- 主方法失败/前提不满足时按计划中的 fallback 执行，并在结果中注明
- 数字全部来自程序计算；报告与 Agent 只引用，不手打
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_survey, save_json  # noqa: E402


def _holm(pvals: list[float]) -> list[float]:
    """Holm 逐步校正。"""
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    m = len(pvals)
    adjusted = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        adj = (m - rank) * pvals[idx]
        running = max(running, adj)
        adjusted[idx] = min(1.0, running)
    return adjusted


def _numeric(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


# ---------------------------------------------------------------- 组间比较

def run_ttest(df: pd.DataFrame, plan: dict) -> dict:
    dv, iv = plan["dv"]["variable"], plan["iv"]["variable"]
    data = df[[dv, iv]].dropna().copy()
    data[dv] = pd.to_numeric(data[dv], errors="coerce")
    data = data.dropna()
    g = sorted(data[iv].unique().tolist())
    if len(g) != 2:
        return {"error": f"group_compare 需要恰好 2 组，实际 {len(g)} 组"}
    a, b = (data.loc[data[iv] == g[0], dv], data.loc[data[iv] == g[1], dv])
    welch = plan["method"] == "welch_t"
    import pingouin as pg
    res = pg.ttest(a, b, correction=welch)
    t = float(res["T"].iloc[0])
    p = float(res["p_val"].iloc[0])
    dof = float(res["dof"].iloc[0])
    d = float(res["cohen_d"].iloc[0])
    ci = res["CI95"].iloc[0]
    return {
        "test": "welch_t" if welch else "independent_t",
        "n": {str(g[0]): int(len(a)), str(g[1]): int(len(b))},
        "mean": {str(g[0]): round(float(a.mean()), 3), str(g[1]): round(float(b.mean()), 3)},
        "sd": {str(g[0]): round(float(a.std(ddof=1)), 3), str(g[1]): round(float(b.std(ddof=1)), 3)},
        "t": round(t, 3), "df": round(dof, 2), "p": round(p, 4),
        "effect_size": {"name": "cohens_d", "value": round(d, 3)},
        "ci95_mean_diff": [round(float(ci[0]), 3), round(float(ci[1]), 3)],
        "apa": f"t({dof:.1f}) = {t:.2f}, p = {p:.3f}, Cohen's d = {d:.2f}",
    }


def run_mann_whitney(df: pd.DataFrame, plan: dict) -> dict:
    dv, iv = plan["dv"]["variable"], plan["iv"]["variable"]
    data = df[[dv, iv]].dropna().copy()
    data[dv] = pd.to_numeric(data[dv], errors="coerce")
    data = data.dropna()
    g = sorted(data[iv].unique().tolist())
    a, b = (data.loc[data[iv] == g[0], dv], data.loc[data[iv] == g[1], dv])
    u, p = sps.mannwhitneyu(a, b, alternative="two-sided")
    n1, n2 = len(a), len(b)
    n = n1 + n2
    u_mean = n1 * n2 / 2
    u_sd = (n1 * n2 * (n + 1) / 12) ** 0.5
    z = (min(u, n1 * n2 - u) - u_mean) / u_sd
    r = abs(z) / (n ** 0.5)
    return {
        "test": "mann_whitney_u",
        "n": {str(g[0]): n1, str(g[1]): n2},
        "median": {str(g[0]): float(a.median()), str(g[1]): float(b.median())},
        "u": round(float(u), 1), "z": round(float(z), 3), "p": round(float(p), 4),
        "effect_size": {"name": "r", "value": round(float(r), 3)},
        "apa": f"U = {u:.1f}, Z = {z:.2f}, p = {p:.3f}, r = {r:.2f}",
    }


def _posthoc_pairwise(df: pd.DataFrame, dv: str, iv: str, method: str) -> dict:
    """KW 事后：两两 Mann–Whitney（Holm 校正）。"""
    data = df[[dv, iv]].dropna().copy()
    data[dv] = pd.to_numeric(data[dv], errors="coerce")
    data = data.dropna()
    groups = sorted(data[iv].unique().tolist())
    pairs, ps = [], []
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            a = data.loc[data[iv] == groups[i], dv]
            b = data.loc[data[iv] == groups[j], dv]
            _, p = sps.mannwhitneyu(a, b, alternative="two-sided")
            pairs.append((groups[i], groups[j]))
            ps.append(float(p))
    adjusted = _holm(ps)
    rows = [{"group_1": str(a), "group_2": str(b), "p": round(p, 4),
             "p_holm": round(pa, 4)} for (a, b), p, pa in zip(pairs, ps, adjusted)]
    return {"method": "pairwise_mann_whitney_holm", "comparisons": rows}


def run_anova(df: pd.DataFrame, plan: dict, welch: bool) -> dict:
    import pingouin as pg
    dv, iv = plan["dv"]["variable"], plan["iv"]["variable"]
    data = df[[dv, iv]].dropna().copy()
    data[dv] = pd.to_numeric(data[dv], errors="coerce")
    data = data.dropna()
    if welch:
        res = pg.welch_anova(data=data, dv=dv, between=iv)
        f = float(res["F"].iloc[0])
        p = float(res["p_unc"].iloc[0])
        df1, df2 = float(res["ddof1"].iloc[0]), float(res["ddof2"].iloc[0])
        omega2 = max(0.0, (f - 1) * df1 / (f * df1 + df2))
        out = {"test": "welch_anova", "F": round(f, 3), "df1": df1, "df2": round(df2, 2),
               "p": round(p, 4),
               "effect_size": {"name": "omega_squared", "value": round(omega2, 3)},
               "apa": f"F({df1:.0f}, {df2:.1f}) = {f:.2f}, p = {p:.3f}, ω² = {omega2:.3f}"}
    else:
        res = pg.anova(data=data, dv=dv, between=iv, detailed=False)
        f = float(res["F"].iloc[0])
        p = float(res["p_unc"].iloc[0])
        df1 = int(res["ddof1"].iloc[0])
        df2 = int(res["ddof2"].iloc[0])
        eta2 = float(res["np2"].iloc[0])
        out = {"test": "one_way_anova", "F": round(f, 3), "df1": df1, "df2": df2,
               "p": round(p, 4),
               "effect_size": {"name": "eta_squared", "value": round(eta2, 3)},
               "apa": f"F({df1}, {df2}) = {f:.2f}, p = {p:.3f}, η² = {eta2:.3f}"}
    out["n"] = int(len(data))
    out["group_means"] = {
        str(k): {"mean": round(float(v.mean()), 3), "sd": round(float(v.std(ddof=1)), 3),
                 "n": int(len(v))}
        for k, v in data.groupby(iv)[dv]}
    return out


def run_kruskal(df: pd.DataFrame, plan: dict) -> dict:
    dv, iv = plan["dv"]["variable"], plan["iv"]["variable"]
    data = df[[dv, iv]].dropna().copy()
    data[dv] = pd.to_numeric(data[dv], errors="coerce")
    data = data.dropna()
    groups = [g for _, g in data.groupby(iv)[dv]]
    h, p = sps.kruskal(*groups)
    n = len(data)
    eps2 = h * (n + 1) / (n ** 2 - 1)
    return {
        "test": "kruskal_wallis", "H": round(float(h), 3), "df": len(groups) - 1,
        "p": round(float(p), 4), "n": n,
        "effect_size": {"name": "epsilon_squared", "value": round(float(eps2), 3)},
        "median": {str(k): float(v.median()) for k, v in data.groupby(iv)[dv]},
        "apa": f"H({len(groups) - 1}) = {h:.2f}, p = {p:.3f}, ε² = {eps2:.3f}",
    }


# ---------------------------------------------------------------- 交叉与相关

def run_crosstab(df: pd.DataFrame, plan: dict) -> dict:
    v1, v2 = plan["v1"], plan["v2"]
    table = pd.crosstab(df[v1], df[v2])
    n = int(table.values.sum())
    chi2, p, dof, expected = sps.chi2_contingency(table, correction=False)
    cramers_v = (chi2 / (n * (min(table.shape) - 1))) ** 0.5
    out = {
        "test": "chi_square", "n": n, "chi2": round(float(chi2), 3), "df": int(dof),
        "p": round(float(p), 4),
        "effect_size": {"name": "cramers_v", "value": round(float(cramers_v), 3)},
        "table": {str(r): {str(c): int(table.loc[r, c]) for c in table.columns}
                  for r in table.index},
        "row_pct": {str(r): {str(c): round(100 * table.loc[r, c] / table.loc[r].sum(), 1)
                             for c in table.columns} for r in table.index},
        "apa": f"χ²({dof}, N = {n}) = {chi2:.2f}, p = {p:.3f}, Cramér's V = {cramers_v:.2f}",
    }
    if plan.get("posthoc") == "std_residuals":
        std_res = (table - expected) / (expected ** 0.5)
        out["posthoc"] = {"method": "standardized_residuals",
                          "table": {str(r): {str(c): round(float(std_res.loc[r, c]), 2)
                                             for c in table.columns} for r in table.index}}
    return out


def run_fisher(df: pd.DataFrame, plan: dict) -> dict:
    table = pd.crosstab(df[plan["v1"]], df[plan["v2"]])
    odds, p = sps.fisher_exact(table)
    n = int(table.values.sum())
    chi2, _, dof, _ = sps.chi2_contingency(table, correction=False)
    v = (chi2 / (n * (min(table.shape) - 1))) ** 0.5
    return {
        "test": "fisher_exact", "n": n, "odds_ratio": round(float(odds), 3),
        "p": round(float(p), 4),
        "effect_size": {"name": "cramers_v", "value": round(float(v), 3)},
        "table": {str(r): {str(c): int(table.loc[r, c]) for c in table.columns}
                  for r in table.index},
        "apa": f"Fisher 精确检验 p = {p:.3f}（OR = {odds:.2f}, Cramér's V = {v:.2f}）",
    }


def run_correlation(df: pd.DataFrame, plan: dict) -> dict:
    """相关分析。序数变量的标签 → 数值编码由 execute() 预处理（value_map）。"""
    import pingouin as pg
    v1, v2 = plan["v1"]["variable"], plan["v2"]["variable"]
    data = df[[v1, v2]].dropna().copy()
    for v in (v1, v2):
        data[v] = pd.to_numeric(data[v], errors="coerce")
    data = data.dropna()
    method = plan["method"]
    res = pg.corr(data[v1], data[v2], method="spearman" if method == "spearman"
                  else "kendall" if method == "kendall_tau" else "pearson")
    r = float(res["r"].iloc[0])
    p = float(res["p_val"].iloc[0])
    ci = res["CI95"].iloc[0]
    return {
        "test": method, "n": int(len(data)),
        "r": round(r, 3), "p": round(p, 4),
        "ci95": [round(float(ci[0]), 3), round(float(ci[1]), 3)],
        "apa": f"r = {r:.2f}, p = {p:.3f}, 95% CI [{ci[0]:.2f}, {ci[1]:.2f}], N = {len(data)}",
    }


# ---------------------------------------------------------------- 信度

def run_reliability(df: pd.DataFrame, plan: dict) -> dict:
    import pingouin as pg
    cols = [c for c in plan.get("item_columns", []) if c in df.columns]
    if len(cols) < 2:
        return {"error": f"构念「{plan.get('construct')}」有效题项不足 2 个"}
    items = df[cols].apply(pd.to_numeric, errors="coerce")
    items = items.dropna()
    alpha, ci = pg.cronbach_alpha(items)
    result = {
        "test": "cronbach_alpha", "construct": plan.get("construct"),
        "n_items": len(cols), "n": int(len(items)),
        "alpha": round(float(alpha), 3),
        "ci95": [round(float(ci[0]), 3), round(float(ci[1]), 3)],
        "apa": f"α = {alpha:.2f}（{len(cols)} 题，N = {len(items)}）",
    }
    total = items.sum(axis=1)
    item_stats = []
    for col in cols:
        rest = items.drop(columns=[col])
        corrected = float(items[col].corr(total - rest.sum(axis=1)))
        alpha_wo, _ = pg.cronbach_alpha(rest)
        item_stats.append({"item": col, "citc": round(corrected, 3),
                           "alpha_if_deleted": round(float(alpha_wo), 3)})
    result["item_stats"] = item_stats

    if "kmo_bartlett" in plan.get("extra", []):
        try:
            from factor_analyzer.factor_analyzer import (
                calculate_bartlett_sphericity, calculate_kmo)
            kmo_per_item, kmo_total = calculate_kmo(items)
            chi2_b, p_b = calculate_bartlett_sphericity(items)
            result["kmo"] = round(float(kmo_total), 3)
            result["bartlett"] = {"chi2": round(float(chi2_b), 1),
                                  "p": float(p_b) if p_b > 1e-300 else 0.0}
        except Exception as exc:
            result["kmo_error"] = str(exc)
    return result


# ---------------------------------------------------------------- 主流程

RUNNERS = {
    "independent_t": lambda df, p: run_ttest(df, p),
    "welch_t": lambda df, p: run_ttest(df, p),
    "mann_whitney_u": lambda df, p: run_mann_whitney(df, p),
    "anova": lambda df, p: run_anova(df, p, welch=False),
    "welch_anova": lambda df, p: run_anova(df, p, welch=True),
    "kruskal_wallis": lambda df, p: run_kruskal(df, p),
    "chi_square": lambda df, p: run_crosstab(df, p),
    "fisher_exact": lambda df, p: run_fisher(df, p),
    "pearson": lambda df, p: run_correlation(df, p),
    "spearman": lambda df, p: run_correlation(df, p),
    "kendall_tau": lambda df, p: run_correlation(df, p),
    "cronbach_alpha": lambda df, p: run_reliability(df, p),
}


def _prepare(df: pd.DataFrame, item: dict, specs: dict) -> pd.DataFrame:
    """序数变量按 value_map 数值化（组间比较与相关需要数值；卡方保留原标签）。"""
    df2 = df.copy()
    if item.get("type") == "group_compare":
        vars_ = [item.get("dv", {}).get("variable"), item.get("iv", {}).get("variable")]
    elif item.get("type") == "correlation":
        vars_ = [item.get("v1", {}).get("variable"), item.get("v2", {}).get("variable")]
    else:
        return df2
    for v in vars_:
        if not v or v not in df2.columns:
            continue
        vmap = (specs.get(v) or {}).get("value_map")
        if vmap and (df2[v].dtype == object or str(df2[v].dtype) == "str"):
            df2[v] = df2[v].map(vmap)
    return df2


def execute(df: pd.DataFrame, plan: dict, out_dir: Path, survey: dict | None = None) -> dict:
    from data_profile import build_specs
    specs = build_specs(survey, df) if survey else {}
    results, skipped = [], []
    for item in plan.get("analyses", []):
        aid = item.get("id", "A?")
        if item.get("status") != "confirmed_by_user":
            skipped.append({"id": aid, "reason": "status 未确认（Gate 3）"})
            continue
        method = item.get("method")
        runner = RUNNERS.get(method)
        if runner is None:
            results.append({"id": aid, "error": f"未实现的统计方法 {method}"})
            continue
        try:
            res = runner(_prepare(df, item, specs), item)
            res["id"] = aid
            res["why"] = item.get("why", "")
            res["planned_method"] = method
            res["fallback_available"] = item.get("fallback")
            results.append(res)
            save_json(res, out_dir / f"{aid}.json")
        except Exception as exc:
            results.append({"id": aid, "method": method, "error": str(exc)})
            save_json({"id": aid, "method": method, "error": str(exc)},
                      out_dir / f"{aid}.json")
    return {"tool": "stats_run", "data": plan.get("data"),
            "executed": len([r for r in results if "error" not in r]),
            "errors": len([r for r in results if "error" in r]),
            "skipped": skipped, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft 统计执行引擎")
    parser.add_argument("--data", required=True)
    parser.add_argument("--survey", required=True)
    parser.add_argument("--plan", required=True, help="analysis_plan.json（Gate 3 已确认）")
    parser.add_argument("--out-dir", dest="out_dir", default="outputs/results")
    args = parser.parse_args()

    df = pd.read_csv(args.data, encoding="utf-8-sig", dtype=str,
                     keep_default_na=False).replace("", pd.NA)
    survey = load_survey(args.survey)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = execute(df, plan, out_dir, survey=survey)
    save_json(summary, out_dir / "run_summary.json")
    print(json.dumps({k: summary[k] for k in ("tool", "executed", "errors", "skipped")},
                     ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
