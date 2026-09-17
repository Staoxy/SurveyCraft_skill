# SurveyCraft Skill 设计方案

版本：V1.1（修订版）
日期：2026-09-16
定位：面向大学课程、统计学课程、市场调查、社会调查、用户研究的 Agent-callable 调查研究 Skill

---

## 0. 修订说明（V1 → V1.1）

| # | 变更 | 原因 |
|---|------|------|
| 1 | 架构从"Python 应用"改为"Agent Skill 形态"：SKILL.md 为第一接口，references/ 放方法论文档，scripts/ 收敛为少量 JSON-in/JSON-out 脚本 | V1 的 40+ 模块化包结构是软件产品结构，Agent 无法直接使用 |
| 2 | 新增工件（artifact）体系：`analysis_plan.json`、`cleaning_log.json`、`import_report.json`、Gate 确认记录落盘 | "可追溯可复现"必须有载体 |
| 3 | Schema 补齐：选项 `value` 编码值、matrix 逐行展开变量、"其他____"子字段、`attention_check` 标记 | 真实平台数据一导入就会暴露的缺口 |
| 4 | 新增量表计分模块 `scale_score.py`（反向计分 + 维度分 + 缺失题规则） | 实际分析跑在维度分上，V1 缺了这一步 |
| 5 | 导出优先级调整：Word + 问卷星文本导入格式优先，SurveyJS 降为可选 | 课程场景的实际收集渠道 |
| 6 | 统计层补齐：事后检验、多重比较校正、效应量默认值表、APA 模板、数字审计、功效计算、缺失值策略 | 课程报告最容易被挑刺的地方 |
| 7 | 语言质量检查改为"代码查结构 + Agent 按 rubric 查语义"，输出统一 issue schema | 双重问题/诱导/歧义是语义任务，纯规则匹配太弱 |
| 8 | 范围裁剪：砍 Dashboard；Question Bank 降级为 YAML 模板文件夹；版本管理简化为 version + changelog；V1 按 M1–M3 里程碑排序 | 25 个必做模块一学期做不完，按课程节拍交付 |
| 9 | M1 里程碑显式补入 design（问卷生成）环节及对应验收标准 | 生成闭环的入口环节此前未列入里程碑，容易在实现时被跳过 |

---

## 1. 定位

SurveyCraft 不是"AI 自动生成问卷"，而是 **AI 辅助的完整调查研究工作流 Skill**：

```text
研究目标 → 问卷设计 → 问卷检查 → 发布格式 → 数据导入
→ 数据清洗 → 统计分析 → 可视化 → 研究结论 → 课程报告
```

核心竞争力不是"生成更多问题"，而是**让整份调查研究更规范**。第一质量标准不是生成速度，而是：

```text
Research Validity + Statistical Correctness + Traceability + Reproducibility
```

最终形态：用户只说"帮我调查大学生对 AI 学习工具的使用情况"，Skill 逐步产出研究方案、问卷、质量报告、Survey JSON、可发放问卷、数据质量报告、分析计划、统计结果、图表、研究发现、课程调查报告——**每一步都落盘保留**。

---

## 2. 职责分层与统一 Issue Schema

### 2.1 分工表

| 任务 | 执行者 | 说明 |
|------|--------|------|
| 问卷结构检查（选项重叠、量表不一致、跳题错误、必答缺失） | 代码 | 规则引擎，确定性 |
| 问卷语言检查（双重问题、诱导、歧义、否定句、术语） | **Agent 按 rubric** | 语义任务，结果写入统一 issue schema |
| 统计计算 | 代码 | scipy / pingouin / statsmodels |
| 统计方法选择 | 代码决策表 + Agent 解释 | Router 按决策表给候选，Agent 向用户解释"为什么" |
| 开放题 codebook 与编码 | Agent 生成/编码，映射表落盘 | 频数统计走代码 |
| 报告数字 | 模板从 result JSON 注入 | **禁止 LLM 手打任何数字** |
| 报告解释文字 | Agent | 引用 fact id，通过数字审计校验 |
| 各 Gate 决策 | **用户** | Skill 只建议，不擅自决定 |

### 2.2 统一 Issue Schema

问卷审查（无论来源是规则还是 Agent）输出同一种结构，报告生成器不区分来源：

```json
{
  "id": "ISS-001",
  "target": { "question_id": "Q07", "option_ids": [] },
  "severity": "WARNING",
  "category": "double_barreled",
  "source": "rule | agent",
  "rule_id": "OPT-003",
  "message": "该题可能同时测量两个概念：学习效率、学习兴趣",
  "suggestion": "考虑拆分为两题"
}
```

severity 取值：`ERROR`（必须修）/ `WARNING`（建议修）/ `INFO`。**审查器绝不直接修改问卷。**

---

## 3. 架构：Agent Skill 形态

### 3.1 SKILL.md 是第一接口

工作流逻辑（模式路由、Gate 规则、何时调用哪个脚本）写在 SKILL.md 里给 Agent 读；细节知识按需加载（progressive disclosure）：

- `SKILL.md` ≤ 500 行：frontmatter 触发描述 + 模式判定决策树 + 各阶段脚本调用方式 + Gate 规则
- `references/`：方法论文档（决策表、rubric、APA 规范、计分规则），Agent 在对应阶段才读
- frontmatter description 覆盖触发词：问卷、调查、量表、信效度、卡方、t 检验、方差分析、问卷星数据……

模式判定决策树（写入 SKILL.md）：

```text
用户输入 →
  含数据文件（csv/xlsx/sav）            → MODE ANALYZE（import 起步）
  含 survey.json 或问卷文档             → MODE REVIEW（review 起步）
  只有一句话研究想法                    → MODE CREATE（design 起步）
  "完整做一项调查"                      → MODE FULL（按流水线依次进入各阶段）
```

### 3.2 目录结构

```text
SurveyCraft_skill/
├── SKILL.md                    # 第一接口
├── README.md
├── docs/
│   └── DESIGN.md               # 本文档
├── references/                 # 给 Agent 读的方法论（按需加载）
│   ├── method-router.md        # 统计方法决策表
│   ├── question-review.md      # 语言质量检查 rubric
│   ├── data-quality.md         # 数据质量规则与阈值
│   ├── scale-scoring.md        # 量表计分与反向题规则
│   └── apa-reporting.md        # APA 中文报告规范
├── scripts/                    # 确定性执行单元（JSON in / JSON out）
│   ├── schema_validate.py
│   ├── survey_check.py         # 问卷结构检查（规则引擎）
│   ├── survey_export.py        # word / wjx-text / excel-template / surveyjs
│   ├── data_import.py          # 平台适配 + schema 对账
│   ├── data_profile.py         # 数据体检（Gate 1）
│   ├── data_clean.py           # 清洗执行（Gate 2）→ cleaning_log.json
│   ├── scale_score.py          # 反向计分 + 维度分
│   ├── method_router.py        # 决策表执行 → analysis_plan.json
│   ├── stats_run.py            # 按 plan 执行统计 → results/*.json
│   ├── result_audit.py         # 结果质检 + 报告数字审计
│   ├── charts.py               # 图表（中文字体内置）
│   ├── report_build.py         # Markdown / DOCX / XLSX 报告
│   ├── power.py                # 样本量 / 功效
│   └── make_synthetic.py       # 模拟数据生成器（测试夹具 + 教学演示）
├── schemas/
│   ├── survey.schema.json
│   ├── issue.schema.json
│   ├── analysis_plan.schema.json
│   └── analysis_result.schema.json
├── templates/
│   ├── surveys/                # 问卷模板（university_survey.yaml ...）
│   └── questions/              # 题目模板库（demographics.yaml / behavior.yaml ...）
├── setup_env.py                # venv + 依赖安装 + 环境自检
├── requirements.txt            # 版本锁定
└── tests/
    ├── fixtures/               # 各平台导出样例、教材例题数据
    ├── golden/                 # 统计模块金标准输出
    └── test_*.py
```

### 3.3 工件状态机：四模式 = 同一条流水线的不同入口

```text
survey.json ──→ review ──→ export ──→ [收集] ──→ import ──→ profile ──→ clean
                                                                      │
   report ←── result_audit ←── stats_run ←── method_router ←── scale_score
```

四个模式只是进入流水线的不同起点，实现为工件状态机，**不做四套流程**。每个项目目录固定布局：

```text
surveys/<survey_id>/
├── survey.json
├── data/
│   ├── raw/                  # 导入原始数据，永不修改
│   └── clean/                # 清洗后数据 + cleaning_log.json
├── outputs/
│   ├── review_report.json    # 问卷审查 issue 列表
│   ├── dictionary.csv        # 数据字典
│   ├── quality_report.json   # 数据体检（Gate 1）
│   ├── analysis_plan.json    # 分析计划（Gate 3）
│   ├── results/*.json        # 每个分析一份机器可读结果
│   └── charts/               # PNG + 每图的聚合数据 CSV
├── report/                   # Markdown / DOCX / XLSX
└── decisions.log             # Gate 确认记录：谁、何时、确认了什么
```

**可复现定义**：同 `analysis_plan.json` + 同数据重跑 `stats_run` 必得相同数字（涉及随机过程处记录 seed）。

### 3.4 环境引导

- `setup_env.py`：创建 .venv → 安装锁定版本的 requirements.txt → 自检（pandas/scipy/pingouin/pyreadstat 导入、matplotlib 中文字体探测），SKILL.md 第一步就是调用它
- matplotlib 中文字体内置配置：Windows 微软雅黑 / macOS PingFang SC / Linux Noto Sans CJK，`axes.unicode_minus=False`；否则第一张图就乱码

---

## 4. Survey Schema V1.1

问卷必须保存为标准 JSON Schema，这是后续导出、导入对账、自动分析的基础。

### 4.1 顶层结构

```json
{
  "survey": {
    "id": "AI_STUDENT_001",
    "title": "大学生AI学习工具使用调查",
    "version": "1.0.0",
    "changelog": [
      { "version": "1.0.0", "date": "2026-09-16", "note": "初始版本" }
    ]
  },
  "sections": [
    { "id": "S1", "title": "基本信息", "question_ids": ["Q01", "Q02"] }
  ],
  "questions": [],
  "logic": [],
  "metadata": {}
}
```

### 4.2 题目对象（V1.1 完整示例）

```json
{
  "id": "Q05",
  "type": "matrix_single",
  "text": "请评价你对以下AI功能的满意程度",
  "construct": "满意度",
  "variable_prefix": "sat",
  "rows": [
    { "id": "R1", "text": "答案生成质量", "variable": "sat_quality" },
    { "id": "R2", "text": "响应速度",     "variable": "sat_speed" }
  ],
  "scale": {
    "min": 1, "max": 5,
    "labels": ["非常不满意", "不满意", "一般", "满意", "非常满意"],
    "values": [1, 2, 3, 4, 5]
  },
  "reverse_scored": false,
  "required": true,
  "attention_check": false,
  "analysis_role": "dependent_variable"
}
```

单选题带"其他"子字段的示例：

```json
{
  "id": "Q03",
  "type": "single_choice",
  "text": "你主要通过什么渠道了解AI学习工具？",
  "options": [
    { "id": "A", "label": "同学推荐", "value": 1 },
    { "id": "B", "label": "社交媒体", "value": 2 },
    { "id": "C", "label": "其他",     "value": 99, "other_text": true }
  ],
  "required": true
}
```

关键修订点：

- **选项必须有 `value`**（编码值）：label ≠ value，Likert 不存 value 后面无法算分与比较
- **`reverse_scored`** 在题目层显式声明，反向题由系统记入计分规则，不靠用户记
- **`attention_check`** 标记注意力检查题，与数据质量检查联动（答错自动进疑似无效清单）
- **matrix 每行有独立 `variable`**：矩阵题必须逐行展开为独立变量才能分析
- **`other_text: true`** 声明"其他____"填空子字段，导入导出都要处理对应派生列
- **版本管理（简化形态）**：survey.json 内 `version` + `changelog` 字段。规则保留原设计：文字小改 → patch；增选项 → minor；题目含义大变 → 新 question id（否则历史数据无法比较）。不做独立版本管理系统。

### 4.3 展开 / 派生变量命名规则（全流程统一）

| 题型 | 派生列 | 示例 |
|------|--------|------|
| multiple_choice | 每个选项一列：`<qid>_<option_id>` | Q03_A, Q03_B |
| matrix | 每行一个变量：row.variable | sat_quality |
| other_text | `<qid>_<option_id>_other` | Q03_C_other |
| 量表维度分 | `score_<construct>` | score_satisfaction |

### 4.4 题型系统

V1 支持：

```text
single_choice    multiple_choice    text    textarea    number
integer          decimal            date    time        rating
likert           matrix_single      matrix_multiple    ranking
yes_no           nps
```

以后再增：slider、semantic_differential、constant_sum、image_choice、file_upload。

---

## 5. 问卷质量审查（两层）

### 5.1 结构检查（代码，`survey_check.py`）

确定性规则，逐条有 rule_id：

```text
选项互斥性（数值区间重叠，如 18-20 / 20-22）
选项穷尽性（缺"其他/无"类兜底项）
量表标签与 values 一致性、方向一致性
正/反向题混排标记
筛选题与跳题逻辑自洽（visibleIf 引用存在、无死路、无循环）
必答/非必答与逻辑冲突
敏感题位置（人口学后置）
选项数量与平衡性（量表是否缺少中间点/两端点）
```

### 5.2 语言检查（Agent 按 rubric，见 references/question-review.md）

rubric 检查项：双重问题、诱导性措辞、模糊限定词、复杂长句、否定句与双重否定、术语难度、题目与 construct 是否对应。Agent 逐题输出统一 issue schema（source: "agent"），并给出改写建议。

输出：review_report.json（issue 列表）+ 人读版审查报告。流程原则不变：**只报告与建议，绝不偷偷修改。**

---

## 6. 量表计分（`scale_score.py`，新增模块）

实际分析几乎都跑在维度分上而非单题上，这一步必须显式化：

- **反向计分**：`new = (min + max) − old`，只对 `reverse_scored: true` 的题执行
- **维度分**：`score_<construct>` = 该构念全部题项的**已答均分**（prorated mean）
- **缺失题规则**：已答比例 ≥ 2/3（可在 clean plan 配置）才计分，否则记缺失
- **计分日志**：每个构念的题项清单、反向题、缺失处理数量，写入 outputs 供报告"调查方法"节引用
- **与信度分析衔接**：Cronbach's α / McDonald's ω 在计分前对原始题项计算；α 低时系统给出"当前指标 + 删除该题后的变化 + 理论影响"，**由研究者决定是否删题**，不设"α<0.7 必删"的硬规则

---

## 7. 逻辑引擎

必须支持：`visibleIf`、skip、display logic、termination、randomization、carry forward。逻辑保存为结构化 JSON（与题目内容解耦）。导出到具体平台时，**无法表达的逻辑必须输出 WARNING 清单**，提示人工在平台上配置。

---

## 8. 题库与模板（降级形态）

V1 不做题目 ID 管理系统，题库就是 YAML 模板文件夹（`templates/questions/`），按主题组织（demographics / behavior / satisfaction / technology / education），每题带 construct、测量层次、量表定义、来源标注。问卷模板在 `templates/surveys/`。同题跨调查复用靠"复制 YAML + 改 id"，够用且零维护成本。

---

## 9. 导出引擎（收集出口优先级）

| 优先级 | 格式 | 用途 | 里程碑 |
|--------|------|------|--------|
| 1 | **Word (.docx)** | 纸质发放、提交审阅 | M1 |
| 2 | **问卷星文本导入格式 (.txt)** | 粘贴进问卷星"文本编辑"批量建卷 | M1 |
| 3 | Excel 数据录入模板 | 无线上渠道时人工录入，列名与 schema 对齐 | M2 |
| 4 | SurveyJS JSON / 静态 HTML | 网页问卷 | M3 可选 |

SurveyJS 仅作格式兼容导出参考（其 JSON-first、问题/逻辑/验证分离的设计值得吸收），不作为运行时依赖。

---

## 10. 数据导入引擎

### 10.1 平台适配器

```text
wjx      问卷星：多选题展开列、矩阵合并列（"1|2|3"）、前两行题目/选项文本、百分比列
tencent  腾讯问卷
spss     .sav（pyreadstat，读取 value labels）
generic  CSV / XLSX / Parquet
```

**编码处理（必做）**：Excel 导出的中文 CSV 是 GBK/GB18030，按 utf-8 → utf-8-sig → gb18030 顺序探测。

### 10.2 导入流程

```text
探测平台 → 解析 → 与 survey.json 对账（列 ↔ 变量匹配，列出未匹配列）
→ 映射表人工确认（Gate 0：raw column → 推断变量 → 题型 → 用户逐项确认）
→ 落盘 data/raw/ + import_report.json
```

无 survey.json 时自动推断变量类型并同样走映射确认。**自动识别之后必须经用户确认才能进入分析。**

### 10.3 数据字典（自动生成）

| Variable | Label | Type | Level | Missing | Unique | Min | Max | Mean |
|----------|-------|------|-------|--------:|-------:|----:|----:|-----:|
| Q01 | 性别 | categorical | nominal | 0 | 2 | | | |
| Q02 | 年级 | categorical | ordinal | 2 | 4 | | | |
| score_satisfaction | 满意度得分 | numeric | interval | 1 | | 1.0 | 5.0 | 3.42 |

这是 Method Router 与分析引擎的基础。

---

## 11. 数据质量引擎与清洗

### 11.1 检查规则（阈值具体化，见 references/data-quality.md）

| 规则 | 阈值定义 |
|------|----------|
| 直线作答 | 同一量表 block（≥5 连续题）SD = 0 |
| 速答 | duration < 中位数时长 × 0.33；无 duration 数据 → 标记"无法评估" |
| 注意力检查失败 | 任一 `attention_check: true` 题答错 |
| 全题同选 | 单选题全卷同一选项（无量表 block 结构时） |
| 重复样本 | 有唯一 ID 用 ID；无则全响应哈希（报告中注明局限） |
| 逻辑矛盾 | 违反跳题逻辑（如 Q01=没用过 但 Q06 有作答） |
| 异常文本 | 开放题乱码/超短/与题意无关 |

### 11.2 原则与工件

- **只标记"疑似低质量"，默认不删**：输出候选清单 → 用户确认 → 执行
- 清洗动作全部写入 **`cleaning_log.json`**：

```json
{
  "survey_id": "AI_STUDENT_001",
  "data_file": "raw/responses_wjx_20261001.csv",
  "policy": { "missing": "prorated_mean", "min_answered_ratio": 0.67 },
  "actions": [
    { "respondent": "R023", "rule": "attention_check_failed", "detail": "Q12 答错", "action": "flagged" },
    { "respondent": "R087", "rule": "speeder", "detail": "46s < 0.33×median(139s)", "action": "excluded_by_user" }
  ]
}
```

- **缺失值策略**进清洗计划并写入 policy：listwise / pairwise / prorated mean（量表计分），报告"数据清洗"章节直接从 cleaning_log 生成

---

## 12. Method Router（决策表驱动）

### 12.1 形态

决策规则写成**可审计的决策表**（references/method-router.md），代码（method_router.py）执行它，Agent 向用户解释。路由输入：研究问题 + 变量测量层次 + 样本量 + 设计类型。

### 12.2 决策表（节选）

| DV | IV / 设计 | 前提判定 | 主方法 | 替代方法 | 事后检验 | 效应量 |
|----|-----------|----------|--------|----------|----------|--------|
| 数值，近正态 | 二分类组间 | Levene p≥.05 | 独立样本 t | Welch t | — | Cohen's d |
| 数值 | 二分类组间 | 方差不齐 | Welch t | Mann–Whitney U | — | r = Z/√N |
| 数值 | ≥3 组 | 方差齐 + 近正态 | One-way ANOVA | Kruskal–Wallis | Tukey HSD | η² |
| 数值 | ≥3 组 | 方差不齐 | Welch ANOVA | Kruskal–Wallis | Games–Howell | ω² |
| 序数（单题 Likert）或严重偏态 | 二分类 | — | Mann–Whitney U | Welch t | — | r |
| 序数 | ≥3 组 | — | Kruskal–Wallis | Welch ANOVA | Dunn（Holm 校正） | ε² |
| 分类 | 分类 | 期望频数≥5 且无 <1 | 卡方 | Fisher 精确 | 标准化残差 | Cramér's V |
| 数值 | 数值 | — | Pearson | Spearman | — | r |
| 序数 | 序数 | — | Spearman | Kendall τ | — | r_s |
| 配对 / 前后测 | — | 差值近正态 | 配对 t | Wilcoxon | — | d_z |

### 12.3 前提阈值（显式写入决策表）

- 每组 n < 30，或 |偏度| > 2，或（n≥50 时）Shapiro–Wilk p < .01 → 视为不满足"近正态"
- 小样本 Shapiro 对轻微偏离过敏，**不单独**作为正态判据
- 组容量不等时默认 Welch 系

### 12.4 分析计划三件套

Router 输出的每个分析必含：**主方法 + 前提不满足时的替代方法 + 事后检验方案**，p 值默认 Holm 校正（报告同时给校正前后）。

`analysis_plan.json` 示例：

```json
{
  "analyses": [
    {
      "id": "A03",
      "why": "检验不同年级满意度是否存在差异（研究目标5）",
      "dv": { "variable": "score_satisfaction", "level": "numeric" },
      "iv":  { "variable": "grade", "groups": 4, "level": "ordinal" },
      "method": "welch_anova",
      "fallback": "kruskal_wallis",
      "posthoc": "games_howell",
      "p_adjust": "holm",
      "effect_size": "omega_squared",
      "assumptions_checked": ["n_per_group>=30", "levene"],
      "status": "confirmed_by_user"
    }
  ]
}
```

---

## 13. 统计能力

| 类别 | 内容 | 里程碑 |
|------|------|--------|
| 描述统计 | 频数、百分比、均值、中位数、SD、四分位、极值 | M1 |
| 交叉分析 | Crosstab（Row%/Col%/期望频数）、卡方、Fisher、Cramér's V、标准化残差 | M2 |
| 差异分析 | t / Welch t / 配对 t / ANOVA / Welch ANOVA / Mann–Whitney / Wilcoxon / Kruskal–Wallis，含事后与效应量 | M2 |
| 相关 | Pearson / Spearman / Kendall（按测量层次选择，不全跑 Pearson） | M2 |
| 信度 | Cronbach's α、McDonald's ω、item-total、α if deleted；split-half 视场景 | M2 |
| 效度 | KMO、Bartlett、EFA（factor_analyzer）；**EFA 标记可选** | M2 可选 |
| 回归 | Linear / Logistic / Ordinal Logistic，输出系数、SE、CI、p、R²、Adj R²、VIF、残差诊断 | M3 |
| 功效 | 样本量估算 / achieved power（statsmodels），写入报告"调查方法"节 | M3 |
| 开放题 | 见 §14 | M3 |

V2 专题（不在 V1）：CFA、SEM、复杂中介调节、MaxDiff、Conjoint、TURF、Kano、PSM。

---

## 14. 开放题分析

```text
Text Cleaning（代码）
→ Codebook（Agent 生成：主题、定义、示例）
→ Coding（Agent 逐条编码，映射表落盘 open_text_coding.csv：response_id → code）
→ Frequency（代码统计）
→ Representative Themes（报告附代表性原文引用）
```

原则：**AI 生成的主题必须保留原始答案映射**，每个主题可回溯到原始答案，避免"凭感觉总结"。

---

## 15. Analysis Gates（工件化）

```text
Gate 0  导入映射确认（import）          → import_report.json
Gate 1  数据体检报告（profile）          → quality_report.json      → 用户确认清洗范围
Gate 2  清洗计划含缺失值策略（clean）     → cleaning_log.json        → 用户确认
Gate 3  分析计划（plan）                 → analysis_plan.json       → 用户确认
Gate 4  结果自动质检 + 数字审计（report） → results/*.json + audit
```

模式：`manual`（每 Gate 停）/ `semi`（Gate 1、3 停）/ `auto`（不停，全部落盘供事后审查）。所有确认写入 decisions.log。

---

## 16. Result Validator 与数字审计（防幻觉核心）

1. **所有统计结果由程序生成**，Agent 只负责解释——绝不让 LLM 自己算 t 值
2. 每个分析落一份机器可读结果 `results/<analysis_id>.json`
3. **APA 模板**（reporting 内置，规范见 references/apa-reporting.md）：result JSON → `t(173) = 2.51, p = .013, Cohen's d = 0.38, 95% CI [0.08, 0.68]`；p<.001 写法、斜体统计符号等中文报告惯例固定成模板
4. **数字审计**：report_build 出稿前，result_audit 提取正文所有数字（统计量、p、百分比、N），逐一在 results/*.json 与图表聚合 CSV 中查找；**找不到来源的数字 → ERROR，阻止出报告**

---

## 17. 可视化

V1 图表：条形、堆叠条形、饼图、直方图、箱线、散点、热力、雷达、Likert Diverging Bar。按变量类型自动推荐图型。规则：

- 中文字体配置内置（见 §3.4）
- 每图输出 PNG（300dpi）+ **图表聚合数据 CSV**（供数字审计与复现）
- 不做交互式 Dashboard（已裁剪）

---

## 18. 研究结论与报告

### 18.1 结论三层（每个发现必含）

```text
FACT：          样本中 68.4% 的受访者表示使用过 AI 学习工具。
INTERPRETATION：在本次样本中，AI 学习工具具有较高使用渗透率。
LIMITATION：    本调查采用便利抽样，该比例不能直接视为总体比例。
```

### 18.2 报告结构（Markdown / DOCX / XLSX）

研究背景 → 研究目的 → 调查方法（含样本量依据）→ 样本描述 → 数据质量（源自 cleaning_log）→ 描述统计 → 差异/相关/回归 → 信效度 → 开放题 → 核心发现 → 研究局限 → 附录。

数字全部模板注入（§16），解释文字由 Agent 撰写并引用 fact id。

### 18.3 Excel 结果包（survey_analysis.xlsx）

README / Data Dictionary / Sample Profile / Frequency / Crosstab / Tests / Reliability / Correlation / Regression / Open Text / Charts / Quality Report。

---

## 19. 技术栈

| 层 | 选型 | 说明 |
|----|------|------|
| 主统计引擎 | **pingouin** | t/ANOVA/Welch/非参数/事后/效应量/信度现成，省大量手写 |
| 补充 | scipy, statsmodels | 回归诊断、ordinal logistic、功效 |
| 数据 | pandas, numpy, pyreadstat, openpyxl | |
| EFA | factor_analyzer | 可选 |
| 可视化 | matplotlib | 中文字体内置；plotly 不进 V1 |
| 报告 | python-docx, openpyxl | |

依赖版本锁定在 requirements.txt，由 setup_env.py 安装。

---

## 20. 测试策略

- **Golden tests 优先打统计模块**：教材例题数据集 + 已知输出，断言统计量与 p 到 1e-6
- **Schema round-trip**：load → export → load 幂等
- **平台夹具**：问卷星 / 腾讯问卷 / SPSS 真实导出样例文件
- **模拟数据生成器**（make_synthetic.py）：按 schema 生成 N 份模拟数据，可注入直线作答、速答、注意力失败样本——既是测试夹具，也是收数据前的全流程演示工具和教学功能

---

## 21. 许可与二次开发原则

**只学思路，不抄代码**（设计思路不受版权保护，最干净）。若确需复用：

- MIT 项目（survey_data_analysis 等）可复用，NOTICE 署名
- SurveyJS 只用 MIT 的 survey-core / survey-library，且优先做格式兼容导出而非运行时依赖
- GPL（LimeSurvey）代码绝不混入，只学设计
- StackExchange/Survey（Apache-2.0）：学稳定 question ID、版本化、survey flow 的思路

---

## 22. CLI 接口（修订）

Agent 与用户统一走命令行，JSON 输出 + `--out` 落盘，`--mode manual|semi|auto`：

```text
surveycraft design    # 研究设计：目标 → 变量 → 构念 → 问卷结构（AI 提案，用户确认）
surveycraft review    # 问卷审查：结构检查（代码）+ 语言检查（Agent）→ review_report.json
surveycraft export    # 导出：word / wjx-text / excel-template / surveyjs
surveycraft import    # 数据导入：平台适配 + schema 对账 + 映射确认（Gate 0）
surveycraft profile   # 数据体检（Gate 1）
surveycraft clean     # 清洗（Gate 2）→ cleaning_log.json
surveycraft plan      # 分析计划（Gate 3）→ analysis_plan.json
surveycraft analyze   # 按 plan 执行统计 → results/*.json（含 scale_score 前置）
surveycraft report    # Gate 4 + 报告生成（Markdown/DOCX/XLSX + 数字审计）
```

---

## 23. V1 范围与里程碑

### 砍掉 / 降级（相对 V1 草案）

- 砍掉：Dashboard
- 降级：Question Bank → YAML 模板文件夹；Versioning → survey.json version + changelog；EFA → 可选

### M1（期中前可用）

design 问卷生成流程（Agent 按 SKILL.md 指令起草 + 题目模板库兜底，无重型代码）｜ Schema + 校验器 ｜ 结构化 review ｜ Word + 问卷星文本导出 ｜ CSV/XLSX 导入（GBK + 映射确认）｜ 数据字典 ｜ 描述统计 + 基础图表

**验收标准**：① 从一句研究想法出发，经 design → review → export 产出可发放的 Word / 问卷星文本问卷，review 报告无未处理 ERROR；② 用一份真实问卷星导出数据，从导入到描述统计 + 图表 + Markdown 报告全流程跑通。

### M2

数据质量 + cleaning_log ｜ 量表计分 ｜ Method Router + 决策表 ｜ 推断检验（事后 + 效应量 + CI）｜ 信度（KMO/Bartlett 必做，EFA 可选）｜ Excel 结果包

**验收标准**：一份含量表与多选题的问卷数据，semi 模式从 Gate 1 到 Excel 结果包自动走完。

### M3

回归 + 诊断 ｜ 开放题 codebook 流程 ｜ DOCX/Markdown 报告 + APA 模板 + 数字审计 ｜ power.py ｜ 问卷星适配完善 ｜ SurveyJS 导出（可选）

**验收标准**：课程报告 DOCX 一键生成且数字审计零 ERROR。

---

## 24. 质量标准（每次分析必须能回答）

```text
为什么做这个分析？→ 用了什么变量？→ 用什么方法？→ 为什么选这个方法？
→ 前提是否满足？→ 结果是什么？→ 结果是否可靠？→ 有什么限制？
```

这八个问题分别由 analysis_plan.json（why/variables/method/fallback）、assumptions 检查、results JSON、LIMITATION 层落盘回答。

---

## 25. 外部关系

- **DocBeauty**：SurveyCraft 产出 Markdown/DOCX 报告工件，DocBeauty 消费做排版 → "从问卷到课程报告"工作流。接口 = 报告文件本身，松耦合。
- **StatLab**：**不合并开发**。SurveyCraft 输出 Method Request（analysis_plan.json 的分析方法字段）保持接口兼容，未来可路由到 StatLab 的 Method Router + Statistical Engine。两项目独立演进。

---

## 26. 完整课程场景

> "我这学期统计调查课要研究大学生使用 AI 学习工具的情况，帮我做一份调查。"

```text
design（研究计划，用户确认）→ 问卷草稿 → review（质量报告，用户改稿）
→ export（Word + 问卷星文本）→ [发放收集]
→ import（对账 + 映射确认）→ profile（Gate 1）→ clean（Gate 2）
→ scale_score → plan（Gate 3，含三件套）→ analyze（统计 + 图表）
→ report（Gate 4 数字审计 → 课程报告 DOCX/XLSX）
```

每一步工件落盘于 surveys/<survey_id>/，全程可追溯、可复现、可交给老师检查。

---

## 27. 最终定位

SurveyCraft 的核心不是"一个会生成问卷的 AI"，而是 **一个 Agent 可以调用的调查研究方法引擎**：

```text
Research Understanding → Questionnaire Design → Survey Quality → Data Quality
→ Method Router → Statistical Engine → Result Validation → Visualization
→ Research Interpretation → Report
```

与研究设计相关的判断交给 Agent，计算与校验交给代码，关键决策交给用户，一切落盘可追溯——这条边界就是本 Skill 的全部设计。
