"""text_analysis 开放题编码统计 与 result_audit 数字审计 测试。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pandas as pd

from common import ROOT
from result_audit import audit_report
from text_analysis import analyze

ROOT_P = Path(__file__).resolve().parent.parent


def _open_text_df():
    return pd.DataFrame({
        "respondent_id": ["R001", "R002", "R003", "R004"],
        "improvement_suggestion": [
            "希望答案能更准确一些，不要胡说八道",
            "希望免费额度能多一点",
            "界面再简洁一点就好了",
            pd.NA,
        ],
    })


def _coding():
    return pd.DataFrame({
        "respondent_id": ["R001", "R002", "R003"],
        "code": ["准确性", "价格", "易用性"],
    })


def test_text_analysis_frequencies_and_quotes():
    res = analyze(_open_text_df(), "improvement_suggestion", _coding(), None)
    assert res["n_answered"] == 3
    assert res["n_coded"] == 3
    assert res["coverage_pct"] == 100.0
    codes = {c["code"]: c for c in res["codes"]}
    assert codes["准确性"]["n"] == 1
    assert "胡说八道" in codes["准确性"]["representative"][0]["text"]


def test_text_analysis_traceability():
    """编码引用了未作答的 respondent_id 应被报告。"""
    coding = pd.DataFrame({
        "respondent_id": ["R001", "R999"],
        "code": ["准确性", "价格"],
    })
    res = analyze(_open_text_df(), "improvement_suggestion", coding, None)
    assert "R999" in res["coding_referenced_unknown_ids"]
    assert res["n_coded"] == 2


def test_text_analysis_codebook_definitions():
    codebook = {"codes": [{"code": "准确性", "definition": "答案质量问题"}]}
    res = analyze(_open_text_df(), "improvement_suggestion", _coding(), codebook)
    codes = {c["code"]: c for c in res["codes"]}
    assert codes["准确性"]["definition"] == "答案质量问题"


# ---------------------------------------------------------------- 数字审计

def _profile_stub():
    return {"summary": {"n_respondents": 150, "missing_rate_pct": 10.4},
            "variables": [
                {"variable": "gender", "frequencies": [
                    {"label": "男", "n": 80, "pct": 53.3},
                    {"label": "女", "n": 70, "pct": 46.7}]},
            ]}


def _results_stub():
    return [{"id": "A01", "test": "kruskal_wallis", "H": 2.11, "p": 0.716,
             "effect_size": {"name": "epsilon_squared", "value": 0.023},
             "apa": "H(4) = 2.11, p = 0.716"}]


def test_audit_passes_when_traceable():
    report = ("样本 150 人，缺失率 10.4%。\n"
              "- 男占 53.3%（80 人）\n"
              "- Kruskal-Wallis：H = 2.11，p = 0.716，ε² = 0.023\n")
    res = audit_report(report, _profile_stub(), _results_stub())
    assert res["passed"], res["untraceable"]


def test_audit_fails_on_untraceable_number():
    report = "- 样本中 73.9% 的同学表示满意。\n"  # 73.9 无处可溯
    res = audit_report(report, _profile_stub(), _results_stub())
    assert not res["passed"]
    assert res["untraceable"][0]["value"] == 73.9


def test_audit_p_lt_convention():
    """p < .001 特例：任一结果 p ≤ .001 即可追溯。"""
    report = "- 差异显著（p < .001）\n"
    results = [{"id": "A1", "p": 0.0004}]
    res = audit_report(report, _profile_stub(), results)
    assert res["passed"]


def test_audit_ignores_code_spans_and_years():
    report = "数据文件 `responses_2026.csv` 共 150 行。\n详见 [图表](outputs/charts/CH_x.png)。\n"
    res = audit_report(report, _profile_stub(), _results_stub())
    assert res["passed"]
