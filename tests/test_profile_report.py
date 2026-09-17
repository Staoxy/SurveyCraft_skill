"""data_profile / charts / report_build 的联合测试。"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pandas as pd

from common import ROOT, load_survey, save_json
from data_profile import profile, profile_variable, write_dictionary
from report_build import build_facts, build_report

DEMO = ROOT / "surveys" / "demo" / "survey.json"


def _demo_df():
    return pd.read_csv(ROOT / "surveys" / "demo" / "data" / "raw" / "responses.csv",
                       encoding="utf-8-sig", dtype=str, keep_default_na=False
                       ).replace("", pd.NA)


# ---------------------------------------------------------------- profile

def test_profile_counts():
    df = _demo_df()
    result = profile(df, load_survey(DEMO), source="test")
    assert result["summary"]["n_respondents"] == 150
    by_var = {v["variable"]: v for v in result["variables"]}
    # 跳题产生的结构性缺失 = 非用户数 − 注入的逻辑矛盾样本数（与原始数据一致）
    non_users = (df["used_ai"] == "没有使用过").sum()
    violators = ((df["used_ai"] == "没有使用过") & df["usage_frequency"].notna()).sum()
    assert by_var["usage_frequency"]["missing"] == non_users - violators
    # 量表题应带 value_map 与 scale_item
    sat = by_var["sat_quality"]
    assert sat["scale_item"] is True
    assert sat["value_map"]["非常满意"] == 5
    # 反向题标记透传
    assert by_var["distraction"]["reverse_scored"] is True
    # 注意力检查题标记透传
    assert by_var["attention_check_1"]["attention_check"] is True


def test_profile_frequencies_ordered_by_scale():
    df = _demo_df()
    result = profile(df, load_survey(DEMO))
    by_var = {v["variable"]: v for v in result["variables"]}
    labels = [f["label"] for f in by_var["overall_satisfaction"]["frequencies"]]
    assert labels == ["非常不同意", "不同意", "一般", "同意", "非常同意"]


def test_dictionary_csv():
    df = _demo_df()
    result = profile(df, load_survey(DEMO))
    tmp = Path(tempfile.mkdtemp(prefix="sc_dict_"))
    try:
        out = write_dictionary(result, tmp / "dictionary.csv")
        d = pd.read_csv(out, encoding="utf-8-sig")
        assert list(d.columns) == ["Variable", "Label", "Type", "Level",
                                   "Missing", "Unique", "Min", "Max", "Mean"]
        assert "gender" in set(d["Variable"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_profile_variable_inference_without_spec():
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], name="x")
    entry = profile_variable(s.astype(str), None)
    assert entry["type"] == "numeric"


# ---------------------------------------------------------------- charts（冒烟）

def test_charts_generate(tmp_path=None):
    import subprocess
    tmp = Path(tempfile.mkdtemp(prefix="sc_charts_"))
    try:
        subprocess.check_call([
            sys.executable, str(ROOT / "scripts" / "charts.py"),
            "--in", str(ROOT / "surveys" / "demo" / "data" / "raw" / "responses.csv"),
            "--profile", str(ROOT / "surveys" / "demo" / "outputs" / "profile.json"),
            "--out-dir", str(tmp / "charts")], stdout=subprocess.DEVNULL)
        pngs = list((tmp / "charts").glob("*.png"))
        csvs = list((tmp / "charts").glob("*.csv"))
        assert len(pngs) >= 10 and len(csvs) == len(pngs)  # 每图一张聚合 CSV（charts.json 为 .json）
        # 聚合 CSV 数字与 profile 一致（抽样 gender）
        agg = pd.read_csv(tmp / "charts" / "CH_gender.csv", encoding="utf-8-sig")
        prof = json.loads((ROOT / "surveys" / "demo" / "outputs" / "profile.json")
                          .read_text(encoding="utf-8"))
        by_var = {v["variable"]: v for v in prof["variables"]}
        expected = {f["label"]: f["n"] for f in by_var["gender"]["frequencies"]}
        for _, row in agg.iterrows():
            assert expected[row["label"]] == row["n"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- report

def _report_parts():
    profile = json.loads((ROOT / "surveys" / "demo" / "outputs" / "profile.json")
                         .read_text(encoding="utf-8"))
    survey = load_survey(DEMO)
    fake_chart = [{"title": "示例图", "png": "CH_gender.png", "csv": "CH_gender.csv"}]
    text = build_report(profile, survey, fake_chart, Path("."))
    return profile, text


def test_report_sections():
    _, text = _report_parts()
    for section in ("## 1. 调查概况", "## 2. 数据字典", "## 3. 数据质量概览",
                    "## 4. 描述统计", "## 6. 图表", "## 7. 核心发现",
                    "## 8. 核心发现解读", "## 9. 附录"):
        assert section in text, section


def test_report_facts_generated():
    profile, _ = _report_parts()
    facts = build_facts(profile)
    assert 5 <= len(facts) <= 12
    assert all(f["id"].startswith("F") for f in facts)


def test_report_numbers_traceable():
    """数字审计雏形：FACT 陈述中的百分数必须来自 profile 的频数表。"""
    profile, _ = _report_parts()
    facts = build_facts(profile)
    by_var = {v["variable"]: v for v in profile["variables"]}
    valid_pcts = set()
    for v in profile["variables"]:
        for f in v.get("frequencies", []):
            valid_pcts.add(f["pct"])
            valid_pcts.add(v.get("mean"))
    for fact in facts:
        nums = [float(x) for x in __import__("re").findall(r"(\d+(?:\.\d+)?)%", fact["statement"])]
        for num in nums:
            assert num in valid_pcts, f"{fact['id']} 中 {num}% 无法追溯"


def test_report_excludes_attention_from_facts():
    profile, text = _report_parts()
    facts = build_facts(profile)
    att_labels = [v["label"] for v in profile["variables"] if v.get("attention_check")]
    for fact in facts:
        for label in att_labels:
            assert label not in fact["statement"]
