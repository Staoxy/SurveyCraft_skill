"""power.py 功效计算测试（与 G*Power 教科书常用值对照）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from power import power_anova, power_chi2, power_t


def test_t_required_n():
    """Cohen (1988)：d=0.5、α=.05、power=.80 → 每组约 64 人。"""
    res = power_t(0.5, 0.05, None, 0.8)
    assert res["required_n_per_group"] == pytest.approx(64, abs=1)


def test_t_achieved_power():
    res = power_t(0.5, 0.05, 64, None)
    assert res["achieved_power"] == pytest.approx(0.8014, abs=0.01)


def test_anova_required_n():
    """f=0.25（中等）、k=4 → 总样本约 180（G*Power 同参数 ≈ 180）。"""
    res = power_anova(0.25, 4, 0.05, None, 0.8)
    assert res["required_total_n"] == pytest.approx(180, abs=3)


def test_chi2_required_n():
    res = power_chi2(0.1, 1, 0.05, None, 0.8)
    assert res["required_total_n"] > 700  # w=0.1 是小效应，需要大样本
    assert isinstance(res["required_total_n"], int)
