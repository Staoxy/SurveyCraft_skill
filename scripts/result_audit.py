"""SurveyCraft 数字审计（Gate 4，DESIGN.md §16）。

用法：
    python scripts/result_audit.py --report report/report.md \
        --profile outputs/profile.json --results-dir outputs/results \
        [--out outputs/number_audit.json]

规则：报告正文中的数字必须能追溯到 profile.json 或 results/*.json。
- 百分数：与 profile 频数表的 pct（或 n）匹配
- 统计量（t/F/H/χ²/r/d/α/p…）：与 results 数值字段匹配（含 p < .001 特例）
- 找不到来源的数字 → ERROR；有 ERROR 时退出码 1（阻止交付报告）
- 附录/文件名/日期等位置以启发式排除（markdown 链接、反引号内、年份）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_json  # noqa: E402

NUM_RE = re.compile(r"(?<![\w.])(-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-\.\d+)(?![\w])")
CODE_SPAN_RE = re.compile(r"(`[^`]*`)|(\[[^\]]*\]\([^)]*\))|(\|)")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def collect_traceable(profile: dict, results: list[dict]) -> tuple[set[float], float | None]:
    values: set[float] = set()

    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in {"date", "source", "file", "data", "coding_file", "scoring_log",
                         "quality_report", "source_file", "decision_table"}:
                    continue
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)
        elif isinstance(obj, bool):
            return
        elif isinstance(obj, (int, float)):
            values.add(float(obj))

    _walk(profile)
    for r in results:
        _walk(r)
    p_values = [v for v in values if 0 < v <= 0.05]
    return values, (min(p_values) if p_values else None)


def _add(values: set[float], token: str) -> None:
    values.add(float(token.replace(",", "")))


def audit_report(report_text: str, profile: dict, results: list[dict]) -> dict:
    traceable, min_p = collect_traceable(profile, results)
    lines = report_text.splitlines()
    findings: list[dict] = []

    for lineno, line in enumerate(lines, start=1):
        stripped = CODE_SPAN_RE.sub("|", line)  # 排除代码段/链接/表格分隔
        for m in NUM_RE.finditer(stripped):
            token = m.group(1)
            try:
                value = float(token.replace(",", ""))
            except ValueError:
                continue
            context = stripped[max(0, m.start() - 30):m.end() + 30]
            # "95% CI" 是置信水平的固定标签，不是数据断言
            if stripped[m.end():].startswith("% CI"):
                continue
            # 区间写法（如 18-20）中的 "-20" 不是独立数字
            if m.start() > 0 and stripped[m.start()] == "-" and                     m.start() >= 2 and stripped[m.start() - 1].isdigit():
                continue
            if m.start() > 0 and stripped[m.start() - 1] == "-" and                     m.start() >= 2 and stripped[m.start() - 2].isdigit() and                     stripped[m.start()] != "-":
                pass
            # 排除：年份、纯序号（如 "### 5.1"）、行首编号 "- **F01**"、样本量表已单独入库
            if YEAR_RE.fullmatch(token.split(".")[0]) and "." not in token:
                continue
            if re.match(r"^\s*#{1,6}\s*\d", stripped) and m.start() < 8:
                continue
            if re.match(r"^\s*\d+\.\s", stripped) and m.end() <= len(stripped.split(".")[0]) + 2:
                continue
            ok = value in traceable or any(
                abs(value - v) <= max(0.0051, abs(v) * 0.0006) for v in traceable)
            if not ok and re.search(r"p\s*<\s*\.?0*1\b", context) and value == 0.001 \
                    and min_p is not None and min_p <= 0.001:
                ok = True  # APA 的 p < .001 约定
            findings.append({"line": lineno, "value": value, "token": token,
                             "context": context.strip(), "traceable": ok})

    untraceable = [f for f in findings if not f["traceable"]]
    return {
        "tool": "result_audit",
        "n_numbers_checked": len(findings),
        "n_untraceable": len(untraceable),
        "passed": not untraceable,
        "untraceable": untraceable[:50],
        "gate": "Gate 4：存在不可追溯数字时报告不得交付；请改为引用 FACT/结果数值，或修正模板。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft 数字审计（Gate 4）")
    parser.add_argument("--report", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--results-dir", dest="results_dir", default="outputs/results")
    parser.add_argument("--out", default="outputs/number_audit.json")
    args = parser.parse_args()

    text = Path(args.report).read_text(encoding="utf-8")
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    results = []
    rdir = Path(args.results_dir)
    if rdir.exists():
        results = [json.loads(f.read_text(encoding="utf-8"))
                   for f in sorted(rdir.glob("*.json")) if f.name != "run_summary.json"]
    result = audit_report(text, profile, results)
    result["report"] = args.report
    save_json(result, args.out)
    print(json.dumps({k: result[k] for k in ("tool", "n_numbers_checked",
                                             "n_untraceable", "passed")},
                     ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
