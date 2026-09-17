"""SurveyCraft 报告生成器（M1：Markdown）。

用法：
    python scripts/report_build.py --profile outputs/profile.json \
        --survey survey.json --charts outputs/charts --out report/report.md

原则（DESIGN.md §16 / §18）：
- 报告中的所有数字由本程序从 profile.json 注入，Agent 只补解释文字
- 核心发现按 FACT 层程序生成（F01...Fnn），解读与局限由 Agent 引用 FACT 撰写
- 附录记录数据文件清单，保证可追溯
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_text  # noqa: E402


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _mapped_mean(stats: dict) -> float | None:
    """按 value_map × 频数计算映射均值（Likert 类）。"""
    vmap = stats.get("value_map") or {}
    freqs = stats.get("frequencies") or []
    total = sum(f["n"] for f in freqs)
    if not total or not vmap:
        return None
    weighted = sum(f["n"] * vmap[str(f["label"])] for f in freqs
                   if str(f["label"]) in vmap and vmap[str(f["label"])] is not None)
    mapped_n = sum(f["n"] for f in freqs if str(f["label"]) in vmap)
    if not mapped_n:
        return None
    return round(weighted / mapped_n, 2)


def build_facts(profile: dict) -> list[dict]:
    facts: list[dict] = []
    n_total = profile["summary"]["n_respondents"]

    def add(statement: str, sources: list[str]):
        facts.append({"id": f"F{len(facts) + 1:02d}", "statement": statement,
                      "sources": sources})

    for v in profile["variables"]:
        if len(facts) >= 12:
            break
        label = v.get("label") or v["variable"]
        if v.get("attention_check") or v.get("type") in {"text", "meta_text", "binary"}:
            continue
        if v.get("variable") in {"respondent_id", "submitted_at", "duration_sec",
                                 "duration_raw", "ip"}:
            continue
        if v.get("frequencies") and v.get("value_map") and v.get("scale_item")                 and v.get("question_id"):
            mean = _mapped_mean(v)
            top = max(v["frequencies"], key=lambda f: f["n"])
            points = len(v.get("value_map") or {})
            if mean is not None:
                add(f"「{label}」的映射均值为 {mean}（{points} 点量表），最集中选项为「{top['label']}」"
                    f"（{top['pct']}%）；有效作答 {v['n_valid']} 份。",
                    [v["variable"]])
        elif v.get("frequencies") and v.get("question_id"):
            top = max(v["frequencies"], key=lambda f: f["n"])
            add(f"「{label}」中最常见的选项为「{top['label']}」，占有效作答的 {top['pct']}%"
                f"（{top['n']}/{v['n_valid']}；全样本 {n_total}）。",
                [v["variable"]])
        elif v.get("type") == "numeric" and "mean" in v and v["variable"] != "duration_sec":
            add(f"「{label}」均值为 {v['mean']}（SD={v.get('std')}，中位数 {v.get('median')}，"
                f"范围 {v.get('min')}–{v.get('max')}）。",
                [v["variable"]])
    return facts


def _cleaning_section(cleaning: dict | None) -> list[str]:
    if not cleaning:
        return []
    lines = [f"- 数据清洗：排除 {cleaning.get('n_excluded', 0)} 个样本"
             f"（{cleaning.get('n_before')} → {cleaning.get('n_after')}，模式：{cleaning.get('mode')}）。"
             "明细见 `outputs/cleaning_log.json`。"]
    for a in cleaning.get("actions", []):
        if a["action"].startswith("excluded"):
            rules = "；".join(r["rule"] for r in a["rules"])
            lines.append(f"  - {a['respondent_id']}：{rules} → {a['action']}")
    return lines


def _results_section(results_dir: Path) -> list[str]:
    if not results_dir.exists():
        return []
    files = sorted(results_dir.glob("*.json"))
    files = [f for f in files if f.name != "run_summary.json"]
    if not files:
        return []
    lines = ["以下结果均由 stats_run 按用户确认的分析计划（Gate 3）执行生成，"]
    lines.append("解释文字只能引用本节与第 7 节 FACT 中的数字。")
    lines.append("")
    for f in files:
        r = json.loads(f.read_text(encoding="utf-8"))
        if r.get("error"):
            lines.append(f"### {r.get('id')}　（执行失败：{r['error']}）")
            lines.append("")
            continue
        lines.append(f"### {r.get('id')}　{r.get('why', '')}")
        lines.append("")
        lines.append(f"- **{r.get('apa', '')}**（方法：{r.get('test')}）")
        if r.get("fallback_available"):
            lines.append(f"- 计划的替代方法（前提不满足时）：{r['fallback_available']}")
        posthoc = r.get("posthoc")
        if posthoc and posthoc.get("comparisons"):
            lines.append("- 事后检验（Holm 校正）：")
            for c in posthoc["comparisons"][:8]:
                lines.append(f"  - {c['group_1']} vs {c['group_2']}："
                             f"p = {c['p']}，p_holm = {c['p_holm']}")
        lines.append("")
    return lines


def build_report(profile: dict, survey: dict | None, charts: list[dict],
                 chart_dir: Path, cleaning: dict | None = None,
                 results_dir: Path | None = None) -> str:
    summary = profile["summary"]
    survey_meta = (survey or {}).get("survey", {})
    lines: list[str] = []
    title = survey_meta.get("title", "调查数据分析报告")
    lines.append(f"# {title}——数据分析报告（M1 描述统计版）")
    lines.append("")
    lines.append(f"生成日期：{date.today().isoformat()}　|　"
                 f"问卷：{survey_meta.get('id', profile.get('survey_id') or '未提供 schema')}　|　"
                 f"问卷版本：{survey_meta.get('version', '—')}")
    lines.append("")

    # 1 调查概况
    lines.append("## 1. 调查概况")
    lines.append("")
    rows = [["有效样本量", str(summary["n_respondents"])],
            ["变量数", str(summary["n_variables"])],
            ["缺失单元格比例", f"{summary['missing_rate_pct']}%"],
            ["数据来源文件", profile.get("source", "—")]]
    if "duration_sec" in summary and summary["duration_sec"]:
        d = summary["duration_sec"]
        rows.append(["作答时长中位数", f"{d.get('median')} 秒"])
        rows.append(["作答时长范围", f"{d.get('min')} – {d.get('max')} 秒"])
    lines.append(_md_table(["项目", "数值"], rows))
    lines.append("")

    # 2 数据字典（摘要：前 20 行）
    lines.append("## 2. 数据字典（摘要）")
    lines.append("")
    dict_rows = []
    for v in profile["variables"][:20]:
        dict_rows.append([v["variable"], (v.get("label") or "—")[:24], v.get("type") or "—",
                          v.get("level") or "—", v.get("missing", 0)])
    lines.append(_md_table(["Variable", "Label", "Type", "Level", "Missing"], dict_rows))
    lines.append("")
    lines.append("完整字典见 `outputs/dictionary.csv`。")
    lines.append("")

    # 3 数据质量概览
    lines.append("## 3. 数据质量概览")
    lines.append("")
    lines.append(f"- 全表缺失单元格比例 {summary['missing_rate_pct']}%（含因跳题逻辑产生的结构性缺失）。")
    missing_sorted = sorted(profile["variables"], key=lambda v: -v.get("missing", 0))
    top_missing = [v for v in missing_sorted if v.get("missing", 0) > 0][:5]
    if top_missing:
        lines.append("- 缺失最多的变量：")
        lines.append("")
        lines.append(_md_table(
            ["Variable", "Label", "缺失数", "缺失率"],
            [[v["variable"], (v.get("label") or "—")[:24], v["missing"],
              f"{v.get('missing_pct')}%"]
             for v in top_missing]))
    att = [v for v in profile["variables"] if v.get("attention_check")]
    if att:
        lines.append("")
        for v in att:
            lines.append(f"- 注意力检查题「{v['label']}」的作答分布："
                         + "、".join(f"{f['label']} {f['pct']}%" for f in v.get("frequencies", [])))
    if cleaning:
        lines.append("")
        lines.extend(_cleaning_section(cleaning))
    lines.append("")
    lines.append("> 完整的数据质量引擎（直线作答、速答、逻辑矛盾等）属 M2 里程碑，本版仅报告结构性缺失。")
    lines.append("")

    # 4 描述统计
    lines.append("## 4. 描述统计")
    lines.append("")
    for v in profile["variables"]:
        if v["variable"] in {"respondent_id", "submitted_at", "duration_raw", "ip"}:
            continue
        if v.get("type") == "text" or v.get("attention_check"):
            continue
        lines.append(f"### {v['variable']}　{(v.get('label') or '').strip()}")
        lines.append("")
        if v.get("frequencies"):
            freq_rows = [[f["label"], f["n"], f"{f['pct']}%"] for f in v["frequencies"]]
            lines.append(_md_table(["选项", "人数", "占比"], freq_rows))
            mean = v.get("mapped_mean")
            if mean is not None:
                lines.append("")
                lines.append(f"映射均值：**{mean}**")
        elif v.get("type") == "numeric" and "mean" in v:
            lines.append(_md_table(
                ["Mean", "SD", "Median", "Min", "Q1", "Q3", "Max"],
                [[v.get("mean"), v.get("std"), v.get("median"), v.get("min"),
                  v.get("q1"), v.get("q3"), v.get("max")]]))
        elif v.get("type") == "binary":
            continue
        lines.append("")

    # 5 推断统计
    if results_dir is not None:
        res_lines = _results_section(results_dir)
        if res_lines:
            lines.append("## 5. 推断统计结果")
            lines.append("")
            lines.extend(res_lines)

    # 6 图表
    if charts:
        lines.append("## 6. 图表")
        lines.append("")
        for c in charts:
            rel = Path(c["png"]).name
            lines.append(f"![{c['title']}](../outputs/charts/{rel})")
            lines.append("")
            lines.append(f"*图：{c['title']}*（聚合数据：`outputs/charts/{Path(c['csv']).name}`）")
            lines.append("")

    # 6 核心发现（FACT）
    facts = build_facts(profile)
    lines.append("## 7. 核心发现（FACT，程序生成）")
    lines.append("")
    for f in facts:
        lines.append(f"- **{f['id']}** {f['statement']}")
    lines.append("")

    # 7 解读与局限（Agent 撰写）
    lines.append("## 8. 核心发现解读（INTERPRETATION / LIMITATION，由 Agent 撰写）")
    lines.append("")
    lines.append("> 撰写规则：解释文字只能引用第 7 节 FACT 与第 5 节推断统计中的数字，不得引入任何新数字；")
    lines.append("> 每条解读按 INTERPRETATION（统计解释）与 LIMITATION（研究限制，如抽样方式）两层展开。")
    lines.append("")
    lines.append("（待撰写）")
    lines.append("")

    # 8 附录
    lines.append("## 9. 附录：工件清单")
    lines.append("")
    lines.append("- 原始数据：`data/raw/`（导入副本，未修改）")
    lines.append("- 映射记录：`data/mapping.json`（Gate 0 用户确认）")
    lines.append("- 导入报告：`data/import_report.json`")
    lines.append("- 数据字典：`outputs/dictionary.csv`")
    lines.append("- 描述统计：`outputs/profile.json`")
    lines.append("- 量表计分：`outputs/scoring_log.json`")
    lines.append("- 分析计划：`outputs/analysis_plan.json`（Gate 3）")
    lines.append("- 统计结果：`outputs/results/*.json`")
    lines.append("- 图表聚合数据：`outputs/charts/*.csv`")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- DOCX 渲染

def _add_runs(paragraph, text: str):
    """支持 **加粗** 的极简行内渲染。"""
    import re
    parts = re.split(r"(\*\*[^*]+\*\*)", text)
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            run = paragraph.add_run(part[2:-2])
            run.bold = True
        elif part:
            paragraph.add_run(part)


def build_docx(md_text: str, base_dir: Path, out_path: Path) -> None:
    """把 report_build 生成的 Markdown 渲染为 Word（我们可控的子集：标题/表格/列表/图片）。"""
    import re

    from docx import Document
    from docx.shared import Inches, Pt

    from survey_export import _set_cjk

    doc = Document()
    _set_cjk(doc.styles["Normal"], "Times New Roman", "宋体")
    doc.styles["Normal"].font.size = Pt(11)
    for style in ("Title", "Heading 1", "Heading 2", "Heading 3"):
        _set_cjk(doc.styles[style], "Arial", "黑体")

    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith(">"):
            i += 1
            continue
        if stripped.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r"-{3,}", c) for c in cells):  # 跳过分隔行
                    rows.append(cells)
                i += 1
            if rows:
                ncols = max(len(r) for r in rows)
                table = doc.add_table(rows=len(rows), cols=ncols)
                table.style = "Table Grid"
                for r, row in enumerate(rows):
                    for c in range(ncols):
                        cell = table.rows[r].cells[c]
                        cell.text = row[c] if c < len(row) else ""
                        if r == 0:
                            for run in cell.paragraphs[0].runs or [cell.paragraphs[0].add_run(cell.text)]:
                                run.bold = True
            continue
        img = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", stripped)
        if img:
            img_path = (base_dir / img.group(2)).resolve()
            if img_path.exists():
                doc.add_picture(str(img_path), width=Inches(5.8))
            else:
                doc.add_paragraph(f"[图片缺失：{img.group(2)}]")
            i += 1
            continue
        if stripped.startswith("# "):
            doc.add_heading(stripped[2:], level=0)
        elif stripped.startswith("## "):
            doc.add_heading(stripped[3:], level=1)
        elif stripped.startswith("### "):
            doc.add_heading(stripped[4:], level=2)
        elif stripped.startswith("- "):
            doc.add_paragraph(style="List Bullet")
            _add_runs(doc.paragraphs[-1], stripped[2:])
        elif re.match(r"^\d+\.\s", stripped):
            doc.add_paragraph(style="List Number")
            _add_runs(doc.paragraphs[-1], re.sub(r"^\d+\.\s", "", stripped))
        else:
            doc.add_paragraph()
            _add_runs(doc.paragraphs[-1], stripped)
        i += 1
    doc.save(str(out_path))


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft 报告生成（Markdown）")
    parser.add_argument("--profile", required=True, help="profile.json")
    parser.add_argument("--survey", help="survey.json")
    parser.add_argument("--charts", default="outputs/charts", help="图表目录（含 charts.json 可选）")
    parser.add_argument("--results-dir", dest="results_dir", default="outputs/results",
                        help="stats_run 结果目录（缺失则报告不含推断统计章节）")
    parser.add_argument("--cleaning-log", dest="cleaning_log", default="outputs/cleaning_log.json")
    parser.add_argument("--out", default="report/report.md")
    parser.add_argument("--format", choices=["markdown", "docx", "all"], default="markdown")
    args = parser.parse_args()

    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    survey = json.loads(Path(args.survey).read_text(encoding="utf-8")) if args.survey else None

    charts: list[dict] = []
    chart_dir = Path(args.charts)
    chart_list = chart_dir / "charts.json"
    if chart_list.exists():
        charts = json.loads(chart_list.read_text(encoding="utf-8"))
    else:
        for png in sorted(chart_dir.glob("*.png")):
            charts.append({"title": png.stem, "png": str(png),
                           "csv": str(png.with_suffix(".csv"))})

    cleaning = None
    if Path(args.cleaning_log).exists():
        cleaning = json.loads(Path(args.cleaning_log).read_text(encoding="utf-8"))
    report = build_report(profile, survey, charts, chart_dir,
                          cleaning=cleaning, results_dir=Path(args.results_dir))
    outputs = []
    if args.format in {"markdown", "all"}:
        outputs.append({"format": "markdown", "file": str(save_text(report, args.out))})
    if args.format in {"docx", "all"}:
        docx_out = Path(args.out).with_suffix(".docx")
        build_docx(report, Path(args.out).parent, docx_out)
        outputs.append({"format": "docx", "file": str(docx_out)})
    print(json.dumps({"tool": "report_build", "outputs": outputs,
                      "facts": len(build_facts(profile))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
