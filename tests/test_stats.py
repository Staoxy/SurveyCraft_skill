"""method_router 与 stats_run 的金标准 / 路由决策测试。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import tempfile

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

from method_router import route, var_level
from stats_run import (execute, run_crosstab, run_correlation, run_kruskal,
                       run_mann_whitney, run_reliability, run_ttest, _holm)

ROOT = Path(__file__).resolve().parent.parent
DEMO_SURVEY = ROOT / "surveys" / "demo" / "survey.json"


# ---------------------------------------------------------------- 金标准夹具

def _ttest_df():
    """手算金标准：g1=[4..8], g2=[6..10] → t = -2.0, df = 8, d = 1.0。"""
    return pd.DataFrame({
        "dv": [4, 5, 6, 7, 8, 6, 7, 8, 9, 10],
        "iv": ["A"] * 5 + ["B"] * 5,
    })


def test_ttest_golden():
    plan = {"method": "independent_t", "dv": {"variable": "dv"}, "iv": {"variable": "iv"}}
    res = run_ttest(_ttest_df(), plan)
    assert res["t"] == pytest.approx(-2.0, abs=1e-6)
    assert res["df"] == pytest.approx(8.0, abs=1e-6)
    assert res["p"] == pytest.approx(0.0805, abs=1e-3)
    # Cohen's d（合并标准差）：均值差 2 / 池化 SD 2.0 → d = 1.0
    assert res["effect_size"]["value"] == pytest.approx(1.2649, abs=1e-3)


def test_mann_whitney_consistent_with_scipy():
    df = pd.DataFrame({
        "dv": [1, 2, 3, 4, 10, 11, 12, 13],
        "iv": ["A"] * 4 + ["B"] * 4,
    })
    plan = {"dv": {"variable": "dv"}, "iv": {"variable": "iv"}}
    res = run_mann_whitney(df, plan)
    u, p = sps.mannwhitneyu(df.loc[:3, "dv"], df.loc[4:, "dv"])
    assert res["u"] == pytest.approx(float(min(u, 16 - u)), abs=0.1)
    assert res["p"] == pytest.approx(float(p), abs=5e-5)


def test_chi_square_golden():
    """经典 2×2 卡方：对角设计 → 完全独立时 chi2 已知；此处用恒等表验证。"""
    df = pd.DataFrame({
        "v1": ["男", "男", "女", "女"] * 10,
        "v2": ["使用", "没", "使用", "没"] * 10,   # 与 v1 完全独立（每格 10）
    })
    plan = {"v1": "v1", "v2": "v2", "posthoc": "std_residuals"}
    res = run_crosstab(df, plan)
    assert res["p"] == pytest.approx(1.0, abs=1e-9)   # 完全独立
    assert res["chi2"] == pytest.approx(0.0, abs=1e-9)
    assert res["effect_size"]["value"] == pytest.approx(0.0, abs=1e-9)


def test_chi_square_association():
    """强关联表：chi2 显著且 Cramér's V 接近 1。"""
    df = pd.DataFrame({
        "v1": ["A"] * 20 + ["B"] * 20,
        "v2": ["X"] * 20 + ["Y"] * 20,   # 完全对应
    })
    res = run_crosstab(df, {"v1": "v1", "v2": "v2"})
    assert res["p"] < 1e-6
    assert res["effect_size"]["value"] == pytest.approx(1.0, abs=1e-6)  # Cramér's V 完全关联


def test_correlation_golden():
    x = list(range(1, 11))
    df = pd.DataFrame({"x": x, "y": [3 * v + 1 for v in x]})
    plan = {"method": "pearson", "v1": {"variable": "x"}, "v2": {"variable": "y"}}
    res = run_correlation(df, plan)
    assert res["r"] == pytest.approx(1.0, abs=1e-9)
    assert res["p"] == pytest.approx(0.0, abs=1e-9)


def test_anova_golden():
    """三组均值 1/3/5，每组 n=5，组内方差 1 → F 很大。"""
    df = pd.DataFrame({
        "dv": [1.0, 1.5, 0.5, 1.0, 1.0,  3.0, 3.5, 2.5, 3.0, 3.0,  5.0, 5.5, 4.5, 5.0, 5.0],
        "iv": ["A"] * 5 + ["B"] * 5 + ["C"] * 5,
    })
    plan = {"dv": {"variable": "dv"}, "iv": {"variable": "iv"}}
    from stats_run import run_anova
    res = run_anova(df, plan, welch=False)
    # 组间 MS = 5*((1-3)^2+(3-3)^2+(5-3)^2)/2 *... 直接与 scipy F_oneway 对照
    F, p = sps.f_oneway(df.loc[:4, "dv"], df.loc[5:9, "dv"], df.loc[10:, "dv"])
    assert res["F"] == pytest.approx(float(F), abs=1e-3)
    assert res["p"] == pytest.approx(float(p), abs=5e-5)


def test_reliability_alpha_golden():
    """已知 α：题项完全平行（同方差同相关）时用公式核对手算值。"""
    rng = np.random.default_rng(7)
    true_score = rng.normal(0, 1, 200)
    items = pd.DataFrame({
        "i1_num": true_score * 2 + rng.normal(0, 1, 200),
        "i2_num": true_score * 2 + rng.normal(0, 1, 200),
        "i3_num": true_score * 2 + rng.normal(0, 1, 200),
    })
    plan = {"construct": "满意度", "item_columns": list(items.columns),
            "extra": ["kmo_bartlett"]}
    res = run_reliability(items, plan)
    # 方差比：题项方差 5，协方差 4 → α = 3*(4/5)/(1+2*4/5) = 2.4/2.6 ≈ 0.923
    assert res["alpha"] == pytest.approx(0.923, abs=0.05)
    assert res["n_items"] == 3
    assert len(res["item_stats"]) == 3
    assert "kmo" in res


def test_holm_correction():
    ps = [0.01, 0.04, 0.03]
    adj = _holm(ps)
    assert adj[0] == pytest.approx(0.03)      # 0.01*3
    assert adj[2] == pytest.approx(0.06)      # 0.03*2
    assert adj[1] == pytest.approx(0.06)      # 0.04*3 → 0.12? 用逐步最大值: rank1: .03*3=.09
    # 逐步规则：调整值单调不减且不超过 1
    assert adj == sorted(adj)


# ---------------------------------------------------------------- 路由决策

def _router_df():
    rng = np.random.default_rng(3)
    n = 120
    return pd.DataFrame({
        "grade": rng.choice(["大一", "大二", "大三", "大四"], n),
        "gender": rng.choice(["男", "女"], n),
        "used_ai": rng.choice(["使用过", "没有使用过"], n),
        "score_满意度": rng.normal(3.5, 0.8, n).round(3),
        "overall_satisfaction": rng.choice(["不同意", "一般", "同意", "非常同意"], n),
        "sat_quality_num": rng.normal(3.5, 0.8, n).round(3),
        "sat_speed_num": rng.normal(3.5, 0.8, n).round(3),
        "sat_ease_num": rng.normal(3.5, 0.8, n).round(3),
        "sat_richness_num": rng.normal(3.5, 0.8, n).round(3),
        "distraction_num": rng.normal(3.0, 0.8, n).round(3),
        "overall_satisfaction_num": rng.normal(3.5, 0.8, n).round(3),
    })


def test_var_level_mapping():
    from data_profile import build_specs
    survey = json.loads(Path(DEMO_SURVEY).read_text(encoding="utf-8"))
    df = _router_df()
    specs = build_specs(survey, df)
    assert var_level("score_满意度", specs) == "numeric"
    assert var_level("overall_satisfaction", specs) == "ordinal"
    assert var_level("gender", specs) == "categorical"


def test_router_numeric_two_groups():
    from data_profile import build_specs
    survey = json.loads(Path(DEMO_SURVEY).read_text(encoding="utf-8"))
    df = _router_df()
    df = df[df["gender"] == "男"].append(df[df["gender"] == "女"]) if hasattr(df, "append") else pd.concat([df])
    req = {"analyses": [{"id": "A1", "type": "group_compare",
                         "dv": "score_满意度", "iv": "gender", "why": "测试"}]}
    plan = route(df, survey, req)
    a = plan["analyses"][0]
    assert a["method"] in {"independent_t", "welch_t", "mann_whitney_u"}
    assert a["status"] == "proposed"
    assert a["assumption_results"]["min_n_per_group"] > 0


def test_router_ordinal_dv_prefers_nonparametric():
    from data_profile import build_specs
    survey = json.loads(Path(DEMO_SURVEY).read_text(encoding="utf-8"))
    df = _router_df()
    req = {"analyses": [{"id": "A1", "type": "group_compare",
                         "dv": "overall_satisfaction", "iv": "grade"}]}
    plan = route(df, survey, req)
    assert plan["analyses"][0]["method"] == "kruskal_wallis"
    assert plan["analyses"][0]["fallback"] == "welch_anova"


def test_router_crosstab_and_correlation():
    survey = json.loads(Path(DEMO_SURVEY).read_text(encoding="utf-8"))
    df = _router_df()
    req = {"analyses": [
        {"id": "A2", "type": "crosstab", "v1": "gender", "v2": "used_ai"},
        {"id": "A3", "type": "correlation", "v1": "overall_satisfaction", "v2": "score_满意度"},
        {"id": "A4", "type": "reliability", "construct": "满意度"},
    ]}
    plan = route(df, survey, req)
    a2, a3, a4 = plan["analyses"]
    assert a2["method"] == "chi_square" and a2["effect_size"] == "cramers_v"
    assert a3["method"] == "spearman"   # 序数+数值 → Spearman
    assert a4["method"] == "cronbach_alpha" and len(a4["items"]) == 6


def test_gate3_blocks_unconfirmed():
    """status=proposed 的分析不得执行。"""
    df = _ttest_df()
    plan = {"data": "memory", "analyses": [
        {"id": "A1", "method": "independent_t", "status": "proposed",
         "dv": {"variable": "dv"}, "iv": {"variable": "iv"}}]}
    summary = execute(df, plan, Path(tempfile.mkdtemp()))
    assert summary["executed"] == 0
    assert summary["skipped"][0]["id"] == "A1"


def test_gate3_confirmed_executes():
    df = _ttest_df()
    plan = {"data": "memory", "analyses": [
        {"id": "A1", "method": "independent_t", "status": "confirmed_by_user",
         "dv": {"variable": "dv"}, "iv": {"variable": "iv"},
         "why": "金标准 t 检验"}]}
    summary = execute(df, plan, Path(tempfile.mkdtemp()))
    assert summary["executed"] == 1
    assert summary["results"][0]["t"] == pytest.approx(-2.0, abs=1e-6)

