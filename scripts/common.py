"""SurveyCraft 共享工具：路径、IO、问卷加载与规范化、issue 构造、matplotlib 中文字体。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = ROOT / "schemas"
TEMPLATES_DIR = ROOT / "templates"
REFERENCES_DIR = ROOT / "references"

CHOICE_TYPES = {"single_choice", "multiple_choice", "ranking"}
MATRIX_TYPES = {"matrix_single", "matrix_multiple"}
SCALED_TYPES = {"likert", "rating", "nps"}
ALL_TYPES = {
    "single_choice", "multiple_choice", "text", "textarea", "number", "integer",
    "decimal", "date", "time", "rating", "likert", "matrix_single",
    "matrix_multiple", "ranking", "yes_no", "nps",
}


# ---------------------------------------------------------------- IO

def load_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(obj: Any, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def load_yaml(path: str | Path) -> Any:
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_text(text: str, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# ---------------------------------------------------------------- survey 加载与规范化

def load_survey(path: str | Path) -> dict:
    """加载问卷 JSON 并规范化默认值，返回深拷贝。

    规范化内容：
    - yes_no 题缺 options 时补 是/否（value 1/2）
    - required / reverse_scored / attention_check / randomize_options 补默认值
    - 非矩阵题缺 variable 时用 id 小写（如 Q01 → q01）
    - scale 缺 values 时按 min..max 连续整数生成
    """
    raw = load_json(path)
    survey = copy.deepcopy(raw)
    for q in survey.get("questions", []):
        if q["type"] == "yes_no" and not q.get("options"):
            q["options"] = [
                {"id": "A", "label": "是", "value": 1},
                {"id": "B", "label": "否", "value": 2},
            ]
        q["required"] = bool(q.get("required", True))
        q["reverse_scored"] = bool(q.get("reverse_scored", False))
        q["attention_check"] = bool(q.get("attention_check", False))
        q.setdefault("randomize_options", False)
        if q["type"] in MATRIX_TYPES:
            q.setdefault("variable_prefix", f"{q['id'].lower()}_")
        elif "variable" not in q:
            q["variable"] = q["id"].lower()
        if "scale" in q:
            scale = q["scale"]
            if "values" not in scale:
                scale["values"] = list(range(int(scale["min"]), int(scale["max"]) + 1))
    return survey


def ordered_questions(survey: dict) -> tuple[list[dict], list[str]]:
    """按 sections 顺序返回 (题目列表, 未被任何 section 引用的题目 id 列表)。"""
    index = {q["id"]: q for q in survey.get("questions", [])}
    ordered: list[dict] = []
    seen: set[str] = set()
    for section in survey.get("sections", []):
        for qid in section.get("question_ids", []):
            if qid in index and qid not in seen:
                ordered.append(index[qid])
                seen.add(qid)
    orphans = [qid for qid in index if qid not in seen]
    return ordered, orphans


def question_index(survey: dict) -> dict[str, dict]:
    return {q["id"]: q for q in survey.get("questions", [])}


def option_by_id(question: dict) -> dict[str, dict]:
    return {o["id"]: o for o in question.get("options", [])}


def derived_variable(qid: str, option_id: str) -> str:
    """多选题展开列名：Q03_A。"""
    return f"{qid}_{option_id}"


def other_variable(qid: str, option_id: str) -> str:
    """“其他”填空子字段列名：Q03_C_other。"""
    return f"{qid}_{option_id}_other"


# ---------------------------------------------------------------- issue

def make_issue(
    severity: str,
    category: str,
    source: str,
    message: str,
    rule_id: Optional[str] = None,
    suggestion: Optional[str] = None,
    question_id: Optional[str] = None,
    option_ids: Optional[list[str]] = None,
    row_ids: Optional[list[str]] = None,
    logic_id: Optional[str] = None,
    respondent_id: Optional[str] = None,
) -> dict:
    target: dict[str, Any] = {}
    if question_id:
        target["question_id"] = question_id
    if option_ids:
        target["option_ids"] = option_ids
    if row_ids:
        target["row_ids"] = row_ids
    if logic_id:
        target["logic_id"] = logic_id
    if respondent_id:
        target["respondent_id"] = respondent_id
    issue: dict[str, Any] = {
        "id": "",
        "severity": severity,
        "category": category,
        "source": source,
        "message": message,
    }
    if target:
        issue["target"] = target
    if rule_id:
        issue["rule_id"] = rule_id
    if suggestion:
        issue["suggestion"] = suggestion
    return issue


def assign_issue_ids(issues: list[dict]) -> list[dict]:
    for i, issue in enumerate(issues, start=1):
        issue["id"] = f"ISS-{i:03d}"
    return issues


# ---------------------------------------------------------------- matplotlib 中文字体

def setup_cjk_font() -> Optional[str]:
    """配置 matplotlib 中文字体，返回命中的字体名（未找到返回 None）。"""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager
    names = {f.name for f in font_manager.fontManager.ttflist}
    for cand in ("Microsoft YaHei", "SimHei", "PingFang SC", "Noto Sans CJK SC"):
        if cand in names:
            _apply_font(cand)
            return cand
    _apply_font("sans-serif")
    return None


def _apply_font(name: str) -> None:
    import matplotlib
    matplotlib.rcParams["font.family"] = name
    matplotlib.rcParams["axes.unicode_minus"] = False
