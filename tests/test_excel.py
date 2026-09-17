"""excel.py 结果包冒烟测试。"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from common import ROOT
from excel import build_workbook


def test_excel_package_sheets():
    tmp = Path(tempfile.mkdtemp(prefix="sc_excel_"))
    try:
        out = tmp / "survey_analysis.xlsx"
        sheets = build_workbook(ROOT / "surveys" / "demo", out)
        assert out.exists()
        expected = {"README", "Data Dictionary", "Frequency", "Tests",
                    "Reliability", "Correlation", "Quality Report", "Cleaning Log"}
        assert expected <= set(sheets), sheets
        import pandas as pd
        tests = pd.read_excel(out, sheet_name="Tests")
        assert len(tests) >= 2  # 组间比较 + 交叉表（相关/信度分属独立 Sheet）
        assert any("kruskal" in str(v) or "chi" in str(v) or "t" in str(v)
                   for v in tests["方法"])
        rel = pd.read_excel(out, sheet_name="Reliability")
        assert any("满意度" in str(v) for v in rel["构念"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_excel_cli():
    import json
    tmp = Path(tempfile.mkdtemp(prefix="sc_excel_cli_"))
    try:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "excel.py"),
             "--project", str(ROOT / "surveys" / "demo"), "--out", str(tmp / "out.xlsx")],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["tool"] == "excel" and (tmp / "out.xlsx").exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
