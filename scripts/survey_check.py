"""SurveyCraft 问卷结构化审查（规则引擎，确定性检查）。

用法：
    python scripts/survey_check.py --in survey.json [--out result.json]

职责分工（DESIGN.md §2.1 / §5）：
- 本脚本只做**结构检查**：选项、量表、逻辑、命名的确定性规则
- **语言检查**（双重问题、诱导、歧义）由 Agent 按 references/question-review.md 执行，
  结果以 source="agent" 写入同一 issue 列表

输出：{tool, file, summary:{error,warning,info}, issues:[...]}（issue 符合 schemas/issue.schema.json）
退出码：0 无 ERROR，1 有 ERROR。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    CHOICE_TYPES, MATRIX_TYPES, SCALED_TYPES, assign_issue_ids, load_survey,
    make_issue, option_by_id, ordered_questions, save_json, other_variable,
)

# ------------------------------------------------------------------ 区间解析（OPT-001）

RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-~～—–－]\s*(\d+(?:\.\d+)?)")
GE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:岁以上|以上|及以上)")
LE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:岁以下|以下|不满)")

CATCH_ALL_KEYWORDS = (
    "其他", "其它", "以上都没", "以上均不", "不确定", "不清楚", "没有", "无", "从不", "不使用",
    "更少", "及以上",
)
INSTRUCTION_KEYWORDS = ("请", "选择")


def _parse_interval(label: str) -> tuple[float, float, str] | None:
    """从选项文本解析数值区间，返回 (low, high, 风格)；无法解析返回 None。"""
    m = RANGE_RE.search(label)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return (min(lo, hi), max(lo, hi), "closed")
    m = GE_RE.search(label)
    if m:
        return (float(m.group(1)), float("inf"), "open")
    m = LE_RE.search(label)
    if m:
        return (float("-inf"), float(m.group(1)), "open")
    return None


# ------------------------------------------------------------------ 规则组

def check_options(q: dict, issues: list[dict]) -> None:
    opts = q.get("options", [])
    if not opts:
        return

    # NUM-001 选项 id 重复
    seen: dict[str, str] = {}
    for o in opts:
        if o["id"] in seen:
            issues.append(make_issue(
                "ERROR", "duplicate_option_id", "rule",
                f"{q['id']} 选项 id「{o['id']}」重复出现",
                rule_id="NUM-001", question_id=q["id"], option_ids=[o["id"]]))
        seen[o["id"]] = o["label"]

    # OPT-003 选项文本重复
    label_owner: dict[str, str] = {}
    for o in opts:
        if o["label"] in label_owner:
            issues.append(make_issue(
                "ERROR", "duplicate_option_label", "rule",
                f"{q['id']} 选项文本「{o['label']}」重复",
                rule_id="OPT-003", question_id=q["id"], option_ids=[o["id"]]))
        label_owner[o["label"]] = o["id"]

    # OPT-001 数值区间重叠
    intervals: list[tuple[float, float, str, str]] = []
    for o in opts:
        parsed = _parse_interval(o["label"])
        if parsed:
            intervals.append((parsed[0], parsed[1], parsed[2], o["id"]))
    for i in range(len(intervals)):
        for j in range(i + 1, len(intervals)):
            lo1, hi1, style1, id1 = intervals[i]
            lo2, hi2, style2, id2 = intervals[j]
            if lo1 <= hi2 and lo2 <= hi1:
                severity = "ERROR" if "closed" in (style1, style2) else "WARNING"
                issues.append(make_issue(
                    severity, "option_interval_overlap", "rule",
                    f"{q['id']} 选项「{seen.get(id1, id1)}」与「{seen.get(id2, id2)}」数值区间存在重叠",
                    rule_id="OPT-001", question_id=q["id"], option_ids=[id1, id2],
                    suggestion="调整区间边界，确保相邻区间不共享边界值（如 18-19 / 20-22）"))

    # OPT-002 穷尽性启发检查：非量表选择题建议有兜底项
    if q["type"] in {"single_choice", "multiple_choice"} and len(opts) >= 3:
        has_catch_all = any(kw in o["label"] for o in opts for kw in CATCH_ALL_KEYWORDS)
        if not has_catch_all:
            issues.append(make_issue(
                "WARNING", "option_not_exhaustive", "rule",
                f"{q['id']} 未发现「其他/无/不确定」类兜底选项，可能不满足穷尽性",
                rule_id="OPT-002", question_id=q["id"],
                suggestion="如受访者可能不属于任何已列选项，请增加兜底项（可带 other_text 填空）"))

    # OPT-004 other_text 用在非选择题上 / 多个 other_text
    others = [o for o in opts if o.get("other_text")]
    if q["type"] not in CHOICE_TYPES | {"single_choice", "multiple_choice"} and others:
        issues.append(make_issue(
            "ERROR", "other_text_invalid_type", "rule",
            f"{q['id']} 类型为 {q['type']}，选项 other_text 无意义",
            rule_id="OPT-004", question_id=q["id"]))
    if len(others) > 1:
        issues.append(make_issue(
            "WARNING", "multiple_other_options", "rule",
            f"{q['id']} 存在多个「其他」类选项，受访者会困惑",
            rule_id="OPT-004", question_id=q["id"]))

    # SCL-005 选择题选项缺编码值（后续分析需要）
    if q["type"] in {"single_choice", "multiple_choice"}:
        missing = [o["id"] for o in opts if o.get("value") is None]
        if missing:
            issues.append(make_issue(
                "WARNING", "option_missing_value", "rule",
                f"{q['id']} 选项缺少编码值 value：{', '.join(missing)}，导入后无法自动编码",
                rule_id="SCL-005", question_id=q["id"], option_ids=missing,
                suggestion="为每个选项补充 value（数值型编码）"))


def check_scale(q: dict, issues: list[dict]) -> None:
    if q["type"] in MATRIX_TYPES and "scale" not in q:
        issues.append(make_issue(
            "ERROR", "scale_missing", "rule",
            f"{q['id']} 矩阵题缺少量表定义", rule_id="SCL-001", question_id=q["id"]))
        return
    if "scale" not in q:
        return
    scale = q["scale"]
    labels, values = scale.get("labels", []), scale.get("values", [])
    if len(labels) != len(values):
        issues.append(make_issue(
            "ERROR", "scale_length_mismatch", "rule",
            f"{q['id']} 量表 labels（{len(labels)}）与 values（{len(values)}）数量不一致",
            rule_id="SCL-001", question_id=q["id"]))
        return
    expected = list(range(int(scale["min"]), int(scale["max"]) + 1))
    if values != expected:
        issues.append(make_issue(
            "ERROR", "scale_values_not_contiguous", "rule",
            f"{q['id']} 量表 values 应为 {scale['min']}..{scale['max']} 连续序列，实际 {values}",
            rule_id="SCL-001", question_id=q["id"]))
    if q["type"] == "nps" and (scale["min"], scale["max"]) != (0, 10):
        issues.append(make_issue(
            "WARNING", "nps_scale_nonstandard", "rule",
            f"{q['id']} NPS 题量表不是标准的 0-10", rule_id="SCL-001", question_id=q["id"]))


def check_attention(q: dict, issues: list[dict]) -> None:
    if not q.get("attention_check"):
        return
    if not any(kw in q["text"] for kw in INSTRUCTION_KEYWORDS):
        issues.append(make_issue(
            "INFO", "attention_check_unclear", "rule",
            f"{q['id']} 标记为注意力检查题，但题干缺少明确的作答指令（如\"请选择X\"）",
            rule_id="ATT-001", question_id=q["id"],
            suggestion="题干中明确告知应选择的选项，否则数据质量规则无法判定"))


def check_logic(survey: dict, issues: list[dict]) -> None:
    ordered, _ = ordered_questions(survey)
    order_pos = {q["id"]: i for i, q in enumerate(ordered)}
    q_index = {q["id"]: q for q in survey.get("questions", [])}
    deps: dict[str, set[str]] = {}
    for rule in survey.get("logic", []):
        target, cond = rule.get("target"), rule.get("condition", {})
        cond_q = cond.get("question")
        if target in q_index and cond_q in q_index:
            # LOG-002 逻辑向后引用
            if order_pos.get(target, -1) <= order_pos.get(cond_q, -1):
                issues.append(make_issue(
                    "WARNING", "logic_backward_reference", "rule",
                    f"逻辑 {rule['id']}：{target} 的显示条件引用了在其之后（或自身）的题目 {cond_q}，"
                    "逻辑通常应向前引用", rule_id="LOG-002", logic_id=rule["id"],
                    question_id=target))
            deps.setdefault(target, set()).add(cond_q)
            # LOG-001 条件选项必须存在
            if cond.get("operator") in {"in", "not_in"}:
                cond_opts = option_by_id(q_index[cond_q])
                bad = [v for v in (cond.get("value") or []) if v not in cond_opts]
                if bad:
                    issues.append(make_issue(
                        "ERROR", "logic_option_missing", "rule",
                        f"逻辑 {rule['id']} 条件引用了 {cond_q} 中不存在的选项 {bad}",
                        rule_id="LOG-001", logic_id=rule["id"], question_id=cond_q))
        # LOG-003 循环依赖
    def has_cycle(node: str, visiting: set[str]) -> bool:
        if node in visiting:
            return True
        if node not in deps:
            return False
        visiting = visiting | {node}
        return any(has_cycle(dep, visiting) for dep in deps[node])

    for target in deps:
        if has_cycle(target, set()):
            issues.append(make_issue(
                "ERROR", "logic_cycle", "rule",
                f"显示逻辑存在循环依赖，涉及 {target}",
                rule_id="LOG-003", question_id=target))


def check_variables(survey: dict, issues: list[dict]) -> None:
    """所有派生变量（题目 variable / 矩阵行变量 / other_text 派生列）不得重名。"""
    owner: dict[str, str] = {}
    for q in survey.get("questions", []):
        names: list[str] = []
        if q["type"] in MATRIX_TYPES:
            names = [row["variable"] for row in q.get("rows", [])]
        elif q["type"] != "textarea" or "variable" in q:
            if "variable" in q:
                names = [q["variable"]]
        for o in q.get("options", []):
            if o.get("other_text"):
                names.append(other_variable(q["id"], o["id"]))
        for name in names:
            if name in owner and owner[name] != q["id"]:
                issues.append(make_issue(
                    "ERROR", "variable_collision", "rule",
                    f"变量名「{name}」在 {owner[name]} 与 {q['id']} 中重复，导入数据将冲突",
                    rule_id="NUM-002", question_id=q["id"]))
            owner.setdefault(name, q["id"])


def check_demographics_position(ordered: list[dict], issues: list[dict]) -> None:
    """ORD-001 人口学位置（INFO 级建议）。"""
    demo_keywords = ("性别", "年龄", "年级", "专业", "月生活费", "收入")
    first_two = ordered[:2]
    if any(any(kw in q["text"] for kw in demo_keywords) for q in first_two):
        issues.append(make_issue(
            "INFO", "demographics_first", "rule",
            "人口学题目位于问卷开头。人口学后置通常能提高完成率；课程场景如需用人口学做筛选也可保留在前，请权衡",
            rule_id="ORD-001", question_id=first_two[0]["id"]))


# ------------------------------------------------------------------ 主流程

def check_survey(survey: dict, file: str = "(memory)") -> dict:
    issues: list[dict] = []
    ordered, _ = ordered_questions(survey)

    for q in ordered:
        check_options(q, issues)
        check_scale(q, issues)
        check_attention(q, issues)
        if q["type"] in SCALED_TYPES | MATRIX_TYPES and q.get("reverse_scored"):
            pass  # 方向一致性在 collect 后统一处理（SCL-002）
    _check_scale_directions(ordered, issues)
    check_logic(survey, issues)
    check_variables(survey, issues)
    check_demographics_position(ordered, issues)

    issues = assign_issue_ids(issues)
    errors = [i for i in issues if i["severity"] == "ERROR"]
    warnings = [i for i in issues if i["severity"] == "WARNING"]
    infos = [i for i in issues if i["severity"] == "INFO"]
    return {
        "tool": "survey_check",
        "file": str(file),
        "valid": not errors,
        "summary": {"error": len(errors), "warning": len(warnings), "info": len(infos)},
        "issues": issues,
    }


def _check_scale_directions(ordered: list[dict], issues: list[dict]) -> None:
    """SCL-002：同构量表（labels 集合相同的 Likert 题）方向应一致；反向题应标记 reverse_scored。"""
    from collections import Counter
    likerts = [q for q in ordered if q["type"] == "likert" and "scale" in q]
    groups: dict[tuple, list[dict]] = {}
    for q in likerts:
        key = tuple(sorted(q["scale"]["labels"]))
        groups.setdefault(key, []).append(q)
    for items in groups.values():
        if len(items) < 2:
            continue
        order_counts = Counter(tuple(q["scale"]["labels"]) for q in items)
        if len(order_counts) < 2:
            continue
        counts = sorted(order_counts.values(), reverse=True)
        majority_order, majority_count = order_counts.most_common(1)[0]
        if majority_count == counts[0] and len(counts) > 1 and counts[0] == counts[1]:
            ids = ", ".join(
                f"{q['id']}（{'已标记反向' if q.get('reverse_scored') else '未标记反向'}）" for q in items)
            issues.append(make_issue(
                "WARNING", "scale_direction_mixed", "rule",
                f"同组 Likert 题标签顺序不一致且无法确定主方向：{ids}。请确认每题方向并正确标记 reverse_scored，"
                "否则维度计分会把不同方向的题当作同向求和", rule_id="SCL-002"))
            continue
        for q in items:
            if tuple(q["scale"]["labels"]) != majority_order and not q.get("reverse_scored"):
                issues.append(make_issue(
                    "WARNING", "scale_direction_mixed", "rule",
                    f"{q['id']} 量表标签顺序与同组其他题相反，但未标记 reverse_scored: true，"
                    "维度计分时会被当作同向处理", rule_id="SCL-002", question_id=q["id"],
                    suggestion="确认方向后标记 reverse_scored，或统一标签顺序"))


def check(survey_path: str | Path) -> dict:
    return check_survey(load_survey(survey_path), file=str(survey_path))


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft 问卷结构化审查")
    parser.add_argument("--in", dest="src", required=True, help="survey.json 路径")
    parser.add_argument("--out", dest="out", help="结果 JSON 落盘路径（如 outputs/review_report.json）")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    result = check(args.src)
    if args.out:
        save_json(result, args.out)
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
