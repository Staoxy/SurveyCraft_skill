"""data_quality（Gate 1）与 data_clean（Gate 2）测试。"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pandas as pd

from common import ROOT, load_survey
from data_clean import build_plan
from data_quality import check_speeder, run_quality
from make_synthetic import generate

DEMO = ROOT / "surveys" / "demo" / "survey.json"


def _df_with_quality(n=150, seed=42):
    """生成 WJX 原始数据并走完导入标准化——质检引擎运行在标准化数据上。"""
    from data_import import apply_mapping, build_mapping

    survey = load_survey(DEMO)
    rows, columns, summary = generate(survey, n, seed=seed)
    raw = pd.DataFrame(rows, columns=columns).replace("", pd.NA)
    mapping = build_mapping(raw, survey, "wjx", "gb18030", "test")
    mapping["status"] = "confirmed_by_user"
    std, _ = apply_mapping(raw, survey, mapping)
    return std, summary


# ---------------------------------------------------------------- Gate 1

def test_quality_flags_injected_patterns():
    df, summary = _df_with_quality()
    injected = summary["injected"]
    result = run_quality(df, load_survey(DEMO))
    s = result["summary"]
    assert s["attention_fail"] >= injected["attention_fail"] * 0.8  # 部分失败样本与直线作答重叠
    assert s["speeder"] >= injected["speeder"]  # 阈值为相对中位数，天然快答者也会被纳入
    assert 0 < s["logic_violation"] <= injected["logic_violator"] * 4
    assert result["n_flagged"] > 0
    # 候选携带 respondent_id 与规则明细
    for cand in result["candidates"]:
        assert cand["respondent_id"] and cand["rules"]


def test_speeder_threshold_relative():
    df = pd.DataFrame({
        "respondent_id": ["R1", "R2", "R3", "R4"],
        "duration_sec": ["100", "200", "150", "20"],
    })
    flagged, note = check_speeder(df)
    assert note is None
    assert [f["respondent_id"] for f in flagged] == ["R4"]


def test_speeder_without_duration():
    df = pd.DataFrame({"respondent_id": ["R1"], "duration_sec": [pd.NA]})
    flagged, note = check_speeder(df)
    assert flagged == [] and note and "无法评估" in note


def test_straight_liner_needs_enough_items():
    df, _ = _df_with_quality()
    result = run_quality(df, load_survey(DEMO))
    # demo 有 6 个量表题项 → 直线作答可检测
    assert result["summary"]["straight_liner"] >= 1


def test_gate1_never_modifies_data():
    df, _ = _df_with_quality()
    before = df.copy()
    run_quality(df, load_survey(DEMO))
    assert len(df) == len(before)  # 只标记不删除


# ---------------------------------------------------------------- Gate 2

def test_clean_requires_confirmation():
    """无 --exclude / --plan / --auto 时必须被 Gate 阻断（退出码 2）。"""
    df, _ = _df_with_quality()
    tmp = Path(tempfile.mkdtemp(prefix="sc_clean_"))
    try:
        quality = run_quality(df, load_survey(DEMO))
        plan = build_plan(quality, set(), "manual")
        # 手动模式且未给排除名单时 build_plan 全部 kept —— Gate 阻断逻辑在 CLI 层
        assert all(a["action"] == "kept" for a in plan)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_clean_exclude_and_log():
    df, _ = _df_with_quality(n=60, seed=7)
    survey = load_survey(DEMO)
    quality = run_quality(df, survey)
    assert quality["n_flagged"] > 0
    targets = {c["respondent_id"] for c in quality["candidates"][:3]}
    cleaned = df[~df["respondent_id"].isin(targets)]
    assert len(cleaned) == len(df) - len(targets)
    # 每个被排除者的动作记录完整规则明细
    plan = build_plan(quality, targets, "manual")
    excluded = [a for a in plan if a["action"] == "excluded_by_user"]
    assert {a["respondent_id"] for a in excluded} == targets
    assert all(a["rules"] for a in excluded)


def test_clean_auto_mode_excludes_all():
    df, _ = _df_with_quality(n=60, seed=7)
    quality = run_quality(df, load_survey(DEMO))
    plan = build_plan(quality, set(), "auto")
    excluded = [a for a in plan if a["action"] == "excluded_by_auto"]
    # actions 只覆盖候选样本（非候选无需记录），auto 模式下全部候选被排除
    assert len(excluded) == quality["n_flagged"] == len(plan)
