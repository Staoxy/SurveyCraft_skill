"""regression.py 回归引擎测试（金标准 + 诊断行为）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import numpy as np
import pandas as pd
import pytest

from regression import prepare, run_linear, run_logistic, run_ordinal

RNG = np.random.default_rng(11)


def _linear_df():
    n = 200
    x1 = RNG.normal(0, 1, n)
    x2 = RNG.normal(0, 1, n)
    y = 2.0 * x1 - 1.5 * x2 + 1.0 + RNG.normal(0, 0.5, n)
    return pd.DataFrame({"x1": x1, "x2": x2, "y": y})


def test_linear_golden():
    df = _linear_df()
    req = {"id": "R1", "type": "linear", "dv": "y", "ivs": ["x1", "x2"]}
    res = run_linear(df, req, specs={})
    assert res["r2"] == pytest.approx(0.96, abs=0.03)  # 信噪比决定理论上限 ≈ 0.9615
    b_x1 = next(c for c in res["coefficients"] if c["term"] == "x1")
    assert b_x1["b"] == pytest.approx(2.0, abs=0.15)
    b_x2 = next(c for c in res["coefficients"] if c["term"] == "x2")
    assert b_x2["b"] == pytest.approx(-1.5, abs=0.15)


def test_linear_diagnostics_orthogonal_vif():
    """正交自变量的 VIF 应接近 1。"""
    df = _linear_df()
    res = run_linear(df, {"id": "R1", "type": "linear", "dv": "y",
                          "ivs": ["x1", "x2"]}, specs={})
    vif = res["diagnostics"]["vif"]
    assert vif["x1"] == pytest.approx(1.0, abs=0.15)
    assert vif["x2"] == pytest.approx(1.0, abs=0.15)
    assert 1.5 < res["diagnostics"]["durbin_watson"] < 2.5  # 独立噪声 → DW≈2


def test_linear_collinear_vif_high():
    x1 = RNG.normal(0, 1, 200)
    x2 = x1 * 0.999 + RNG.normal(0, 0.01, 200)  # 近似共线
    y = 2 * x1 + RNG.normal(0, 0.5, 200)
    df = pd.DataFrame({"x1": x1, "x2": x2, "y": y})
    res = run_linear(df, {"id": "R1", "type": "linear", "dv": "y",
                          "ivs": ["x1", "x2"]}, specs={})
    assert max(v for k, v in res["diagnostics"]["vif"].items() if v) > 50


def test_logistic_direction_and_or():
    """y 随 x 单调递增 → OR > 1 且分类准确率高。"""
    n = 200
    x = RNG.normal(0, 1, n)
    p = 1 / (1 + np.exp(-(2 * x)))
    y = (RNG.random(n) < p).astype(int)
    df = pd.DataFrame({"x": x, "y": [str(v) for v in y]})
    res = run_logistic(df, {"id": "R2", "type": "logistic", "dv": "y",
                            "ivs": ["x"]}, specs={})
    orx = next(c for c in res["coefficients"] if c["term"] == "x")["odds_ratio"]
    assert orx > 1.5
    assert res["accuracy"] > 0.7
    assert res["pseudo_r2_mcfadden"] > 0.1


def test_ordinal_smoke():
    n = 300
    x = RNG.normal(0, 1, n)
    latent = 1.2 * x + RNG.normal(0, 1, n)
    y = np.select([latent < -0.8, latent < 0.8], ["低", "中"], default="高")
    df = pd.DataFrame({"x": x, "y": y})
    specs = {"y": {"value_order": ["低", "中", "高"]}}  # 真实流程由 survey.json 提供
    res = run_ordinal(df, {"id": "R3", "type": "ordinal", "dv": "y",
                           "ivs": ["x"]}, specs=specs)
    assert res["test"] == "ordinal_logistic_regression"
    assert res["coefficients"][0]["b"] > 0.5  # 方向正确
    assert res["dv"]["levels"] == ["低", "中", "高"]


def test_prepare_value_map():
    df = pd.DataFrame({"grade": ["大一", "大二", "大三"]})
    specs = {"grade": {"value_map": {"大一": 1, "大二": 2, "大三": 3}}}
    out = prepare(df, ["grade"], specs)
    assert out["grade"].tolist() == [1, 2, 3]
