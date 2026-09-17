"""SurveyCraft Excel 结果包（DESIGN.md §18.3）。

用法：
    python scripts/excel.py --project surveys/<survey_id> \
        [--out report/survey_analysis.xlsx]

从项目目录自动发现工件（profile / dictionary / quality_report / cleaning_log /
scoring_log / analysis_plan / results/*.json），汇总为多 Sheet 工作簿：
README、Data Dictionary、Frequency、Tests、Reliability、Correlation、
Quality Report、Cleaning Log。全部数字来自已落盘的机器可读工件。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402


def _load(path: Path) -> dict | None:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _plan_summary(plan: dict) -> list[dict]:
    rows = []
    for a in plan.get("analyses", []):
        rows.append({
            "ID": a.get("id"), "类型": a.get("type"), "变量": a.get("dv", {}).get("variable")
            or a.get("v1") or a.get("construct") or "—",
            "主方法": a.get("method", "—"), "替代方法": a.get("fallback") or "—",
            "事后检验": a.get("posthoc") or "—", "状态": a.get("status"),
            "理由": a.get("why", ""),
        })
    return rows


def build_workbook(project: Path, out: Path) -> list[str]:
    outputs = project / "outputs"
    profile = _load(outputs / "profile.json")
    quality = _load(outputs / "quality_report.json")
    cleaning = _load(outputs / "cleaning_log.json")
    scoring = _load(outputs / "scoring_log.json")
    plan = _load(outputs / "analysis_plan.json")
    results = []
    if (outputs / "results").exists():
        results = [_load(p) for p in sorted((outputs / "results").glob("*.json"))
                   if p.name != "run_summary.json"]
        results = [r for r in results if r]

    dictionary = outputs / "dictionary.csv"
    dict_df = pd.read_csv(dictionary, encoding="utf-8-sig") if dictionary.exists() else None

    sheets: dict[str, pd.DataFrame] = {}

    meta_rows = [
        ["生成日期", date.today().isoformat()],
        ["SurveyCraft", "V1.1（M1 描述统计 + M2 推断统计）"],
        ["决策表", "references/method-router.md"],
        ["样本量（清洗后分析用）", str(plan.get("n")) if plan else "—"],
    ]
    if profile:
        meta_rows.append(["原始样本量", str(profile["summary"]["n_respondents"])])
        meta_rows.append(["缺失单元格比例", f"{profile['summary']['missing_rate_pct']}%"])
    if cleaning:
        meta_rows.append(["清洗排除样本数", str(cleaning.get("n_excluded"))])
    sheets["README"] = pd.DataFrame(meta_rows, columns=["项目", "说明"])

    if dict_df is not None:
        sheets["Data Dictionary"] = dict_df

    if profile:
        freq_rows = []
        for v in profile["variables"]:
            for f in v.get("frequencies", []):
                freq_rows.append({"Variable": v["variable"],
                                  "Label": v.get("label", ""),
                                  "选项": f["label"], "人数": f["n"], "占比%": f["pct"]})
        num_rows = []
        for v in profile["variables"]:
            if v.get("type") == "numeric" and "mean" in v:
                num_rows.append({"Variable": v["variable"], "Label": v.get("label", ""),
                                 "Mean": v["mean"], "SD": v.get("std"),
                                 "Median": v.get("median"), "Min": v.get("min"),
                                 "Max": v.get("max")})
        sheets["Frequency"] = pd.DataFrame(freq_rows) if freq_rows else pd.DataFrame()
        sheets["Numeric"] = pd.DataFrame(num_rows) if num_rows else pd.DataFrame()

    if plan:
        sheets["Analysis Plan"] = pd.DataFrame(_plan_summary(plan))

    test_rows, corr_rows, rel_rows = [], [], []
    for r in results:
        if r.get("error"):
            test_rows.append({"ID": r.get("id"), "方法": r.get("method"),
                              "错误": r["error"]})
            continue
        test = r.get("test")
        if test in {"chi_square", "fisher_exact"}:
            test_rows.append({"ID": r["id"], "方法": test, "统计量": r.get("chi2") or "—",
                              "df": r.get("df", "—"), "p": r.get("p"),
                              "效应量": f"{r.get('effect_size', {}).get('name')} = "
                                       f"{r.get('effect_size', {}).get('value')}",
                              "APA": r.get("apa"), "理由": r.get("why", "")})
        elif test in {"independent_t", "welch_t", "mann_whitney_u", "one_way_anova",
                      "welch_anova", "kruskal_wallis"}:
            es = r.get("effect_size", {})
            test_rows.append({"ID": r["id"], "方法": test,
                              "统计量": r.get("t") or r.get("u") or r.get("F") or r.get("H"),
                              "df": r.get("df", "—"), "p": r.get("p"),
                              "效应量": f"{es.get('name')} = {es.get('value')}" if es else "—",
                              "APA": r.get("apa"), "理由": r.get("why", "")})
        elif test in {"pearson", "spearman", "kendall_tau"}:
            corr_rows.append({"ID": r["id"], "方法": test, "r": r.get("r"),
                              "p": r.get("p"), "N": r.get("n"),
                              "95% CI": str(r.get("ci95")), "APA": r.get("apa"),
                              "理由": r.get("why", "")})
        elif test == "cronbach_alpha":
            rel_rows.append({"ID": r["id"], "构念": r.get("construct"),
                             "α": r.get("alpha"), "题项数": r.get("n_items"),
                             "N": r.get("n"), "APA": r.get("apa"),
                             "KMO": r.get("kmo", "—"),
                             "Bartlett p": (r.get("bartlett") or {}).get("p", "—")})
            for it in r.get("item_stats", []):
                rel_rows.append({"ID": r["id"], "构念": f"{r.get('construct')}·{it['item']}",
                                 "α": it.get("alpha_if_deleted"), "题项数": "",
                                 "N": "", "APA": f"CITC={it['citc']}（删除后α）", "KMO": "",
                                 "Bartlett p": ""})
    sheets["Tests"] = pd.DataFrame(test_rows) if test_rows else pd.DataFrame()
    sheets["Correlation"] = pd.DataFrame(corr_rows) if corr_rows else pd.DataFrame()
    sheets["Reliability"] = pd.DataFrame(rel_rows) if rel_rows else pd.DataFrame()

    if quality:
        q_rows = [{"规则": rule, "候选数": payload and len(payload.get("candidates", [])),
                   "说明": (payload or {}).get("note") or ""}
                  for rule, payload in (quality.get("rules") or {}).items()]
        sheets["Quality Report"] = pd.DataFrame(q_rows)
    if cleaning:
        c_rows = [{"respondent_id": a["respondent_id"],
                   "规则": "；".join(r["rule"] for r in a["rules"]),
                   "明细": "；".join(r["detail"] for r in a["rules"]),
                   "动作": a["action"]}
                  for a in cleaning.get("actions", [])]
        sheets["Cleaning Log"] = pd.DataFrame(c_rows)

    out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name[:31], index=False)
    return list(sheets.keys())


def _autofit(path: Path) -> None:
    from openpyxl import load_workbook
    wb = load_workbook(path)
    for ws in wb.worksheets:
        for column in ws.columns:
            width = max((len(str(c.value)) for c in column if c.value is not None), default=8)
            ws.column_dimensions[column[0].column_letter].width = min(48, max(10, width * 1.6))
    wb.save(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft Excel 结果包")
    parser.add_argument("--project", required=True, help="项目目录 surveys/<id>")
    parser.add_argument("--out", default=None, help="输出 xlsx（默认 <project>/report/survey_analysis.xlsx）")
    args = parser.parse_args()

    project = Path(args.project)
    if not project.exists():
        print(json.dumps({"error": f"项目目录不存在：{project}"}, ensure_ascii=False))
        return 1
    out = Path(args.out) if args.out else project / "report" / "survey_analysis.xlsx"
    sheets = build_workbook(project, out)
    _autofit(out)
    print(json.dumps({"tool": "excel", "file": str(out), "sheets": sheets},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
