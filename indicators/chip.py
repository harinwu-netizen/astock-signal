# -*- coding: utf-8 -*-
"""
筹码分布指标
通过换手率和量价关系估算筹码集中度
"""

import logging
from typing import List, Tuple
import pandas as pd

logger = logging.getLogger(__name__)


def calc_chip_concentration(
    volumes: List[int],
    closes: List[float],
    price: float,
    periods: int = 10
) -> float:
    """
    估算筹码集中度

    算法:
    1. 计算近期成交价格区间
    2. 统计各价格区间的成交量分布
    3. 计算成交量加权的标准差
    4. 集中度 = 1 / (1 + std_dev)

    Returns:
        集中度 0-1，越大越集中
    """
    if len(volumes) < periods or len(closes) < periods:
        return 0.5  # 数据不足返回中性

    recent_vol = volumes[-periods:]
    recent_close = closes[-periods:]

    # 价格区间
    low = min(recent_close)
    high = max(recent_close)
    if high == low:
        return 0.5

    # 划分价格区间（10档）
    bins = 10
    bin_width = (high - low) / bins
    if bin_width == 0:
        return 0.5

    # 统计每档成交量
    bin_volumes = [0] * bins
    for i in range(len(recent_close)):
        price_bin = int((recent_close[i] - low) / bin_width)
        price_bin = min(price_bin, bins - 1)
        bin_volumes[price_bin] += recent_vol[i]

    # 计算加权均价
    total_vol = sum(bin_volumes)
    if total_vol == 0:
        return 0.5

    weighted_price = 0.0
    for i, vol in enumerate(bin_volumes):
        bin_center = low + (i + 0.5) * bin_width
        weighted_price += bin_center * vol / total_vol

    # 计算成交量加权标准差
    variance = 0.0
    for i, vol in enumerate(bin_volumes):
        bin_center = low + (i + 0.5) * bin_width
        variance += ((bin_center - weighted_price) ** 2) * vol / total_vol

    std_dev = variance ** 0.5

    # 集中度 = 1 / (1 + 变异系数)
    coef_of_var = std_dev / weighted_price if weighted_price > 0 else 0
    concentration = 1.0 / (1.0 + coef_of_var * 10)  # 放大系数使区分更明显

    return round(min(max(concentration, 0), 1), 4)


def calc_profit_ratio(
    closes: List[float],
    volumes: List[int],
    periods: int = 10
) -> Tuple[float, float]:
    """
    计算获利筹码比例和平均成本

    Args:
        closes: 收盘价列表
        volumes: 成交量列表
        periods: 统计周期

    Returns:
        (获利比例, 平均成本)
    """
    if len(closes) < periods or len(volumes) < periods:
        return 0.5, 0.0

    recent_close = closes[-periods:]
    recent_vol = volumes[-periods:]

    current_price = recent_close[-1]
    total_vol = sum(recent_vol)

    if total_vol == 0:
        return 0.5, current_price

    # 获利筹码 = 收盘价 > 成本的成交量占比
    profitable_vol = 0
    total_cost = 0.0

    for i in range(periods):
        total_cost += recent_close[i] * recent_vol[i]
        if recent_close[i] <= current_price:
            profitable_vol += recent_vol[i]

    avg_cost = total_cost / total_vol
    profit_ratio = profitable_vol / total_vol

    return round(profit_ratio, 4), round(avg_cost, 2)


def check_chip_buy_signal(
    concentration: float,
    profit_ratio: float,
    turnover_rate: float
) -> Tuple[bool, str]:
    """
    检查筹码相关的买入信号

    Returns:
        (triggered, reason)
    """
    reasons = []

    # 信号1: 筹码集中（集中度>0.6）
    if concentration > 0.6:
        reasons.append(f"筹码集中({concentration:.2%})")

    # 信号2: 获利比例适中（30%-70%）
    if 0.3 <= profit_ratio <= 0.7:
        reasons.append(f"获利比例适中({profit_ratio:.2%})")

    # 信号3: 换手率低（筹码稳定）
    if 0 < turnover_rate < 5:
        reasons.append(f"换手率低({turnover_rate:.2f}%)，筹码稳定")

    triggered = len(reasons) >= 1
    return triggered, " | ".join(reasons) if reasons else ""


def check_chip_sell_signal(
    concentration: float,
    profit_ratio: float,
    turnover_rate: float
) -> Tuple[bool, str]:
    """
    检查筹码相关的卖出信号

    Returns:
        (triggered, reason)
    """
    reasons = []

    # 信号1: 筹码高度集中后分散（主力出货嫌疑）
    if concentration < 0.3 and turnover_rate > 10:
        reasons.append(f"筹码分散({concentration:.2%})+高换手({turnover_rate:.1f}%)")

    # 信号2: 获利比例极高（>90%散户获利，主力可能要派发）
    if profit_ratio > 0.9 and turnover_rate > 5:
        reasons.append(f"获利比例极高({profit_ratio:.2%})，注意派发风险")

    triggered = len(reasons) >= 1
    return triggered, " | ".join(reasons) if reasons else ""
