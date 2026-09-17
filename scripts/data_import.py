"""SurveyCraft 数据导入引擎（M1：CSV/XLSX + 问卷星适配 + 两阶段映射确认）。

用法（Gate 0，DESIGN.md §10 / §15）：
    # 阶段一：探测 + 解析 + 生成映射提案
    python scripts/data_import.py --in responses.csv --survey survey.json --out-dir data
    # 阶段二：人工核对/编辑 mapping.json 后确认应用
    python scripts/data_import.py --in responses.csv --survey survey.json \
        --mapping data/mapping.json --out-dir data

流程：编码探测（utf-8 → utf-8-sig → gb18030）→ 平台识别 → 解析列
→ 与 survey.json 对账（题号 > 题干 > 未匹配）→ 生成映射提案（status=proposed）
→ 用户确认（status=confirmed_by_user）→ 落盘标准化数据 data/raw/responses.csv
+ import_report.json。

原始数据永不修改；多选题展开为 <QID>_<OPTION> 二值列；"其他____"拆出 _other 列。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    MATRIX_TYPES, derived_variable, load_survey, ordered_questions,
    other_variable, save_json,
)

HEADER_RE = re.compile(r"^\s*(\d+)\s*[、.．]\s*(.+?)\[([^\[\]]+)\](?:\.(.+))?$")
META_COLUMNS = {
    "提交答卷时间": "submitted_at",
    "所用时间": "duration_raw",
    "来自IP": "ip",
}


# ---------------------------------------------------------------- 读取与编码探测

def read_table(path: str | Path) -> tuple[pd.DataFrame, str]:
    """按探测编码读取 CSV/XLSX，返回 (df, 使用的编码/格式)。"""
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str), "xlsx"
    last_error = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return pd.read_csv(path, dtype=str, encoding=enc, keep_default_na=False), enc
        except (UnicodeDecodeError, UnicodeError) as exc:
            last_error = exc
    raise ValueError(f"无法识别文件编码（已尝试 utf-8-sig/utf-8/gb18030）: {last_error}")


def detect_platform(df: pd.DataFrame) -> str:
    hits = 0
    for col in df.columns:
        if HEADER_RE.match(str(col)):
            hits += 1
    meta_hit = any(c in META_COLUMNS for c in df.columns)
    if (hits >= max(2, len(df.columns) // 3)) or (hits >= 2 and meta_hit):
        return "wjx"
    return "generic"


# ---------------------------------------------------------------- 解析辅助

def parse_duration(raw: str) -> float | None:
    """'88秒' → 88.0；'2分3秒' → 123.0；'1:23' → 83.0。"""
    if not raw or pd.isna(raw):
        return None
    s = str(raw).strip()
    m = re.fullmatch(r"(?:(\d+)\s*分)?\s*(\d+)\s*秒?", s)
    if m:
        return float(int(m.group(1) or 0) * 60 + int(m.group(2)))
    m = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", s)
    if m:
        parts = [int(g) for g in m.groups() if g is not None]
        return float(sum(v * 60 ** i for i, v in enumerate(reversed(parts))))
    return None


def parse_header(col: str) -> dict | None:
    m = HEADER_RE.match(str(col))
    if not m:
        return None
    return {
        "qnum": int(m.group(1)),
        "stem": m.group(2).strip(),
        "qtype": m.group(3).strip(),
        "sub": m.group(4).strip() if m.group(4) else None,
    }


def split_multi(value: str) -> list[str]:
    try:
        if value is None or pd.isna(value):
            return []
    except (TypeError, ValueError):
        pass
    if not value:
        return []
    return [p.strip() for p in re.split(r"[,，;；]", str(value)) if p.strip()]


def split_other(label: str) -> tuple[str, str | None]:
    """'其他：B站' → ('其他', 'B站')；'其他' → ('其他', None)。"""
    m = re.fullmatch(r"(其他|其它)\s*[:：]?\s*(.*)", str(label).strip())
    if m:
        return m.group(1), (m.group(2) or None)
    return str(label), None


# ---------------------------------------------------------------- 映射提案

def build_mapping(df: pd.DataFrame, survey: dict | None, platform: str,
                  encoding: str, source: str) -> dict:
    """生成列 → 目标变量的映射提案（Gate 0 阶段一）。"""
    ordered, _ = ordered_questions(survey) if survey else ([], [])
    q_by_num = {no: q for no, q in enumerate(ordered, start=1)}
    q_by_stem = {re.sub(r"\s+", "", q["text"]): q for q in ordered}

    entries: list[dict] = []
    unmatched: list[str] = []
    for col in df.columns:
        if col in META_COLUMNS:
            entries.append({
                "column": str(col), "target": META_COLUMNS[col], "role": "meta",
                "confidence": "high", "method": "meta_name",
            })
            continue
        parsed = parse_header(col) if platform == "wjx" else None
        if parsed is None:
            unmatched.append(str(col))
            entries.append({
                "column": str(col), "target": "", "role": "unknown",
                "confidence": "low", "method": "none",
            })
            continue
        q = q_by_num.get(parsed["qnum"])
        method = "question_number"
        if q is None:
            q = q_by_stem.get(re.sub(r"\s+", "", parsed["stem"]))
            method = "stem_text"
        if q is None:
            unmatched.append(str(col))
            entries.append({
                "column": str(col), "target": "", "role": "unknown",
                "confidence": "low", "method": "none", "stem": parsed["stem"],
            })
            continue
        entry = {
            "column": str(col), "question_id": q["id"], "stem": parsed["stem"],
            "qtype": parsed["qtype"], "confidence": "high", "method": method,
        }
        if q["type"] in MATRIX_TYPES and parsed["sub"]:
            row = next((r for r in q["rows"] if r["text"] == parsed["sub"]), None)
            if row:
                entry.update({"target": row["variable"], "role": "matrix_row"})
            else:
                entry.update({"target": "", "role": "matrix_row_unmatched",
                              "confidence": "low", "method": "none"})
        elif q["type"] == "multiple_choice" and parsed["sub"]:
            # 二值展开列（平台按选项分列导出的变体）
            opt = next((o for o in q["options"] if o["label"] == parsed["sub"]), None)
            entry.update({
                "target": derived_variable(q["id"], opt["id"]) if opt else "",
                "role": "multi_binary",
                "confidence": "high" if opt else "low",
            })
        elif q["type"] == "multiple_choice":
            entry.update({"target": q["id"], "role": "multi_joined"})
        else:
            entry.update({"target": q.get("variable", q["id"].lower()),
                          "role": "single" if q["type"] != "textarea" else "text"})
        entries.append(entry)

    return {
        "status": "proposed",
        "source_file": str(source),
        "platform": platform,
        "encoding": encoding,
        "n_rows": int(len(df)),
        "n_columns": int(len(df.columns)),
        "survey_id": survey["survey"]["id"] if survey else None,
        "matched": sum(1 for e in entries if e["confidence"] == "high"),
        "unmatched_columns": unmatched,
        "columns": entries,
    }


# ---------------------------------------------------------------- 标准化落地

def apply_mapping(df: pd.DataFrame, survey: dict | None, mapping: dict) -> tuple[pd.DataFrame, list[dict]]:
    """按（已确认的）映射把原始列转为标准化数据。返回 (标准化 df, 警告)。"""
    warnings: list[str] = []
    out = pd.DataFrame(index=range(len(df)))
    out["respondent_id"] = [f"R{i + 1:03d}" for i in range(len(df))]

    multi_joined: dict[str, dict] = {}
    for entry in mapping["columns"]:
        col, role, target = entry["column"], entry["role"], entry.get("target", "")
        if role == "unknown":
            warnings.append(f"列「{col}」未匹配到任何变量，已忽略")
            continue
        if role == "meta":
            if target == "duration_raw":
                out["duration_sec"] = [parse_duration(v) for v in df[col]]
                out["duration_raw"] = df[col]
            elif target == "submitted_at":
                out["submitted_at"] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                out[target] = df[col]
            continue

        q = _question(survey, entry.get("question_id")) if survey else None
        if role == "single" or role == "text":
            out[target] = df[col].replace("", pd.NA)
        elif role == "multi_joined":
            multi_joined[col] = {"target": target, "question": q, "entry": entry}
        elif role == "matrix_row":
            out[target] = df[col].replace("", pd.NA)
        elif role == "multi_binary":
            out[target] = (df[col].replace("", pd.NA).notna() & (df[col] != "")).astype(int)
        elif role in {"matrix_row_unmatched"}:
            warnings.append(f"列「{col}」为矩阵行但无法匹配行变量，已忽略")

    # 多选题（一格多值）展开
    for col, info in multi_joined.items():
        q = info["question"]
        picked = df[col].map(split_multi)
        has_answer = picked.map(lambda labels: len(labels) > 0)
        if q is not None:
            label_to_opt = {o["label"]: o for o in q.get("options", [])}
            for o in q.get("options", []):
                binary = picked.map(
                    lambda labels, o=o: int(any(
                        (split_other(l)[0] in {o["label"], "其他", "其它"} if o.get("other_text")
                         else l == o["label"]) for l in labels)))
                # 跳题（整格为空）的二值列必须是缺失而非 0，否则质检会把跳题误判为作答
                binary[~has_answer] = pd.NA
                out[derived_variable(q["id"], o["id"])] = binary.astype("Int64")
                if o.get("other_text"):
                    out[other_variable(q["id"], o["id"])] = picked.map(
                        lambda labels, o=o: next(
                            (split_other(l)[1] for l in labels
                             if split_other(l)[0] in {"其他", "其它"} and split_other(l)[1]), None))
            unmatched_labels = {l for labels in picked for l in labels
                                if l not in label_to_opt and split_other(l)[0] not in {"其他", "其它"}}
            if unmatched_labels:
                warnings.append(
                    f"{q['id']} 存在未登记的选项文本：{sorted(unmatched_labels)[:5]}…"
                    if len(unmatched_labels) > 5 else
                    f"{q['id']} 存在未登记的选项文本：{sorted(unmatched_labels)}")
        else:
            # 无 survey.json：按观测值展开
            observed = sorted({l for labels in picked for l in labels})
            for i, label in enumerate(observed, start=1):
                col_out = picked.map(lambda labels, label=label: int(label in labels)).astype("Int64")
                col_out[~has_answer] = pd.NA
                out[f"{info['target'] or col}_{i}"] = col_out

    return out, warnings


def _question(survey: dict, qid: str | None) -> dict | None:
    if not qid:
        return None
    return next((q for q in survey.get("questions", []) if q["id"] == qid), None)


# ---------------------------------------------------------------- 主流程

def run_import(source: str, survey_path: str | None, mapping_path: str | None,
               out_dir: str) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    df, encoding = read_table(source)
    platform = detect_platform(df)
    survey = load_survey(survey_path) if survey_path else None

    if mapping_path:
        mapping = json.loads(Path(mapping_path).read_text(encoding="utf-8"))
        mapping["status"] = "confirmed_by_user"
    else:
        mapping = build_mapping(df, survey, platform, encoding, source)
        save_json(mapping, out_dir / "mapping.json")

    warnings: list[str] = []
    report: dict = {
        "tool": "data_import",
        "source_file": str(source),
        "platform": platform,
        "encoding": encoding,
        "survey_id": survey["survey"]["id"] if survey else None,
        "n_rows": int(len(df)),
        "mapping_status": mapping["status"],
        "matched": mapping.get("matched"),
        "unmatched_columns": mapping.get("unmatched_columns", []),
        "warnings": warnings,
    }

    if mapping["status"] == "proposed":
        report["message"] = (
            "映射提案已生成（mapping.json）。请人工核对后重新运行并指定 --mapping 以确认导入（Gate 0）")
    else:
        standardized, apply_warnings = apply_mapping(df, survey, mapping)
        warnings.extend(apply_warnings)
        out_file = out_dir / "raw" / "responses.csv"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        standardized.to_csv(out_file, index=False, encoding="utf-8-sig")
        report["data_file"] = str(out_file)
        report["n_variables"] = int(standardized.shape[1])
        report["message"] = "导入完成（映射已确认）"

    save_json(report, out_dir / "import_report.json")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft 数据导入")
    parser.add_argument("--in", dest="src", required=True, help="原始数据 CSV/XLSX")
    parser.add_argument("--survey", help="survey.json 路径（用于对账，可选）")
    parser.add_argument("--mapping", help="已核对的 mapping.json（提供则视为确认，Gate 0 第二阶段）")
    parser.add_argument("--out-dir", dest="out_dir", default="data", help="输出目录")
    args = parser.parse_args()

    report = run_import(args.src, args.survey, args.mapping, args.out_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
