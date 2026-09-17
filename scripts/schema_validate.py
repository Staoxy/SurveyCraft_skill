"""SurveyCraft Schema 校验器。

用法：
    python scripts/schema_validate.py --in survey.json
    python scripts/schema_validate.py --in survey.json --quiet   # 仅退出码

执行两层校验：
1. JSON Schema 结构校验（schemas/survey.schema.json）
2. 交叉引用完整性（question id 唯一、section 引用存在、logic 引用存在）

输出：JSON 结果（stdout 或 --out）。退出码：0 通过，1 有 ERROR。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, SCHEMAS_DIR, assign_issue_ids, load_json, make_issue, save_json  # noqa: E402


def validate(survey_path: str | Path) -> dict:
    import jsonschema

    schema = load_json(SCHEMAS_DIR / "survey.schema.json")
    issues: list[dict] = []
    data: dict | None = None
    try:
        data = load_json(survey_path)
    except Exception as exc:
        issues.append(make_issue(
            "ERROR", "invalid_json", "rule",
            f"问卷文件无法解析为 JSON：{exc}", rule_id="JSON-001"))
        issues = assign_issue_ids(issues)
        return _result(survey_path, issues)

    if not isinstance(data, dict):
        issues.append(make_issue(
            "ERROR", "invalid_json", "rule",
            "问卷顶层必须是 JSON 对象", rule_id="JSON-002"))
        issues = assign_issue_ids(issues)
        return _result(survey_path, issues)

    validator = jsonschema.Draft7Validator(schema)
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        loc = ".".join(str(p) for p in err.absolute_path) or "(root)"
        issues.append(make_issue(
            "ERROR", "schema_violation", "rule",
            f"结构不符合 Schema [{loc}]：{err.message}", rule_id="SCH-001"))

    # 交叉引用完整性（JSON Schema 表达不了的部分）
    questions = data.get("questions", []) if isinstance(data.get("questions"), list) else []
    ids = [q.get("id") for q in questions if isinstance(q, dict)]
    dupes = sorted({qid for qid in ids if ids.count(qid) > 1})
    if dupes:
        issues.append(make_issue(
            "ERROR", "duplicate_question_id", "rule",
            f"题目 id 重复：{', '.join(map(str, dupes))}", rule_id="SCH-002"))

    qid_set = set(ids)
    for section in data.get("sections", []):
        for qid in section.get("question_ids", []):
            if qid not in qid_set:
                issues.append(make_issue(
                    "ERROR", "broken_section_ref", "rule",
                    f"Section「{section.get('id')}」引用了不存在的题目 {qid}",
                    rule_id="SCH-003", question_id=qid))

    qids_ordered = [q.get("id") for q in data.get("questions", [])]
    for rule in data.get("logic", []):
        if rule.get("target") and rule["target"] not in qid_set:
            issues.append(make_issue(
                "ERROR", "broken_logic_ref", "rule",
                f"逻辑 {rule.get('id')} 的 target 指向不存在的题目 {rule['target']}",
                rule_id="SCH-004", logic_id=rule.get("id")))
        cond = rule.get("condition", {})
        if cond.get("question") and cond["question"] not in qid_set:
            issues.append(make_issue(
                "ERROR", "broken_logic_ref", "rule",
                f"逻辑 {rule.get('id')} 的 condition 指向不存在的题目 {cond['question']}",
                rule_id="SCH-004", logic_id=rule.get("id")))

    issues = assign_issue_ids(issues)
    return _result(survey_path, issues)


def _result(survey_path: str | Path, issues: list[dict]) -> dict:
    errors = [i for i in issues if i["severity"] == "ERROR"]
    warnings = [i for i in issues if i["severity"] == "WARNING"]
    return {
        "tool": "schema_validate",
        "file": str(survey_path),
        "valid": not errors,
        "counts": {
            "error": len(errors),
            "warning": len(warnings),
            "info": len(issues) - len(errors) - len(warnings),
        },
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft Schema 校验器")
    parser.add_argument("--in", dest="src", required=True, help="survey.json 路径")
    parser.add_argument("--out", dest="out", help="结果 JSON 落盘路径")
    parser.add_argument("--quiet", action="store_true", help="不打印 stdout")
    args = parser.parse_args()

    result = validate(args.src)
    if args.out:
        save_json(result, args.out)
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
