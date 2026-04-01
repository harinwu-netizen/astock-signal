# -*- coding: utf-8 -*-
"""
ATR 真实波幅指标
"""

import pandas as pd
from typing import List


def calc_tr(highs: List[float], lows: List[float], closes: List[float]) -> List[float]:
    """
    计算True Range（真实波幅）

    TR = max(当日高点-当日低点,
             abs(当日高点-昨日收盘),
             abs(当日低点-昨日收盘))
    """
    if len(closes) < 2:
        return [0.0] * len(closes)

    tr_list = []
    for i in range(len(closes)):
        if i == 0:
            tr = highs[0] - lows[0]
        else:
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i - 1])
            lc = abs(lows[i] - closes[i - 1])
            tr = max(hl, hc, lc)
        tr_list.append(tr)
    return tr_list


def calc_atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14
) -> float:
    """
    计算ATR平均真实波幅

    Args:
        highs: 最高价列表
        lows: 最低价列表
        closes: 收盘价列表
        period: 周期，默认14日

    Returns:
        ATR值
    """
    if len(closes) < period + 1:
        return 0.0

    tr_list = calc_tr(highs, lows, closes)
    # 使用Wilder平滑法
    atr = sum(tr_list[-period:]) / period

    # 后续使用指数平滑
    for tr in tr_list[period:]:
        atr = (atr * (period - 1) + tr) / period

    return round(atr, 4)


def calc_atr_stop_loss(
    buy_price: float,
    atr: float,
    multiplier: float = 2.0,
    floor_pct: float = 0.05
) -> float:
    """
    计算ATR止损价（含地板保护）

    公式：max(买入价 - N×ATR, 买入价 × (1 - floor_pct))

    Args:
        buy_price: 买入价
        atr: ATR值
        multiplier: ATR倍数，默认2倍
        floor_pct: 地板止损百分比，默认5%（即最多亏损5%）

    Returns:
        止损价
    """
    if atr <= 0:
        return round(buy_price * (1 - floor_pct), 2)
    atr_stop = buy_price - atr * multiplier
    floor_stop = buy_price * (1 - floor_pct)
    return round(max(atr_stop, floor_stop), 2)


def calc_take_profit(
    buy_price: float,
    atr: float,
    multiplier: float = 3.0
) -> float:
    """
    计算目标止盈价（基于ATR）

    止盈设置为止损幅度的3倍，即盈亏比3:1

    Args:
        buy_price: 买入价
        atr: ATR值
        multiplier: ATR倍数，默认3倍

    Returns:
        止盈价
    """
    if atr <= 0:
        return round(buy_price * 1.15, 2)  # 默认15%止盈
    return round(buy_price + atr * multiplier, 2)
