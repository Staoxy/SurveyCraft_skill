"""SurveyCraft CLI 调度器（统一入口，DESIGN.md §22）。

用法：
    python scripts/surveycraft.py <command> [args...]

命令：
    design     # 初始化问卷项目（从模板生成 surveys/<id>/survey.json 与目录结构）
    validate   # Schema 校验
    review     # 问卷结构化审查（代码规则层）
    export     # 导出问卷（word / wjx-text / all）
    synthetic  # 生成模拟问卷星数据（测试/教学）
    import     # 数据导入（两阶段映射确认，Gate 0）
    profile    # 数据体检 + 数据字典
    quality    # 数据质量引擎（Gate 1，只标记不删除）
    clean      # 数据清洗（Gate 2，需用户确认）→ cleaning_log.json
    score      # 量表计分（反向计分 + 维度分）
    plan       # Method Router → analysis_plan.json（Gate 3）
    analyze    # 执行已确认的推断统计 → results/*.json
    charts     # 图表生成
    regress    # 回归分析（线性/Logistic/定序 + 诊断）
    text       # 开放题编码统计（Agent 编码后的确定性汇总）
    audit      # 数字审计（Gate 4：报告数字必须可追溯）
    report     # Markdown/DOCX 报告
    excel      # Excel 结果包
    power      # 样本量 / 功效计算
"""
from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, TEMPLATES_DIR, load_yaml, save_json  # noqa: E402

COMMANDS = {
    "validate": "schema_validate.py",
    "review": "survey_check.py",
    "export": "survey_export.py",
    "synthetic": "make_synthetic.py",
    "import": "data_import.py",
    "profile": "data_profile.py",
    "quality": "data_quality.py",
    "clean": "data_clean.py",
    "score": "scale_score.py",
    "plan": "method_router.py",
    "analyze": "stats_run.py",
    "regress": "regression.py",
    "text": "text_analysis.py",
    "audit": "result_audit.py",
    "power": "power.py",
    "charts": "charts.py",
    "report": "report_build.py",
    "excel": "excel.py",
}


def design_init(survey_id: str, template: str) -> int:
    src = TEMPLATES_DIR / "surveys" / f"{template}.yaml"
    if not src.exists():
        available = [p.stem for p in (TEMPLATES_DIR / "surveys").glob("*.yaml")]
        print(json.dumps({"error": f"模板 {template} 不存在", "available": available},
                         ensure_ascii=False))
        return 1
    project = ROOT / "surveys" / survey_id
    if (project / "survey.json").exists():
        print(json.dumps({"error": f"项目 {survey_id} 已存在"}, ensure_ascii=False))
        return 1
    for sub in ("data/raw", "data/clean", "outputs/charts", "report", "exports"):
        (project / sub).mkdir(parents=True, exist_ok=True)
    data = load_yaml(src)
    save_json(data, project / "survey.json")
    print(json.dumps({
        "tool": "design",
        "project": str(project),
        "survey_json": str(project / "survey.json"),
        "next": [
            f"python scripts/schema_validate.py --in {project / 'survey.json'}",
            f"python scripts/survey_check.py --in {project / 'survey.json'} --out {project / 'outputs' / 'review_report.json'}",
            f"python scripts/survey_export.py --in {project / 'survey.json'} --format all --outdir {project / 'exports'}",
        ],
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        print(__doc__)
        return 0
    cmd, rest = sys.argv[1], sys.argv[2:]

    if cmd == "design":
        import argparse
        parser = argparse.ArgumentParser(prog="surveycraft design")
        parser.add_argument("--init", dest="survey_id", required=True, help="新问卷项目 id")
        parser.add_argument("--template", default="university_survey", help="模板名（templates/surveys/）")
        args = parser.parse_args(rest)
        return design_init(args.survey_id, args.template)

    if cmd not in COMMANDS:
        print(json.dumps({"error": f"未知命令 {cmd}", "available": list(COMMANDS) + ["design"]},
                         ensure_ascii=False))
        return 1

    sys.argv = [f"surveycraft {cmd}"] + rest
    runpy.run_path(str(Path(__file__).resolve().parent / COMMANDS[cmd]), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
