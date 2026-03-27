# -*- coding: utf-8 -*-
"""
MACD 指标
"""

import pandas as pd
from typing import List, Tuple


def calc_ema(closes: List[float], period: int) -> float:
    """计算指数移动平均"""
    if len(closes) < period:
        return sum(closes) / len(closes) if closes else 0.0
    ema = closes[0]
    multiplier = 2 / (period + 1)
    for price in closes[1:]:
        ema = (price - ema) * multiplier + ema
    return ema


def calc_macd(
    closes: List[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9
) -> Tuple[float, float, float]:
    """
    计算MACD指标

    Args:
        closes: 收盘价列表
        fast: 快线周期
        slow: 慢线周期
        signal: 信号线周期

    Returns:
        (dif, dea, macd_bar)
        dif: 快线-慢线
        dea: 信号线（DIF的EMA）
        macd_bar: MACD柱状图 = (dif - dea) * 2
    """
    if len(closes) < slow + signal:
        return 0.0, 0.0, 0.0

    # 计算EMA
    ema_fast_series = pd.Series(closes).ewm(span=fast, adjust=False).mean()
    ema_slow_series = pd.Series(closes).ewm(span=slow, adjust=False).mean()

    dif_series = ema_fast_series - ema_slow_series
    dea_series = dif_series.ewm(span=signal, adjust=False).mean()

    dif = float(dif_series.iloc[-1])
    dea = float(dea_series.iloc[-1])
    macd_bar = (dif - dea) * 2

    return round(dif, 4), round(dea, 4), round(macd_bar, 4)


def check_macd_signals(
    dif: float,
    dea: float,
    macd_bar: float,
    prev_dif: float = 0,
    prev_dea: float = 0,
) -> Tuple[bool, bool, str]:
    """
    检查MACD信号

    Returns:
        (golden_cross, above_zero, description)
        golden_cross: 是否发生金叉
        above_zero: 是否在零轴上方
    """
    # 金叉：DIF从下方上穿DEA
    golden_cross = prev_dif < prev_dea and dif > dea

    # 死叉：DIF从上方下穿DEA
    death_cross = prev_dif > prev_dea and dif < dea

    # 零轴判断
    above_zero = dif > 0 and dea > 0

    descriptions = []
    if golden_cross:
        descriptions.append("MACD金叉")
    if death_cross:
        descriptions.append("MACD死叉")
    if above_zero:
        descriptions.append("零轴上方")
    elif dif < 0:
        descriptions.append("零轴下方")

    return golden_cross, above_zero, " | ".join(descriptions) if descriptions else "中性"
