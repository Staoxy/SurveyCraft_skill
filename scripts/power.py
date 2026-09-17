"""SurveyCraft 样本量 / 功效计算（DESIGN.md §13 功效行）。

用法：
    # 求所需样本量
    python scripts/power.py --test t --effect 0.5 --alpha 0.05 --power 0.8
    python scripts/power.py --test anova --effect 0.25 --k 4 --alpha 0.05 --power 0.8
    # 已有样本量时求 achieved power
    python scripts/power.py --test t --effect 0.5 --n 128

效应量约定：Cohen's d（t 检验）、f（ANOVA，d/2 近似换算）、w（卡方）。
"""
from __future__ import annotations

import argparse
import json
import math
import sys


def power_t(effect: float, alpha: float, n: float | None, power: float | None) -> dict:
    from statsmodels.stats.power import TTestIndPower
    analysis = TTestIndPower()
    if n is None:
        n = analysis.solve_power(effect_size=effect, alpha=alpha, power=power,
                                 ratio=1, alternative="two-sided")
        return {"test": "t（两组独立，双尾）", "solved": "required_n_per_group",
                "required_n_per_group": math.ceil(n), "total_n": 2 * math.ceil(n),
                "effect_d": effect, "alpha": alpha, "target_power": power}
    achieved = analysis.power(effect_size=effect, nobs1=n, alpha=alpha, ratio=1,
                              alternative="two-sided")
    return {"test": "t（两组独立，双尾）", "solved": "achieved_power",
            "n_per_group": n, "effect_d": effect, "alpha": alpha,
            "achieved_power": round(float(achieved), 4)}


def power_anova(effect: float, k: int, alpha: float, n: float | None, power: float | None) -> dict:
    from statsmodels.stats.power import FTestAnovaPower
    analysis = FTestAnovaPower()
    if n is None:
        n_total = analysis.solve_power(effect_size=effect, alpha=alpha, power=power,
                                       k_groups=k)
        return {"test": f"单因素 ANOVA（{k} 组）", "solved": "required_total_n",
                "required_total_n": math.ceil(n_total),
                "n_per_group_approx": math.ceil(n_total / k),
                "effect_f": effect, "alpha": alpha, "target_power": power}
    achieved = analysis.power(effect_size=effect, nobs=n, alpha=alpha, k_groups=k)
    return {"test": f"单因素 ANOVA（{k} 组）", "solved": "achieved_power",
            "total_n": n, "effect_f": effect, "alpha": alpha,
            "achieved_power": round(float(achieved), 4)}


def power_chi2(w: float, df: int, alpha: float, n: float | None, power: float | None) -> dict:
    from statsmodels.stats.power import GofChisquarePower
    analysis = GofChisquarePower()
    n_bins = df + 1  # statsmodels 用 n_bins 表达自由度（df = n_bins - 1）
    if n is None:
        n = analysis.solve_power(effect_size=w, nobs=None, alpha=alpha, power=power,
                                 n_bins=n_bins)
        return {"test": f"卡方（df = {df}）", "solved": "required_total_n",
                "required_total_n": math.ceil(float(n)),
                "effect_w": w, "alpha": alpha, "target_power": power,
                "note": "列联表场景请结合自由度解释"}
    achieved = analysis.power(effect_size=w, nobs=n, alpha=alpha, n_bins=n_bins)
    return {"test": f"卡方（df = {df}）", "solved": "achieved_power",
            "total_n": n, "effect_w": w, "alpha": alpha,
            "achieved_power": round(float(achieved), 4)}


def main() -> int:
    parser = argparse.ArgumentParser(description="SurveyCraft 功效 / 样本量计算")
    parser.add_argument("--test", required=True, choices=["t", "anova", "chi2"])
    parser.add_argument("--effect", required=True, type=float,
                        help="效应量：d（t）/ f（ANOVA）/ w（卡方）")
    parser.add_argument("--k", type=int, default=3, help="ANOVA 组数")
    parser.add_argument("--df", type=int, default=1, help="卡方自由度")
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--power", type=float, default=0.8)
    parser.add_argument("--n", type=float, default=None,
                        help="t：每组样本量；anova/chi2：总样本量（省略则求所需样本量）")
    args = parser.parse_args()

    if args.test == "t":
        result = power_t(args.effect, args.alpha, args.n, args.power)
    elif args.test == "anova":
        result = power_anova(args.effect, args.k, args.alpha, args.n, args.power)
    else:
        result = power_chi2(args.effect, args.df, args.alpha, args.n, args.power)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
