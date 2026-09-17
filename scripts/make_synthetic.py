"""SurveyCraft 合成数据生成器：按 survey.json 生成模拟的问卷星导出数据。

用法：
    python scripts/make_synthetic.py --in survey.json --n 150 --seed 42 \
        --out data/raw/synthetic.csv --encoding gb18030

用途（DESIGN.md §20）：
- 测试夹具：在收到真实数据前验证导入→分析全链路
- 教学演示：课程上先跑通流程再发放真实问卷

输出格式模拟问卷星「文本模式」导出：
- 列：提交答卷时间 / 所用时间 / 来自IP / "N、题干[题型]"（矩阵题每行一列）
- 多选题一格内逗号拼接选项文本；跳题处留空
- 注入四类低质量样本用于数据质量检验：直线作答、速答、注意力检查失败、逻辑矛盾
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    MATRIX_TYPES, load_survey, ordered_questions,
)

WJX_TYPE_MARKER = {
    "single_choice": "单选题", "yes_no": "单选题", "multiple_choice": "多选题",
    "text": "填空题", "textarea": "填空题", "number": "填空题", "integer": "填空题",
    "decimal": "填空题", "date": "填空题", "time": "填空题", "likert": "量表题",
    "rating": "量表题", "nps": "量表题", "matrix_single": "矩阵量表题",
    "matrix_multiple": "矩阵多选题", "ranking": "排序题",
}

INSTRUCTED_RE = re.compile(r"请选择[\"“](.+?)[\"”]")

OTHER_TEXT_POOL = ["老师推荐", "社交媒体上的推荐帖", "同学介绍", "公众号文章"]

OPEN_TEXT_POOL = [
    "希望答案能更准确一些，有时候会一本正经地胡说八道。",
    "希望免费额度能多一点。",
    "界面再简洁一点就好了，功能入口太深。",
    "希望支持直接上传课件让它总结。",
    "响应速度有时很慢，高峰期基本用不了。",
    "希望有针对大学生学习场景的专门模式。",
    "中文语境下的写作辅助还有提升空间。",
    "希望能记住我之前的对话，不用每次重复背景。",
]


def _visible_rules(survey: dict) -> dict[str, list[dict]]:
    rules: dict[str, list[dict]] = {}
    for rule in survey.get("logic", []):
        if rule.get("type") == "visibleIf":
            rules.setdefault(rule["target"], []).append(rule["condition"])
    return rules


def _condition_met(cond: dict, answers: dict, q_index: dict) -> bool:
    q = q_index.get(cond.get("question"))
    if q is None:
        return True
    val = answers.get(cond["question"])
    op = cond.get("operator")
    if op == "answered":
        return val not in (None, "")
    if op == "not_answered":
        return val in (None, "")
    chosen = {val} if not isinstance(val, list) else set(val)
    wanted = set(cond.get("value") or [])
    label_to_id = {o["label"]: o["id"] for o in q.get("options", [])}
    chosen_ids = {label_to_id.get(c, c) for c in chosen}
    if op == "in":
        return bool(chosen_ids & wanted)
    if op == "not_in":
        return not (chosen_ids & wanted)
    if op == "eq":
        return val == cond.get("value")
    return True


def _likert_pick(rng: random.Random, base: float, scale: dict) -> str:
    return _likert_pick_from(base + rng.gauss(0, 0.9), scale)


def _likert_pick_from(value: float, scale: dict) -> str:
    """按给定连续值就近映射到量表标签。"""
    values = scale["values"]
    idx = round(value - values[0])
    idx = max(0, min(len(values) - 1, idx))
    return scale["labels"][idx]


def generate(survey: dict, n: int, seed: int = 42,
             rates: dict | None = None) -> tuple[list[dict], list[str], dict]:
    """生成 n 条模拟作答。返回 (行列表, 列名列表, 摘要)。"""
    rng = random.Random(seed)
    rates = {"straight_liner": 0.05, "speeder": 0.05, "attention_fail": 0.08,
             "logic_violator": 0.02, **(rates or {})}
    ordered, _ = ordered_questions(survey)
    q_index = {q["id"]: q for q in ordered}
    visible = _visible_rules(survey)
    # 筛选题（被 visibleIf 引用的题）：首选项加权，模拟“多数人符合筛选条件”
    screening_ids = {c.get("question") for cs in visible.values() for c in cs}
    other_prefix_pool = OTHER_TEXT_POOL

    # 列顺序（问卷星文本模式）
    columns = ["提交答卷时间", "所用时间", "来自IP"]
    col_of_question: dict[str, list[str]] = {}
    for no, q in enumerate(ordered, start=1):
        stem = f"{no}、{q['text']}[{WJX_TYPE_MARKER[q['type']]}]"
        if q["type"] in MATRIX_TYPES:
            cols = [f"{stem}.{row['text']}" for row in q["rows"]]
        else:
            cols = [stem]
        col_of_question[q["id"]] = cols
        columns.extend(cols)

    n_straight = round(n * rates["straight_liner"])
    n_speed = round(n * rates["speeder"])
    n_attfail = round(n * rates["attention_fail"])
    n_logic = round(n * rates["logic_violator"])
    straight_set = set(rng.sample(range(n), n_straight))
    speed_set = set(rng.sample(range(n), n_speed))
    attfail_set = set(rng.sample(range(n), n_attfail))
    logic_set = set(rng.sample(range(n), n_logic))

    rows: list[dict] = []
    start_time = datetime(2026, 10, 8, 9, 0, 0)

    for i in range(n):
        answers: dict[str, object] = {}
        # 每个构念一个潜变量：量表题项 = 潜变量 + 题项噪声（模拟真实量表的内部一致性）
        latent: dict[str, float] = {}

        def _latent(cname: str) -> float:
            if cname not in latent:
                latent[cname] = rng.gauss(3.5, 0.8)
            return latent[cname]

        # —— 生成作答 ——
        for q in ordered:
            qid, qt = q["id"], q["type"]
            conds = visible.get(qid, [])
            if conds and not all(_condition_met(c, answers, q_index) for c in conds):
                answers[qid] = None if qt not in MATRIX_TYPES else {r["variable"]: None for r in q["rows"]}
                continue
            if qt in {"single_choice", "yes_no"}:
                if qid in screening_ids:
                    weights = [0.78] + [0.22 / max(1, len(q["options"]) - 1)] * (len(q["options"]) - 1)
                    answers[qid] = rng.choices(q["options"], weights=weights)[0]["label"]
                else:
                    answers[qid] = rng.choice(q["options"])["label"]
            elif qt == "multiple_choice":
                picked = [o for o in q["options"] if rng.random() < 0.38]
                if not picked:
                    picked = [rng.choice(q["options"])]
                labels = []
                for o in picked:
                    if o.get("other_text") and rng.random() < 0.4:
                        labels.append(f"{o['label']}：{rng.choice(other_prefix_pool)}")
                    else:
                        labels.append(o["label"])
                answers[qid] = labels
            elif qt in {"likert", "rating"}:
                if q.get("attention_check"):
                    m = INSTRUCTED_RE.search(q["text"])
                    instructed = m.group(1) if m and m.group(1) in q["scale"]["labels"] else None
                    answers[qid] = instructed or q["scale"]["labels"][len(q["scale"]["labels"]) // 2]
                else:
                    base = _latent(q.get("construct") or qid)
                    if q.get("reverse_scored"):
                        # 反向题：构念水平越高，题面作答越低
                        base = q["scale"]["min"] + q["scale"]["max"] - base
                    answers[qid] = _likert_pick_from(base + rng.gauss(0, 0.6), q["scale"])
            elif qt == "nps":
                answers[qid] = str(rng.choices(
                    range(int(q["scale"]["min"]), int(q["scale"]["max"]) + 1),
                    weights=[1, 1, 1, 2, 2, 3, 4, 6, 8, 8, 9][:q["scale"]["max"] - q["scale"]["min"] + 1])[0])
            elif qt in {"text", "textarea"}:
                answers[qid] = rng.choice(OPEN_TEXT_POOL) if rng.random() < 0.85 else ""
            elif qt in {"number", "integer", "decimal"}:
                answers[qid] = str(rng.randint(1, 100))
            elif qt in {"date", "time"}:
                answers[qid] = "2026-10-10"
            elif qt in MATRIX_TYPES:
                base = _latent(q.get("construct") or qid)
                answers[qid] = {row["variable"]:
                                _likert_pick_from(base + rng.gauss(0, 0.6), q["scale"])
                                for row in q["rows"]}
            else:
                answers[qid] = ""

        # —— 注入低质量模式 ——
        if i in straight_set:
            likert_qids = [q["id"] for q in ordered if q["type"] in {"likert", "rating"}]
            fixed = _likert_pick(rng, 3.0, q_index[likert_qids[0]]["scale"]) if likert_qids else "3"
            for qid in likert_qids:
                if answers.get(qid) is not None:
                    answers[qid] = fixed
            for q in ordered:
                val = answers.get(q["id"])
                if q["type"] in MATRIX_TYPES and isinstance(val, dict) \
                        and any(v is not None for v in val.values()):
                    # 仅覆盖真实作答了矩阵题的样本；跳题样本（全 None）保持跳题
                    answers[q["id"]] = {row["variable"]: fixed for row in q["rows"]}
        if i in attfail_set:
            for q in ordered:
                if q.get("attention_check"):
                    m = INSTRUCTED_RE.search(q["text"])
                    labels = q["scale"]["labels"]
                    wrong = next((label for label in labels
                                  if not m or label != m.group(1)), labels[-1])
                    answers[q["id"]] = wrong
        if i in logic_set:
            screening = next((q for q in ordered if q["type"] == "single_choice"
                              and any(c.get("question") == q["id"] for cs in visible.values() for c in cs)), None)
            if screening:
                no_option = screening["options"][-1]["label"]  # 惯例：最后一项为“没有/否”
                answers[screening["id"]] = no_option
                for q in ordered:  # 保留其后续作答 → 逻辑矛盾
                    conds = visible.get(q["id"], [])
                    if any(c.get("question") == screening["id"] for c in conds):
                        if q["type"] in {"likert", "rating"}:
                            answers[q["id"]] = _likert_pick(rng, 3.5, q["scale"])
                        elif q["type"] in MATRIX_TYPES:
                            answers[q["id"]] = {r["variable"]: _likert_pick(rng, 3.5, q["scale"])
                                                for r in q["rows"]}
                        elif q["type"] in {"single_choice", "yes_no"} and q["id"] != screening["id"]:
                            answers[q["id"]] = rng.choice(q["options"])["label"]

        # —— 时间与时长 ——
        duration = rng.randint(15, 45) if i in speed_set else max(40, int(rng.gauss(180, 70)))
        submitted = start_time + timedelta(minutes=int(rng.gauss(0, 60) + i * 7),
                                           seconds=rng.randint(0, 59))
        if submitted < start_time:
            submitted = start_time + timedelta(minutes=i * 7)

        # —— 填行 ——
        row: dict[str, str] = {
            "提交答卷时间": submitted.strftime("%Y-%m-%d %H:%M:%S"),
            "所用时间": f"{duration // 60}分{duration % 60}秒" if duration >= 60 else f"{duration}秒",
            "来自IP": f"112.17.{rng.randint(0, 255)}.{rng.randint(1, 254)}",
        }
        for q in ordered:
            val = answers.get(q["id"])
            if q["type"] in MATRIX_TYPES:
                for row_def, col in zip(q["rows"], col_of_question[q["id"]]):
                    v = (val or {}).get(row_def["variable"])
                    row[col] = "" if v is None else str(v)
            else:
                col = col_of_question[q["id"]][0]
                if val is None:
                    row[col] = ""
                elif isinstance(val, list):
                    row[col] = ",".join(val)
                else:
                    row[col] = str(val)
        rows.append(row)

    summary = {
        "n": n, "seed": seed,
        "injected": {"straight_liner": len(straight_set), "speeder": len(speed_set),
                     "attention_fail": len(attfail_set), "logic_violator": len(logic_set)},
        "columns": len(columns),
    }
    return rows, columns, summary


def write_csv(rows: list[dict], columns: list[str], path: str | Path,
              encoding: str = "gb18030") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding=encoding, errors="replace") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft 合成数据生成器")
    parser.add_argument("--in", dest="src", required=True, help="survey.json 路径")
    parser.add_argument("--n", type=int, default=150)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", required=True, help="输出 CSV 路径")
    parser.add_argument("--encoding", default="gb18030",
                        help="输出编码，默认 gb18030（模拟问卷星 Excel 导出）")
    args = parser.parse_args()

    survey = load_survey(args.src)
    rows, columns, summary = generate(survey, args.n, args.seed)
    write_csv(rows, columns, args.out, args.encoding)
    summary["file"] = str(args.out)
    summary["encoding"] = args.encoding
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
