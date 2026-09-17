"""SurveyCraft 问卷导出引擎（M1：问卷星文本导入格式 + Word）。

用法：
    python scripts/survey_export.py --in survey.json --format wjx-text --out survey_wjx.txt
    python scripts/survey_export.py --in survey.json --format word --out survey.docx
    python scripts/survey_export.py --in survey.json --format all --outdir exports/

设计要点（DESIGN.md §7 / §9）：
- 导出前必须已通过 schema 校验（本脚本不重复校验，只信任输入）
- 文本格式无法表达的内容（跳题逻辑、选答标记、矩阵量表标签、other_text 填空）
  一律输出 WARNING 清单，提示在平台上人工配置——绝不允许静默丢失
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_survey, ordered_questions, save_text  # noqa: E402

WJX_TYPE_MARKER = {
    "single_choice": "[单选题]",
    "yes_no": "[单选题]",
    "multiple_choice": "[多选题]",
    "text": "[填空题]",
    "textarea": "[填空题]",
    "number": "[填空题]",
    "integer": "[填空题]",
    "decimal": "[填空题]",
    "date": "[填空题]",
    "time": "[填空题]",
    "likert": "[量表题]",
    "rating": "[量表题]",
    "nps": "[量表题]",
    "matrix_single": "[矩阵量表题]",
    "matrix_multiple": "[矩阵多选题]",
    "ranking": "[排序题]",
}

# 文本导入格式无法完整表达、需要在平台人工确认的题型
WJX_TYPE_NOTE = {
    "number": "数字约束（min/max）无法表达，请在平台设置数字校验",
    "integer": "数字约束（min/max）无法表达，请在平台设置数字校验",
    "decimal": "数字约束（min/max）无法表达，请在平台设置数字校验",
    "date": "已导出为填空题，请在平台改为日期题型",
    "time": "已导出为填空题，请在平台改为时间题型",
    "nps": "NPS 量表锚点标签请在平台手动配置",
}


def _describe_condition(survey: dict, rule: dict) -> str:
    q_index = {q["id"]: q for q in survey.get("questions", [])}
    cond = rule.get("condition", {})
    q = q_index.get(cond.get("question"), {})
    opt_map = {o["id"]: o["label"] for o in q.get("options", [])}
    if cond.get("operator") in {"in", "not_in"}:
        labels = "、".join(opt_map.get(v, str(v)) for v in (cond.get("value") or []))
        op = "选中" if cond["operator"] == "in" else "未选中"
        return f"{cond['question']} {op}「{labels}」"
    return f"{cond['question']} {cond.get('operator', '')} {cond.get('value', '')}"


def export_wjx_text(survey: dict) -> tuple[str, list[str]]:
    """返回 (文本内容, 警告清单)。"""
    ordered, _ = ordered_questions(survey)
    warnings: list[str] = []
    lines: list[str] = [survey["survey"]["title"]]
    meta = survey.get("survey", {})
    if meta.get("intro"):
        lines.append(meta["intro"])
    lines.append("")

    for no, q in enumerate(ordered, start=1):
        marker = WJX_TYPE_MARKER[q["type"]]
        required_note = "" if q.get("required", True) else ""
        if not q.get("required", True):
            warnings.append(
                f"Q{no}（{q['id']}）为选答题，文本格式无法标记，请在平台手动设为「选答」")
        lines.append(f"{no}、{q['text']}{marker}{required_note}")

        if q["type"] in {"single_choice", "yes_no", "multiple_choice", "ranking"}:
            for o in q.get("options", []):
                lines.append(o["label"])
                if o.get("other_text"):
                    warnings.append(
                        f"Q{no}（{q['id']}）选项「{o['label']}」需在平台勾选「允许填空」")
        elif q["type"] in {"likert", "rating"}:
            lines.extend(q["scale"]["labels"])
        elif q["type"] == "nps":
            lo, hi = int(q["scale"]["min"]), int(q["scale"]["max"])
            lines.extend(str(v) for v in range(lo, hi + 1))
        elif q["type"] in {"matrix_single", "matrix_multiple"}:
            lines.extend(row["text"] for row in q["rows"])
            warnings.append(
                f"Q{no}（{q['id']}）为矩阵题，量表标签（{'/'.join(q['scale']['labels'])}）"
                "请在平台「矩阵题设置」中配置")
        elif q["type"] in {"text", "textarea"}:
            lines.append("_________")
        if q["type"] in WJX_TYPE_NOTE:
            warnings.append(f"Q{no}（{q['id']}）：{WJX_TYPE_NOTE[q['type']]}")
        lines.append("")

    for rule in survey.get("logic", []):
        warnings.append(
            f"逻辑 {rule['id']}（{rule['type']}：{rule['target']} ← "
            f"{_describe_condition(survey, rule)}）无法在文本导入格式中表达，"
            "请在问卷星平台「逻辑设置」中手动配置")

    return "\n".join(lines).rstrip() + "\n", warnings


# ---------------------------------------------------------------- Word

def _set_cjk(style, ascii_font: str, cjk_font: str) -> None:
    from docx.oxml.ns import qn
    style.font.name = ascii_font
    if style.element.rPr is not None and style.element.rPr.rFonts is not None:
        style.element.rPr.rFonts.set(qn("w:eastAsia"), cjk_font)


def export_word(survey: dict, out_path: str | Path) -> list[str]:
    """纸质/审阅用 Word 问卷。返回警告清单。"""
    from docx import Document
    from docx.shared import Pt

    ordered, _ = ordered_questions(survey)
    warnings: list[str] = []
    doc = Document()
    _set_cjk(doc.styles["Normal"], "Times New Roman", "宋体")
    doc.styles["Normal"].font.size = Pt(11)
    for h in ("Title", "Heading 1", "Heading 2"):
        _set_cjk(doc.styles[h], "Arial", "黑体")

    doc.add_heading(survey["survey"]["title"], level=0)
    meta = survey.get("survey", {})
    if meta.get("intro"):
        doc.add_paragraph(meta["intro"])

    current_section = None
    for no, q in enumerate(ordered, start=1):
        section = next((s for s in survey["sections"] if q["id"] in s["question_ids"]), None)
        if section and section["id"] != current_section:
            current_section = section["id"]
            doc.add_heading(section["title"], level=1)
        req = "" if q.get("required", True) else "（选答）"
        doc.add_heading(f"{no}. {q['text']}{req}", level=2)

        if q["type"] in {"single_choice", "yes_no", "multiple_choice", "ranking"}:
            for o in q.get("options", []):
                mark = "□" if q["type"] in {"single_choice", "yes_no", "multiple_choice"} else "（  ）"
                suffix = "_________" if o.get("other_text") else ""
                doc.add_paragraph(f"{mark} {o['label']}{suffix}")
        elif q["type"] in {"likert", "rating", "nps"}:
            labels = q["scale"]["labels"]
            table = doc.add_table(rows=2, cols=len(labels))
            table.style = "Table Grid"
            for c, label in enumerate(labels):
                table.rows[0].cells[c].text = label
        elif q["type"] in {"matrix_single", "matrix_multiple"}:
            labels = q["scale"]["labels"]
            table = doc.add_table(rows=len(q["rows"]) + 1, cols=len(labels) + 1)
            table.style = "Table Grid"
            for c, label in enumerate(labels, start=1):
                table.rows[0].cells[c].text = label
            for r, row in enumerate(q["rows"], start=1):
                table.rows[r].cells[0].text = row["text"]
        elif q["type"] in {"text", "textarea", "number", "integer", "decimal", "date", "time"}:
            doc.add_paragraph("_________")

    doc.save(str(out_path))
    return warnings


# ---------------------------------------------------------------- CLI

def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft 问卷导出")
    parser.add_argument("--in", dest="src", required=True, help="survey.json 路径")
    parser.add_argument("--format", choices=["wjx-text", "word", "all"], default="wjx-text")
    parser.add_argument("--out", dest="out", help="输出文件（wjx-text→.txt / word→.docx）")
    parser.add_argument("--outdir", help="format=all 时的输出目录")
    args = parser.parse_args()

    survey = load_survey(args.src)
    outputs, all_warnings = [], []

    def emit(fmt: str, path: Path, warnings: list[str]) -> None:
        outputs.append({"format": fmt, "file": str(path), "warnings": warnings})
        all_warnings.extend(warnings)

    if args.format in {"wjx-text", "all"}:
        out = Path(args.out) if args.out and args.format == "wjx-text" else (
            Path(args.outdir or ".") / f"{survey['survey']['id']}_wjx.txt")
        text, warns = export_wjx_text(survey)
        save_text(text, out)
        emit("wjx-text", out, warns)
    if args.format in {"word", "all"}:
        out = Path(args.out) if args.out and args.format == "word" else (
            Path(args.outdir or ".") / f"{survey['survey']['id']}.docx")
        out.parent.mkdir(parents=True, exist_ok=True)
        warns = export_word(survey, out)
        emit("word", out, warns)

    print(json.dumps({"tool": "survey_export", "outputs": outputs,
                      "warning_count": len(all_warnings)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
