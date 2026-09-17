# 统计方法决策表（Method Router 参考）

本表是 `scripts/method_router.py` 的执行依据，也是 Agent 向用户解释"为什么选这个方法"
的说明材料。路由输入：因变量（DV）测量层次 + 自变量（IV）结构 + 前提检验 + 样本量。

## 前提阈值（显式规则）

| 规则 | 阈值 |
|------|------|
| 近正态（数值型 DV） | 每组 n ≥ 30 **且** |偏度| ≤ 2；n≥50 时可参考 Shapiro–Wilk p ≥ .01 |
| 小样本 Shapiro 过敏 | n < 50 时 Shapiro 仅作参考，不单独作为判据 |
| 方差齐性 | Levene p ≥ .05 视为齐性；组容量不等时默认走 Welch 系 |
| 卡方期望频数 | 所有期望频数 ≥ 1 且 < 5 的格子占比 ≤ 20%，否则改 Fisher 精确（2×2）或合并类别 |
| 量表分 vs 单题 | score_* 列为数值（interval）；单题 Likert 为序数——序数 DV 优先非参数 |

## 决策表

### 组间比较（group_compare：DV + 分组 IV）

| DV 测量层次 | 组数 | 前提 | 主方法 | 替代方法 | 事后检验 | 效应量 |
|-------------|------|------|--------|----------|----------|--------|
| 数值（量表分） | 2 | 近正态 + 方差齐 | 独立样本 t | Welch t | — | Cohen's d |
| 数值（量表分） | 2 | 近正态 + 方差不齐 | Welch t | Mann–Whitney U | — | Cohen's d |
| 数值（量表分） | 2 | 非近正态 | Mann–Whitney U | Welch t | — | r = Z/√N |
| 数值（量表分） | ≥3 | 近正态 + 方差齐 | One-way ANOVA | Welch ANOVA | Tukey HSD | η² |
| 数值（量表分） | ≥3 | 近正态 + 方差不齐 / 组容量不等 | Welch ANOVA | Kruskal–Wallis | Games–Howell | ω² |
| 数值（量表分） | ≥3 | 非近正态 | Kruskal–Wallis | Welch ANOVA | 两两 Mann–Whitney（Holm 校正） | ε² |
| 序数（单题 Likert） | 2 | — | Mann–Whitney U | Welch t | — | r = Z/√N |
| 序数（单题 Likert） | ≥3 | — | Kruskal–Wallis | Welch ANOVA | 两两 Mann–Whitney（Holm 校正） | ε² |
| 序数（单题 Likert） | ≥3 | 各组 n≥30 且近正态（罕见） | One-way ANOVA | Kruskal–Wallis | Tukey HSD | η² |

配对设计（V2）：配对 t / Wilcoxon，d_z。

### 交叉分析（crosstab：两个分类变量）

| 条件 | 主方法 | 替代方法 | 事后 | 效应量 |
|------|--------|----------|------|--------|
| 期望频数满足 | 卡方检验 | — | 标准化残差 | Cramér's V |
| 2×2 且期望频数不足 | Fisher 精确检验 | 卡方 | — | Odds Ratio + Cramér's V |
| R×C 期望频数不足 | 合并类别后卡方（须说明依据） | — | 标准化残差 | Cramér's V |

### 相关分析（correlation）

| 变量组合 | 主方法 | 备选 |
|----------|--------|------|
| 数值 + 数值 | Pearson | Spearman |
| 序数 + 序数 | Spearman | Kendall τ |
| 数值 + 序数 | Spearman | — |

### 信度（reliability：一个构念的题项组）

| 内容 | 说明 |
|------|------|
| Cronbach's α | 题项数值化（含反向计分）后计算 |
| α if item deleted | 逐题删除后的 α 变化 |
| item-total 相关 | 修正的题总相关（CITC） |
| KMO + Bartlett | 适合做因子分析的判定（KMO ≥ 0.6、Bartlett p < .05） |

规则：α 低 **不自动删题**——输出指标、删除该题后的变化与理论提示，由研究者决定
（DESIGN.md §6）。

## 输出约定（analysis_plan.json，Gate 3）

Router 为每个分析输出：

```json
{
  "id": "A01",
  "why": "研究目标 5：比较不同年级的满意度差异",
  "type": "group_compare",
  "dv": {"variable": "score_满意度", "level": "numeric"},
  "iv": {"variable": "grade", "groups": 4},
  "method": "welch_anova",
  "fallback": "kruskal_wallis",
  "posthoc": "games_howell",
  "p_adjust": "holm",
  "effect_size": "omega_squared",
  "assumptions": ["n_per_group", "levene", "skew"],
  "assumption_results": {"...": "执行时由 stats_run 填入"},
  "status": "proposed"
}
```

- `status: proposed` 的分析**不会被执行**；用户确认（改为 confirmed_by_user）后 stats_run 才运行
- 每条分析必须回答质量八问的前五问：为什么做、用什么变量、什么方法、为什么、前提如何
