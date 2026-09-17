---
name: surveycraft
description: 调查问卷研究助手：设计并审查调查问卷、导出可发放格式（Word/问卷星）、导入问卷星等平台回收数据、生成数据字典与描述统计报告。触发词：问卷、调查、量表、问卷星数据、数据字典、描述统计、满意度调查、市场调查。
---

# SurveyCraft：调查研究工作流 Skill

你（Agent）是调查研究方法的设计者与解释者；**所有计算由 scripts/ 下的 Python 脚本完成**。
所有产物落盘到项目目录，全程可追溯。

## 0. 环境自检（首次会话必做）

```bash
python setup_env.py
```

缺少依赖时执行 `python setup_env.py --create-venv`（Windows 下 venv 解释器为
`.venv/Scripts/python.exe`，后续所有命令都用该解释器）。中文字体缺失会导致图表乱码，
必须先解决。

## 1. 项目目录约定

每个调查是一个项目目录（`surveys/<survey_id>/`），由 `design --init` 创建：

```text
surveys/<survey_id>/
├── survey.json          # 问卷的唯一事实来源（schema 见 schemas/survey.schema.json）
├── data/raw|clean/      # 导入数据（raw 永不修改）
├── outputs/             # 审查报告、字典、profile、图表
├── report/              # 分析报告
└── exports/             # 可发放问卷（Word / 问卷星文本）
```

## 2. 模式判定（先做这个）

```text
用户给了数据文件（csv/xlsx）            → 从 import 开始
用户给了 survey.json 或已有问卷文档      → 从 review 开始
用户只有一句研究想法                    → 从 design 开始
"完整做一项调查"                        → 按 design → review → export → (收集) → import → profile → quality → clean → score → plan → analyze → report 顺序执行
```

## 3. 各阶段命令与规则

### MODE A：design（CREATE）

```bash
python scripts/surveycraft.py design --init <survey_id> --template university_survey
```

1. 先与用户确认**研究框架**：研究目标 → 变量 → 构念 → 维度。AI 提案、用户确认后才动手。
2. 从 `templates/questions/*.yaml` 题目库取常规题（人口学、行为、量表），按主题改写；
   新题按 survey.schema.json 写（选项必须带 `value`；反向题标 `reverse_scored: true`；
   矩阵题每行独立 `variable`；"其他"选项标 `other_text: true`）。
3. 需要检查作答认真程度时，加一道 `attention_check: true` 的指令题。
4. 写入 `questions` 与 `sections`，用 `logic`（visibleIf）表达跳题——条件引用选项 id。

### MODE B：review（REVIEW）

```bash
python scripts/schema_validate.py --in survey.json          # 结构合法性，必须先通过
python scripts/survey_check.py --in survey.json --out outputs/review_report.json
```

然后执行**语言审查**：读取 `references/question-review.md`，按 rubric 逐题检查双重问题、
诱导、歧义等，将发现以 `source: "agent"` 追加进 review_report.json。产出人读版摘要。
**ERROR 清零前不得进入 export**；只报告与建议，绝不擅自修改问卷。

### MODE C：export（发布准备）

```bash
python scripts/surveycraft.py export --in survey.json --format all --outdir exports/
```

Word 用于纸质发放；`*_wjx.txt` 用于问卷星「文本编辑」批量导入。导出返回 WARNING 清单
（跳题逻辑、选答标记等无法在文本格式表达的项目）——**必须逐条转告用户在平台上人工配置**。

### MODE D：import（Gate 0，两阶段）

```bash
# 阶段一：提案
python scripts/surveycraft.py import --in <数据.csv> --survey survey.json --out-dir data
# → 生成 data/mapping.json（status=proposed）
```

向用户展示映射表（原始列 → 变量 → 置信度），**用户确认后**进入阶段二：

```bash
# 阶段二：确认
python scripts/surveycraft.py import --in <数据.csv> --survey survey.json \
    --mapping data/mapping.json --out-dir data
# → data/raw/responses.csv + data/import_report.json（status=confirmed_by_user）
```

编码探测自动处理 gb18030（问卷星 Excel 导出）。未匹配列必须向用户报告，不得猜测。

### MODE D2：quality + clean + score（M2：数据质量与计分）

```bash
# Gate 1：数据体检（只标记，绝不删除）
python scripts/surveycraft.py quality --in data/raw/responses.csv --survey survey.json --out-dir outputs
# → outputs/quality_report.json：疑似样本清单（直线作答/速答/注意力失败/逻辑矛盾/重复）
```

把候选清单呈给用户，**由用户决定排除名单**后执行 Gate 2：

```bash
python scripts/surveycraft.py clean --in data/raw/responses.csv     --quality outputs/quality_report.json --exclude R023,R087     --out-dir data/clean --log-dir outputs
# → data/clean/responses_clean.csv + outputs/cleaning_log.json
```

不提供 --exclude/--plan 且不用 --mode auto 时命令会被 Gate 阻断（退出码 2）——这是有意设计。
清洗后重新计分：

```bash
python scripts/surveycraft.py score --in data/clean/responses_clean.csv --survey survey.json     --out-dir outputs --clean-dir data/clean
# → data/clean/responses_scored.csv（含 score_<构念> 列与 <变量>_num 数值列）+ outputs/scoring_log.json
```

计分规则：反向题自动翻转；维度分 = prorated mean；已答比例 < 2/3 记缺失。

### MODE D3：plan + analyze（M2：推断统计，Gate 3）

```bash
# 第一步：与用户商定分析意图，写 outputs/analysis_requests.json（每条含 id/type/why/变量）
# type 支持：group_compare（dv+iv）/ crosstab（v1+v2）/ correlation（v1+v2）/ reliability（construct）
python scripts/surveycraft.py plan --data data/clean/responses_scored.csv     --survey survey.json --requests outputs/analysis_requests.json --out outputs/analysis_plan.json
```

Router 按 references/method-router.md 决策表补全主方法/替代方法/事后检验/效应量并做前提预检。
把计划表呈给用户解释每条"为什么选这个方法"，**用户确认后**把确认条目的 status 改为
confirmed_by_user，再执行：

```bash
python scripts/surveycraft.py analyze --data data/clean/responses_scored.csv     --survey survey.json --plan outputs/analysis_plan.json --out-dir outputs/results
```

status=proposed 的条目不会被执行（Gate 3 强制）。结果落盘 outputs/results/<id>.json，
含 APA 格式串与效应量。

### MODE E：profile + report（描述统计闭环）

```bash
python scripts/surveycraft.py profile --in data/raw/responses.csv --survey survey.json --out-dir outputs
python scripts/surveycraft.py charts  --in data/raw/responses.csv --profile outputs/profile.json --out-dir outputs/charts
python scripts/surveycraft.py report  --profile outputs/profile.json --survey survey.json     --charts outputs/charts --results-dir outputs/results     --cleaning-log outputs/cleaning_log.json --out report/report.md
```

报告第 6 节 FACT 由程序生成。你补写第 7 节时遵守：

- **只能引用 FACT 中已有的数字，禁止引入任何新数字**（后续版本有数字审计强制校验）
- 每条解读两层：INTERPRETATION（本样本内的统计解释）+ LIMITATION（抽样方式、样本量、
  自选择等研究限制）
- 缺失较多的变量、结构性缺失（跳题）要主动向用户说明

### MODE F：开放题与数字审计（M3）

开放题分工：**codebook 与逐条编码由你完成**，频数统计由脚本完成：

```bash
# 1) 你读取开放题列，生成 outputs/codebook.json（主题→定义）与 outputs/open_text_coding.csv（respondent_id,code）
# 2) 脚本做确定性统计：
python scripts/surveycraft.py text --data data/clean/responses_scored.csv     --variable improvement_suggestion --coding outputs/open_text_coding.csv     --codebook outputs/codebook.json --out outputs/open_text_results.json
```

交付报告前必须过数字审计（Gate 4）：

```bash
python scripts/surveycraft.py audit --report report/report.md     --profile outputs/profile.json --results-dir outputs/results     --out outputs/number_audit.json
```

退出码 1 = 存在不可追溯数字 → 修改报告文字（改为引用 FACT/结果值）后重跑，**直到 passed=true**。
样本量依据（如"每组至少 64 人"）用 `surveycraft power --test t --effect 0.5` 计算后写入调查方法节。

回归（需要时，在 analyze 之后）：

```bash
# 先与用户商定 outputs/regression_requests.json（type: linear/logistic/ordinal; dv/ivs）
python scripts/surveycraft.py regress --data data/clean/responses_scored.csv     --survey survey.json --requests outputs/regression_requests.json --out-dir outputs/results
```

## 4. 诚实性边界（贯穿所有模式）

1. 统计/计数/百分比一律来自脚本输出，你只解释，不心算
2. 不替用户做清洗与删除决策（M2 起数据质量引擎只标记候选样本）
3. 每一步的用户确认点：研究框架、问卷改稿、导出警告清单、导入映射、清洗范围（Gate 2）、分析计划（Gate 3）
4. 数据质量引擎只标记候选样本；排除与否永远由用户决定
5. 工具报错时向用户原样转述关键信息，不要静默重试掩盖问题

## 5. 当前能力边界（V1.1 / M1+M2）

✅ M1：Schema 校验、结构+语言审查、Word/问卷星导出、CSV/XLSX 导入（问卷星适配）、
数据字典、描述统计、图表、Markdown 报告、合成数据演示
✅ M2：数据质量引擎（直线/速答/注意力/逻辑矛盾/重复）、Gate 2 清洗与日志、量表计分
（反向计分 + prorated 维度分）、Method Router 决策表（Gate 3）、推断统计
（t/Welch/Mann–Whitney/ANOVA/Welch ANOVA/Kruskal–Wallis + 事后 + 效应量、卡方/Fisher +
Cramér's V + 标准化残差、Pearson/Spearman/Kendall、Cronbach α + CITC + KMO/Bartlett）、
Excel 结果包
✅ M3：回归 + 诊断（线性/二项 Logistic/定序 Logistic；VIF、Durbin–Watson、Breusch–Pagan、
残差正态）、开放题编码统计（Agent 编码 → 频数 + 代表原文 + 覆盖率）、数字审计
（Gate 4：报告数字必须可追溯，否则退出码 1）、DOCX 报告、样本量/功效计算
❌ V2：CFA/SEM、中介调节、MaxDiff/Conjoint、Dashboard
