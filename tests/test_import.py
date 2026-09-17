"""data_import 导入引擎测试：编码探测、问卷星解析、映射确认两阶段、标准化落地。"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pandas as pd

from common import ROOT, load_survey
from data_import import (apply_mapping, build_mapping, detect_platform,
                         parse_duration, parse_header, read_table, run_import,
                         split_multi, split_other)
from make_synthetic import generate, write_csv

DEMO = ROOT / "surveys" / "demo" / "survey.json"


def _make_wjx_csv(n: int = 40, encoding: str = "gb18030",
                  rates: dict | None = None) -> Path:
    survey = load_survey(DEMO)
    rows, columns, _ = generate(survey, n, seed=99, rates=rates)
    tmp = Path(tempfile.mkdtemp(prefix="sc_import_"))
    out = write_csv(rows, columns, tmp / "responses.csv", encoding)
    return out


# ---------------------------------------------------------------- 单元

def test_parse_header():
    h = parse_header("9、请评价您对AI学习工具以下方面的满意程度[矩阵量表题].答案生成质量")
    assert h["qnum"] == 9 and h["qtype"] == "矩阵量表题" and h["sub"] == "答案生成质量"
    h2 = parse_header("1、您的性别是[单选题]")
    assert h2["qnum"] == 1 and h2["sub"] is None
    assert parse_header("普通列名") is None


def test_parse_duration():
    assert parse_duration("88秒") == 88.0
    assert parse_duration("2分3秒") == 123.0
    assert parse_duration("1:23") == 83.0
    assert parse_duration("") is None


def test_split_helpers():
    assert split_multi("网页版,手机App，微信小程序") == ["网页版", "手机App", "微信小程序"]
    assert split_other("其他：B站") == ("其他", "B站")
    assert split_other("其他") == ("其他", None)
    assert split_other("男") == ("男", None)


def test_detect_platform_and_encoding():
    path = _make_wjx_csv()
    try:
        df, enc = read_table(path)
        assert enc == "gb18030"
        assert detect_platform(df) == "wjx"
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


# ---------------------------------------------------------------- 两阶段导入

def _run_two_phase(n: int = 40):
    path = _make_wjx_csv(n)
    tmp = Path(tempfile.mkdtemp(prefix="sc_out_"))
    try:
        report1 = run_import(str(path), str(DEMO), None, str(tmp))
        mapping_file = tmp / "mapping.json"
        assert mapping_file.exists()
        mapping = json.loads(mapping_file.read_text(encoding="utf-8"))
        assert mapping["status"] == "proposed"
        assert report1.get("data_file") is None  # 未确认，不落数据
        report2 = run_import(str(path), str(DEMO), str(mapping_file), str(tmp))
        data_file = tmp / "raw" / "responses.csv"
        assert report2["mapping_status"] == "confirmed_by_user"
        assert data_file.exists()
        return report1, report2, pd.read_csv(data_file, encoding="utf-8-sig")
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


def test_two_phase_import():
    report1, report2, df = _run_two_phase()
    assert report1["matched"] >= 12  # 12 道题至少全部按题号匹配
    assert report2["unmatched_columns"] == []
    assert df["respondent_id"].iloc[0] == "R001"
    assert "duration_sec" in df.columns


def test_standardized_columns():
    _, _, df = _run_two_phase()
    # 单选题 → 变量名列
    assert "gender" in df.columns and "used_ai" in df.columns
    # 多选题 → 二值展开列 + other 列
    assert "Q06_A" in df.columns and "Q06_C" in df.columns
    assert "Q06_D_other" in df.columns
    # 矩阵题 → 行变量列
    assert "sat_quality" in df.columns and "sat_richness" in df.columns
    # 二值列只含 0/1
    assert set(df["Q06_A"].dropna().unique()) <= {0, 1}


def test_multi_expansion_correctness():
    """其他：xxx 应拆出 _other 文本且对应二值列=1。"""
    _, _, df = _run_two_phase(60)
    other_rows = df[df["Q06_D_other"].notna()]
    assert (other_rows["Q06_D"] == 1).all()


def test_logic_skip_preserved_as_missing():
    """零注入数据中，非用户在后续题目上应为缺失（导入不臆造数据）。"""
    path = _make_wjx_csv(40, rates={"straight_liner": 0, "speeder": 0,
                                    "attention_fail": 0, "logic_violator": 0})
    tmp = Path(tempfile.mkdtemp(prefix="sc_logic_"))
    try:
        report = run_import(str(path), str(DEMO), None, str(tmp))
        mapping_file = tmp / "mapping.json"
        run_import(str(path), str(DEMO), str(mapping_file), str(tmp))
        df = pd.read_csv(tmp / "raw" / "responses.csv", encoding="utf-8-sig")
        non_users = df[df["used_ai"] == "没有使用过"]
        assert non_users["usage_frequency"].isna().all()
        assert non_users["sat_quality"].isna().all()
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


def test_import_without_survey():
    """无 survey.json：generic 平台，按观测值展开，仍能出标准化数据。"""
    path = _make_wjx_csv(20)
    tmp = Path(tempfile.mkdtemp(prefix="sc_nosurvey_"))
    try:
        df_raw, _ = read_table(path)
        assert detect_platform(df_raw) == "wjx"
        mapping = build_mapping(df_raw, None, "generic", "gb18030", str(path))
        # generic 平台不解析题号：仅 3 个 meta 列可按名称匹配，题目列全部 unmatched
        assert mapping["matched"] == 3
        mapping["status"] = "confirmed_by_user"
        # 手工给 meta 列之外的列标记 unknown → apply 应忽略并警告
        df_std, warnings = apply_mapping(df_raw, None, mapping)
        assert any("未匹配" in w for w in warnings)
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


def test_generic_csv_import():
    """无平台标记的简单 CSV → generic 平台，可完成提案阶段。"""
    tmp = Path(tempfile.mkdtemp(prefix="sc_generic_"))
    try:
        pd.DataFrame({
            "name": ["张三", "李四"],
            "score": ["90", "80"],
        }).to_csv(tmp / "plain.csv", index=False, encoding="utf-8")
        report = run_import(str(tmp / "plain.csv"), None, None, str(tmp))
        assert report["platform"] == "generic"
        assert report["mapping_status"] == "proposed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
