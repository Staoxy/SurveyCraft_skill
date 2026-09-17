"""schema_validate 与 survey_check 的规则级测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from common import ROOT, load_survey
from schema_validate import validate as schema_validate
from survey_check import _parse_interval, check as survey_check

DEMO = ROOT / "surveys" / "demo" / "survey.json"
BAD = ROOT / "tests" / "fixtures" / "bad_survey.json"


# ---------------------------------------------------------------- schema_validate

def test_demo_survey_passes_schema():
    result = schema_validate(DEMO)
    assert result["valid"], result["issues"]


def test_bad_survey_passes_schema_but_demo_ok():
    """坏夹具的目的是测 survey_check 规则，本身必须仍是合法 Schema。"""
    result = schema_validate(BAD)
    assert result["valid"], result["issues"]


def test_duplicate_question_id_detected():
    import copy
    data = __import__("json").loads(__import__("json").dumps(__import__("json").loads(
        Path(DEMO).read_text(encoding="utf-8"))))
    data["questions"].append(copy.deepcopy(data["questions"][0]))
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        __import__("json").dump(data, f, ensure_ascii=False)
        tmp = f.name
    try:
        result = schema_validate(tmp)
        assert not result["valid"]
        assert any(i["rule_id"] == "SCH-002" for i in result["issues"])
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------- survey_check 规则

@pytest.fixture(scope="module")
def bad_result():
    return survey_check(BAD)


def _categories_with_rule(result, rule_id):
    return [i for i in result["issues"] if i.get("rule_id") == rule_id]


def test_bad_survey_has_errors(bad_result):
    assert not bad_result["valid"]
    assert bad_result["summary"]["error"] >= 5


def test_interval_overlap_rule(bad_result):
    issues = _categories_with_rule(bad_result, "OPT-001")
    assert issues and all(i["severity"] == "ERROR" for i in issues)
    assert any("18-20" in i["message"] for i in issues)


def test_duplicate_option_label_rule(bad_result):
    assert _categories_with_rule(bad_result, "OPT-003")


def test_option_missing_value_rule(bad_result):
    assert _categories_with_rule(bad_result, "SCL-005")


def test_scale_length_mismatch_rule(bad_result):
    issues = _categories_with_rule(bad_result, "SCL-001")
    assert any(i["target"]["question_id"] == "Q02" for i in issues)


def test_matrix_missing_scale_rule():
    """矩阵题缺 scale 由 schema 层拦截；规则引擎对内存构造的对象同样能报 SCL-001。"""
    import copy

    import json
    survey = load_survey(DEMO)
    q09 = next(q for q in survey["questions"] if q["id"] == "Q09")
    q09.pop("scale")
    from survey_check import check_survey
    result = check_survey(survey)
    issues = [i for i in result["issues"]
              if i.get("rule_id") == "SCL-001" and i.get("target", {}).get("question_id") == "Q09"]
    assert issues and issues[0]["severity"] == "ERROR"


def test_scale_direction_mixed_rule(bad_result):
    """Q05 与 Q07 标签集合相同但顺序相反（平票），应产生整组方向不一致 WARNING。"""
    issues = _categories_with_rule(bad_result, "SCL-002")
    assert [i for i in issues if i["severity"] == "WARNING" and "Q05" in i["message"]], issues


def test_logic_option_missing_rule(bad_result):
    issues = _categories_with_rule(bad_result, "LOG-001")
    assert issues and issues[0]["severity"] == "ERROR"


def test_logic_cycle_rule(bad_result):
    assert _categories_with_rule(bad_result, "LOG-003")


def test_logic_backward_reference_rule(bad_result):
    assert _categories_with_rule(bad_result, "LOG-002")


def test_variable_collision_rule(bad_result):
    issues = _categories_with_rule(bad_result, "NUM-002")
    assert issues and "study_way" in issues[0]["message"]


def test_demographics_position_info(bad_result):
    issues = _categories_with_rule(bad_result, "ORD-001")
    assert issues and issues[0]["severity"] == "INFO"


def test_demo_survey_clean():
    """官方示例问卷不得有 ERROR / WARNING（INFO 可接受）。"""
    result = survey_check(DEMO)
    assert result["summary"]["error"] == 0, result["issues"]
    assert result["summary"]["warning"] == 0, result["issues"]


# ---------------------------------------------------------------- 区间解析单元测试

def test_parse_interval_closed():
    assert _parse_interval("18-20岁") == (18.0, 20.0, "closed")
    assert _parse_interval("1000～1500元") == (1000.0, 1500.0, "closed")


def test_parse_interval_open():
    assert _parse_interval("26及以上")[0] == 26.0 and _parse_interval("26及以上")[1] == float("inf")
    assert _parse_interval("20以下")[0] == float("-inf") and _parse_interval("20以下")[1] == 20.0


def test_parse_interval_none():
    assert _parse_interval("男") is None
    assert _parse_interval("每周几次") is None


def test_issue_ids_sequential():
    result = survey_check(BAD)
    ids = [i["id"] for i in result["issues"]]
    assert ids == [f"ISS-{n:03d}" for n in range(1, len(ids) + 1)]


def test_load_survey_normalization():
    survey = load_survey(DEMO)
    q = {x["id"]: x for x in survey["questions"]}
    assert q["Q01"]["required"] is True
    assert q["Q11"]["reverse_scored"] is True
    assert q["Q08"]["attention_check"] is True
    assert q["Q09"]["scale"]["values"] == [1, 2, 3, 4, 5]
