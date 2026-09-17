"""scale_score 量表计分测试（含手算金标准）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import numpy as np
import pandas as pd

from common import load_survey
from scale_score import build_constructs, score_dataframe

DEMO = ROOT = Path(__file__).resolve().parent.parent
DEMO_SURVEY = DEMO / "surveys" / "demo" / "survey.json"

VALUE_MAP = {"非常不同意": 1, "不同意": 2, "一般": 3, "同意": 4, "非常同意": 5}


def _mini_survey():
    """5 题构念（其中 1 题反向），单题构念，注意力检查题。"""
    return {
        "survey": {"id": "T", "title": "t", "version": "1.0.0"},
        "sections": [{"id": "S", "title": "s", "question_ids": ["Q01", "Q02", "Q03", "Q04", "Q05", "Q06"]}],
        "questions": [
            {"id": "Q01", "type": "likert", "text": "a", "construct": "满意度", "variable": "it1",
             "scale": {"min": 1, "max": 5, "labels": list(VALUE_MAP), "values": [1, 2, 3, 4, 5]}},
            {"id": "Q02", "type": "likert", "text": "b", "construct": "满意度", "variable": "it2",
             "scale": {"min": 1, "max": 5, "labels": list(VALUE_MAP), "values": [1, 2, 3, 4, 5]}},
            {"id": "Q03", "type": "likert", "text": "c", "construct": "满意度", "variable": "it3",
             "scale": {"min": 1, "max": 5, "labels": list(VALUE_MAP), "values": [1, 2, 3, 4, 5]}},
            {"id": "Q04", "type": "likert", "text": "d", "construct": "满意度", "variable": "it4",
             "reverse_scored": True,
             "scale": {"min": 1, "max": 5, "labels": list(VALUE_MAP), "values": [1, 2, 3, 4, 5]}},
            {"id": "Q05", "type": "likert", "text": "e", "construct": "满意度", "variable": "it5",
             "scale": {"min": 1, "max": 5, "labels": list(VALUE_MAP), "values": [1, 2, 3, 4, 5]}},
            {"id": "Q06", "type": "likert", "text": "注意力", "construct": "质量控制",
             "variable": "att", "attention_check": True,
             "scale": {"min": 1, "max": 5, "labels": list(VALUE_MAP), "values": [1, 2, 3, 4, 5]}},
        ],
        "logic": [], "metadata": {},
    }


def _mini_df():
    # RA: 全答 [5,4,3,反向题2,5] → 反向题翻转为 4 → mean = (5+4+3+4+5)/5 = 4.2
    # RB: 只答 2/5 题（低于 0.67 阈值）→ NaN
    # RC: 答 4/5 题（0.8 ≥ 0.67）→ prorated mean
    return pd.DataFrame({
        "respondent_id": ["RA", "RB", "RC"],
        "it1": ["非常同意", "非常同意", "非常同意"],
        "it2": ["同意", "同意", "同意"],
        "it3": ["一般", pd.NA, "一般"],
        "it4": ["不同意", "不同意", pd.NA],       # 反向题：不同意=2 → 翻转为 4
        "it5": ["非常同意", pd.NA, "非常同意"],
        "att": ["一般", "一般", "一般"],
    })


def test_reverse_scoring_golden():
    df = _mini_df()
    constructs = build_constructs(_mini_survey())
    scored, log = score_dataframe(df, constructs, min_ratio=0.67)
    # RA 手算：(5 + 4 + 3 + (2→4) + 5) / 5 = 4.2
    assert scored.loc[0, "score_满意度"] == 4.2
    # RA 反向题数值列翻转：2 → 4
    assert scored.loc[0, "it4_num"] == 4
    # RB 仅答 2/5 → 低于 0.67 阈值 → NaN
    assert pd.isna(scored.loc[1, "score_满意度"])
    # RC 答 4/5 → prorated mean：(5+4+3+5)/4 = 4.25
    assert scored.loc[2, "score_满意度"] == 4.25


def test_attention_check_excluded():
    constructs = build_constructs(_mini_survey())
    assert "质量控制" not in constructs or all(
        it["variable"] != "att" for it in constructs.get("质量控制", {}).get("items", []))
    assert all(it["variable"] != "att" for it in constructs["满意度"]["items"])


def test_reverse_flag_logged():
    constructs = build_constructs(_mini_survey())
    _, log = score_dataframe(_mini_df(), constructs)
    assert log["满意度"]["reverse_items"] == ["it4"]


def test_demo_survey_constructs():
    """demo 问卷：满意度 = Q09 的 4 行 + Q10 + Q11（反向）。"""
    survey = load_survey(DEMO_SURVEY)
    constructs = build_constructs(survey)
    sat = constructs["满意度"]
    vars_ = [it["variable"] for it in sat["items"]]
    assert vars_ == ["sat_quality", "sat_speed", "sat_ease", "sat_richness",
                     "overall_satisfaction", "distraction"]
    assert sat["items"][-1]["reverse"] is True
