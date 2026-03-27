# -*- coding: utf-8 -*-
"""
RSI 指标
"""

import pandas as pd
from typing import List, Tuple


def calc_rsi(closes: List[float], period: int = 6) -> float:
    """
    计算RSI相对强弱指标

    Args:
        closes: 收盘价列表
        period: 周期，默认6日

    Returns:
        RSI值，0-100
    """
    if len(closes) < period + 1:
        return 50.0  # 数据不足返回中性

    # 计算涨跌幅
    deltas = pd.Series(closes).diff()

    # 分离涨跌
    gains = deltas.clip(lower=0)
    losses = -deltas.clip(upper=0)

    # 计算平均涨跌幅
    avg_gain = gains.rolling(window=period).mean().iloc[-1]
    avg_loss = losses.rolling(window=period).mean().iloc[-1]

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi), 2)


def calc_rsi_series(closes: List[float], period: int = 6) -> list:
    """计算RSI序列"""
    if len(closes) < period + 1:
        return [50.0] * len(closes)

    deltas = pd.Series(closes).diff()
    gains = deltas.clip(lower=0)
    losses = -deltas.clip(upper=0)

    avg_gains = gains.rolling(window=period).mean()
    avg_losses = losses.rolling(window=period).mean()

    rs = avg_gains / avg_losses.replace(0, 0.0001)
    rsi = 100 - (100 / (1 + rs))

    return [float(r) if not pd.isna(r) else 50.0 for r in rsi]


def check_rsi_signals(rsi: float, rsi_prev: float = 0) -> Tuple[bool, str]:
    """
    检查RSI信号

    Returns:
        (oversold_rebound, description)
        oversold_rebound: 是否超卖反弹
    """
    descriptions = []

    oversold = rsi < 30
    oversold_rebound = oversold and rsi_prev > 0 and rsi > rsi_prev

    overbought = rsi > 75
    neutral = 40 <= rsi <= 60

    if oversold:
        descriptions.append("RSI超卖")
    elif overbought:
        descriptions.append("RSI超买")
    elif rsi < 40:
        descriptions.append("RSI偏弱")
    elif rsi > 60:
        descriptions.append("RSI偏强")
    else:
        descriptions.append("RSI中性")

    if oversold_rebound:
        descriptions.append("反弹信号")

    return oversold_rebound, " | ".join(descriptions)
