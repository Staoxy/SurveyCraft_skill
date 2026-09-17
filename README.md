# SurveyCraft Skill

面向大学课程与统计学课程的 Agent-callable 调查研究 Skill：从研究目标到问卷设计、质量检查、数据导入清洗、统计分析，再到课程报告的完整工作流。

- 设计方案：[DESIGN.md](DESIGN.md)（V1.1）
- Agent 使用说明：[SKILL.md](SKILL.md)
- 状态：**M1+M2 已实现并验收；M3 已实现**（回归/开放题/数字审计/DOCX/功效）

## 当前能力（M1）

```text
design  →  review  →  export  →  (发放收集)  →  import  →  profile  →  charts  →  report
研究设计    质量审查    发布格式       回收数据        数据体检     图表        Markdown 报告
```

- 问卷 Schema（JSON Schema 校验）+ 结构化审查规则引擎（选项重叠/量表一致性/跳题逻辑/变量冲突…）
- 语言质量审查 rubric（Agent 按 `references/question-review.md` 执行）
- 导出：Word（纸质）+ 问卷星文本导入格式（含不可表达逻辑的 WARNING 清单）
- 导入：编码自动探测（gb18030 等）+ 问卷星格式适配 + 两阶段列映射确认（Gate 0）
- 数据字典 + 描述统计 + 中文字体图表（PNG 300dpi + 聚合数据 CSV）
- Markdown + DOCX 报告：核心发现（FACT）程序生成；数字审计（Gate 4）强制报告数字可追溯
- 推断统计：组间比较（t/Welch/ANOVA/Welch ANOVA/Kruskal–Wallis + 事后 + 效应量）、卡方/Fisher、相关、信度（α/CITC/KMO/Bartlett）
- 回归：线性 / 二项 Logistic / 定序 Logistic，含 VIF、Durbin–Watson、Breusch–Pagan 诊断
- 功效计算：t / ANOVA / 卡方 所需样本量或 achieved power
- 合成数据生成器：模拟问卷星导出（含直线作答/速答/注意力失败/逻辑矛盾样本），用于测试与教学

## 快速开始

```bash
python setup_env.py                          # 环境自检
python scripts/surveycraft.py design --init my_survey   # 从模板建项目
python scripts/surveycraft.py export --in surveys/my_survey/survey.json --format all --outdir surveys/my_survey/exports
python scripts/make_synthetic.py --in surveys/my_survey/survey.json --n 150 --out demo.csv   # 模拟数据演示
```

完整命令见 SKILL.md；端到端示例见 `surveys/demo/`。

## 开发

```bash
python -m pytest tests/ -q
```

里程碑与验收标准见 DESIGN.md §23。
