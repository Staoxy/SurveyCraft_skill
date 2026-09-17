"""SurveyCraft 回归引擎（M3：线性 / 二项 Logistic / 定序 Logistic + 诊断）。

用法：
    python scripts/regression.py --data data/clean/responses_scored.csv \
        --survey survey.json --requests outputs/regression_requests.json \
        --out-dir outputs/results

requests.json 示例：
    {"analyses": [
      {"id": "R01", "why": "渠道与频率对满意度的影响", "type": "linear",
       "dv": "score_满意度", "ivs": ["Q06_A", "usage_frequency"]},
      {"id": "R02", "why": "满意度预测是否推荐", "type": "logistic",
       "dv": "used_ai", "ivs": ["score_满意度"]},
      {"id": "R03", "why": "年级预测满意度序数", "type": "ordinal",
       "dv": "overall_satisfaction", "ivs": ["grade"]}
    ]}

序数/分类自变量按 schema 的 value_map 数值化；线性回归输出 VIF、Shapiro 残差检验、
Durbin–Watson、Breusch–Pagan；结果落盘 outputs/results/<id>.json。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_survey, save_json  # noqa: E402
from data_profile import build_specs  # noqa: E402


def prepare(df: pd.DataFrame, columns: list[str], specs: dict) -> pd.DataFrame:
    """把指定列按 value_map 数值化（保留数值列原样），返回子 DataFrame。"""
    out = pd.DataFrame(index=df.index)
    for col in columns:
        if col not in df.columns:
            continue
        s = df[col]
        vmap = (specs.get(col) or {}).get("value_map")
        if vmap:
            out[col] = pd.to_numeric(s.map(vmap), errors="coerce")
        else:
            out[col] = pd.to_numeric(s, errors="coerce")
    return out


def _coef_frame(model) -> list[dict]:
    summary = model.summary2().tables[1]
    rows = []
    for name, row in summary.iterrows():
        rows.append({
            "term": str(name),
            "b": round(float(row["Coef."]), 3),
            "se": round(float(row["Std.Err."]), 3),
            "ci95": [round(float(row["Coef."]) - 1.959964 * float(row["Std.Err."]), 3),
                     round(float(row["Coef."]) + 1.959964 * float(row["Std.Err."]), 3)],
            "z_or_t": round(float(row["z"]), 3) if "z" in row else round(float(row.get("t", np.nan)), 3),
            "p": round(float(row["P>|z|"]) if "P>|z|" in row else float(row.get("P>|t|", np.nan)), 4),
        })
    return rows


def _vif(X: pd.DataFrame) -> dict:
    """方差膨胀因子（含常数列除外）。"""
    from statsmodels.stats.outliers_influence import variance_inflation_factor
    import statsmodels.api as sm
    Xc = sm.add_constant(X)
    out = {}
    for i, col in enumerate(Xc.columns):
        if col == "const":
            continue
        try:
            out[col] = round(float(variance_inflation_factor(Xc.values, i)), 2)
        except Exception:
            out[col] = None
    return out


def run_linear(df: pd.DataFrame, req: dict, specs: dict) -> dict:
    import statsmodels.api as sm
    from statsmodels.stats.diagnostic import het_breuschpagan
    from statsmodels.stats.stattools import durbin_watson

    dv = req["dv"]
    ivs = [v for v in req.get("ivs", []) if v in df.columns]
    controls = [v for v in req.get("control", []) if v in df.columns]
    cols = [dv] + ivs + controls
    data = prepare(df, cols, specs).dropna()
    if len(data) < 10:
        return {"id": req["id"], "error": f"有效样本不足（n={len(data)}）"}

    X = sm.add_constant(data[ivs + controls])
    model = sm.OLS(data[dv], X).fit()
    residuals = model.resid
    shapiro_p = None
    if len(residuals) <= 5000:
        shapiro_p = round(float(sps_shapiro(residuals)[1]), 4)
    bp_stat, bp_p, _, _ = het_breuschpagan(residuals, X)
    coefs = _coef_frame(model)

    return {
        "id": req["id"], "why": req.get("why", ""), "test": "linear_regression",
        "dv": dv, "ivs": ivs + controls, "n": int(len(data)),
        "r2": round(float(model.rsquared), 3),
        "adj_r2": round(float(model.rsquared_adj), 3),
        "F": round(float(model.fvalue), 3),
        "df": [int(model.df_model), int(model.df_resid)],
        "p": round(float(model.f_pvalue), 4),
        "coefficients": coefs,
        "diagnostics": {
            "vif": _vif(data[ivs + controls]),
            "shapiro_residual_p": shapiro_p,
            "durbin_watson": round(float(durbin_watson(residuals)), 3),
            "breusch_pagan_p": round(float(bp_p), 4),
            "notes": ["VIF > 10 提示严重多重共线性；Durbin–Watson ≈ 2 为理想；"
                      "BP p < .05 提示异方差，建议稳健标准误"],
        },
        "apa": (f"R² = {model.rsquared:.2f}, Adjusted R² = {model.rsquared_adj:.2f}, "
                f"F({int(model.df_model)}, {int(model.df_resid)}) = {model.fvalue:.2f}, "
                f"p = {model.f_pvalue:.3f}"),
    }


def sps_shapiro(x):
    from scipy import stats as sps
    return sps.shapiro(x)


def run_logistic(df: pd.DataFrame, req: dict, specs: dict) -> dict:
    import statsmodels.api as sm

    dv = req["dv"]
    ivs = [v for v in req.get("ivs", []) if v in df.columns] + \
          [v for v in req.get("control", []) if v in df.columns]
    data = prepare(df, [dv] + ivs, specs).dropna()
    y = data[dv]
    # 二分类 DV：优先 value_map 两值；否则取观测水平的字典序两值
    vmap = (specs.get(dv) or {}).get("value_map") or {}
    if len(vmap) == 2:
        y = y.map(vmap)
    levels = sorted(y.dropna().unique().tolist())
    if len(levels) != 2:
        return {"id": req["id"], "error": f"logistic 需要 2 分类 DV，实际 {len(levels)} 水平"}
    y = (y == levels[1]).astype(int)
    X = sm.add_constant(data[ivs])
    model = sm.Logit(y, X).fit(disp=0)
    coefs = _coef_frame(model)
    for row in coefs:
        row["odds_ratio"] = round(float(np.exp(row["b"])), 3)
        row["ci95_or"] = [round(float(np.exp(row["ci95"][0])), 3),
                          round(float(np.exp(row["ci95"][1])), 3)]
    pred = (model.predict(X) >= 0.5).astype(int)
    llnull = sm.Logit(y, np.ones((len(y), 1))).fit(disp=0).llf
    pseudo_r2 = 1 - model.llf / llnull
    return {
        "id": req["id"], "why": req.get("why", ""), "test": "logistic_regression",
        "dv": {"variable": dv, "positive_level": str(levels[1])},
        "ivs": ivs, "n": int(len(data)),
        "llr_p": round(float(model.llr_pvalue), 4),
        "pseudo_r2_mcfadden": round(float(pseudo_r2), 3),
        "accuracy": round(float((pred == y).mean()), 3),
        "coefficients": coefs,
        "apa": (f"Logistic：McFadden pseudo-R² = {pseudo_r2:.2f}, "
                f"LLR χ²({int(model.df_model)}) = {model.llr:.2f}, p = {model.llr_pvalue:.3f}"),
    }


def run_ordinal(df: pd.DataFrame, req: dict, specs: dict) -> dict:
    """定序 Logistic：DV 保留有序标签（按 value_order 编码），仅 IV 数值化。"""
    from statsmodels.miscmodels.ordinal_model import OrderedModel

    dv = req["dv"]
    ivs = [v for v in req.get("ivs", []) if v in df.columns]
    data = prepare(df, ivs, specs)
    data[dv] = df[dv]
    data = data.dropna()
    vmap = (specs.get(dv) or {}).get("value_map") or {}
    order = (specs.get(dv) or {}).get("value_order")
    if order:
        levels = [l for l in order if l in set(data[dv].unique())]
    else:
        levels = sorted(data[dv].unique().tolist())
    if len(levels) < 3:
        return {"id": req["id"], "error": f"ordinal 需要至少 3 个有序水平，实际 {len(levels)}"}
    y = data[dv].map({l: i for i, l in enumerate(levels)})
    model = OrderedModel(y, data[ivs], distr="logit").fit(method="bfgs", disp=0)
    coefs = _coef_frame_ordered(model, ivs)
    return {
        "id": req["id"], "why": req.get("why", ""), "test": "ordinal_logistic_regression",
        "dv": {"variable": dv, "levels": levels},
        "ivs": ivs, "n": int(len(data)),
        "pseudo_r2_mcfadden": round(float(1 - model.llf / model.llnull), 3),
        "llr_p": round(float(model.llr_pvalue), 4),
        "coefficients": coefs,
        "apa": (f"定序 Logistic：McFadden pseudo-R² = "
                f"{1 - model.llf / model.llnull:.2f}, p = {model.llr_pvalue:.3f}"),
    }


def _coef_frame_ordered(model, ivs: list[str]) -> list[dict]:
    params = model.params
    conf = model.conf_int()
    pvals = model.pvalues
    bse = model.bse
    rows = []
    for name in ivs:
        if name not in params.index:
            continue
        rows.append({
            "term": str(name),
            "b": round(float(params[name]), 3),
            "se": round(float(bse[name]), 3),
            "ci95": [round(float(conf.loc[name, 0]), 3), round(float(conf.loc[name, 1]), 3)],
            "odds_ratio": round(float(np.exp(params[name])), 3),
            "p": round(float(pvals[name]), 4),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft 回归引擎")
    parser.add_argument("--data", required=True)
    parser.add_argument("--survey", required=True)
    parser.add_argument("--requests", required=True)
    parser.add_argument("--out-dir", dest="out_dir", default="outputs/results")
    args = parser.parse_args()

    df = pd.read_csv(args.data, encoding="utf-8-sig", dtype=str,
                     keep_default_na=False).replace("", pd.NA)
    survey = load_survey(args.survey)
    specs = build_specs(survey, df)
    requests = json.loads(Path(args.requests).read_text(encoding="utf-8"))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    executed, errors = 0, 0
    for req in requests.get("analyses", []):
        rtype = req.get("type")
        try:
            if rtype == "linear":
                res = run_linear(df, req, specs)
            elif rtype == "logistic":
                res = run_logistic(df, req, specs)
            elif rtype == "ordinal":
                res = run_ordinal(df, req, specs)
            else:
                res = {"id": req.get("id"), "error": f"未知回归类型 {rtype}"}
        except Exception as exc:
            res = {"id": req.get("id"), "error": str(exc)}
        save_json(res, out_dir / f"{req.get('id', 'R?')}.json")
        if "error" in res:
            errors += 1
        else:
            executed += 1
    print(json.dumps({"tool": "regression", "executed": executed, "errors": errors},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
