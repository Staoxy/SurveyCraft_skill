"""make_synthetic 合成数据生成器测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pandas as pd

from common import ROOT, load_survey
from make_synthetic import generate, write_csv

DEMO = ROOT / "surveys" / "demo" / "survey.json"


def _load_df(n: int = 150, seed: int = 42, rates: dict | None = None):
    survey = load_survey(DEMO)
    rows, columns, summary = generate(survey, n, seed=seed, rates=rates)
    df = pd.DataFrame(rows, columns=columns).replace("", pd.NA)  # 跳题空串 → NA
    return df, summary


def test_shape_and_columns():
    df, summary = _load_df()
    assert df.shape == (150, summary["columns"])
    assert df.shape[1] == 3 + 12 + 3  # 元数据3列 + 12题 + 矩阵4行额外3列(4行-1题)
    assert df.columns[0] == "提交答卷时间"
    assert df.columns[2] == "来自IP"


def test_wjx_header_style():
    df, _ = _load_df()
    assert any("您的性别是[单选题]" in c for c in df.columns)
    assert any("[矩阵量表题]." in c for c in df.columns)
    assert any("[多选题]" in c for c in df.columns)


def test_logic_respected():
    """零注入率下，Q04=没有使用过 的行，其后续题目必须全部为空（visibleIf 严格执行）。"""
    df, _ = _load_df(100, rates={"straight_liner": 0, "speeder": 0,
                                 "attention_fail": 0, "logic_violator": 0})
    q4 = df.columns[6]
    non_users = df[df[q4] == "没有使用过"]
    assert (non_users[df.columns[7]].isna()).all()
    assert (non_users[df.columns[8]].isna()).all()
    assert (non_users[df.columns[13]].isna()).all()
    assert (non_users[df.columns[15]].isna()).all()
    # 注意力检查题不受跳题控制，所有人均作答
    assert (df[df.columns[10]].notna()).all()


def test_logic_violators_injected():
    """注入的逻辑矛盾样本：Q04=没有使用过 但矩阵题有作答。"""
    df, _ = _load_df()
    q4 = df.columns[6]
    viol = df[(df[q4] == "没有使用过") & df[df.columns[13]].notna()]
    assert 0 < len(viol) <= 10


def test_attention_check_pattern():
    df, _ = _load_df()
    att = df.columns[10]
    counts = df[att].value_counts()
    assert counts.get("一般", 0) > 100  # 多数人按指令作答
    assert sum(v for k, v in counts.items() if k != "一般") >= 8  # 存在失败样本


def test_straight_liners_injected():
    df, _ = _load_df()
    att = df.columns[10]
    q10, q11 = df.columns[14], df.columns[15]
    straight = df[(df[q10] == df[q11]) & (df[q11] == df[att]) &
                  (df[df.columns[13]] == df[q10])]
    assert len(straight) >= 3


def test_speeders_injected():
    df, _ = _load_df()
    durations = df["所用时间"].str.extract(r"(?:(\d+)分)?(\d+)秒").fillna(0).astype(int)
    total_sec = durations[0] * 60 + durations[1]
    assert (total_sec < 50).sum() >= 3


def test_deterministic_with_seed():
    survey = load_survey(DEMO)
    r1, c1, _ = generate(survey, 50, seed=7)
    r2, c2, _ = generate(survey, 50, seed=7)
    assert c1 == c2
    assert [tuple(r.values()) for r in r1[:10]] == [tuple(r.values()) for r in r2[:10]]


def test_write_csv_encoding(tmp_path=None):
    """gb18030 写出后应能按 gb18030 读回（模拟问卷星 Excel 导出）。"""
    import tempfile

    survey = load_survey(DEMO)
    rows, columns, _ = generate(survey, 10, seed=1)
    tmp = Path(tempfile.mkdtemp(prefix="sc_syn_"))
    try:
        out = write_csv(rows, columns, tmp / "x.csv", "gb18030")
        df = pd.read_csv(out, encoding="gb18030")
        assert df.shape == (10, len(columns))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
