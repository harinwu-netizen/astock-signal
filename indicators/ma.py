# -*- coding: utf-8 -*-
"""
均线系统指标
"""

import pandas as pd
from typing import List, Tuple, Optional


def calc_ma(closes: List[float], period: int) -> float:
    """计算简单移动平均"""
    if len(closes) < period:
        return 0.0
    return sum(closes[-period:]) / period


def calc_ma_series(closes: List[float], period: int) -> List[float]:
    """计算均线序列（对齐到每一天）"""
    if len(closes) < period:
        return [0.0] * len(closes)
    result = [0.0] * (period - 1)
    for i in range(period - 1, len(closes)):
        result.append(sum(closes[i - period + 1:i + 1]) / period)
    return result


def check_ma_alignment(ma5: float, ma10: float, ma20: float) -> Tuple[bool, str]:
    """
    检查均线多头/空头排列

    Returns:
        (is_bullish, description)
    """
    if ma5 > ma10 > ma20:
        return True, "MA5>MA10>MA20 多头排列"
    elif ma5 < ma10 < ma20:
        return False, "MA5<MA10<MA20 空头排列"
    elif ma5 > ma10 and ma10 < ma20:
        return True, "MA5>MA10，震荡偏强"
    elif ma5 < ma10 and ma10 > ma20:
        return False, "MA5<MA10，震荡偏弱"
    else:
        return False, "均线纠缠"


def check_price_ma_support(
    price: float,
    ma5: float,
    ma10: float,
    ma20: float,
    tolerance: float = 0.01
) -> Tuple[bool, str]:
    """
    检查价格回踩均线支撑

    Args:
        price: 当前价格
        ma5, ma10, ma20: 各周期均线
        tolerance: 容差，1%以内算回踩

    Returns:
        (on_support, description)
    """
    results = []

    if ma5 > 0 and price <= ma5 * (1 + tolerance):
        results.append("MA5")
    if ma10 > 0 and price <= ma10 * (1 + tolerance):
        results.append("MA10")
    if ma20 > 0 and price <= ma20 * (1 + tolerance):
        results.append("MA20")

    if results:
        return True, f"价格回踩{'/'.join(results)}支撑"
    return False, ""


def calc_bias(price: float, ma: float) -> float:
    """
    计算乖离率

    Returns:
        乖离率%，正数表示价格高于均线
    """
    if ma <= 0:
        return 0.0
    return (price - ma) / ma * 100


def calc_all_ma(closes: List[float]) -> dict:
    """
    计算所有均线指标

    Returns:
        dict: {ma5, ma10, ma20, ma60, bias_ma5, bias_ma10, bias_ma20}
    """
    closes_series = pd.Series(closes)

    ma5 = float(closes_series.rolling(5).mean().iloc[-1]) if len(closes) >= 5 else 0.0
    ma10 = float(closes_series.rolling(10).mean().iloc[-1]) if len(closes) >= 10 else 0.0
    ma20 = float(closes_series.rolling(20).mean().iloc[-1]) if len(closes) >= 20 else 0.0
    ma60 = float(closes_series.rolling(60).mean().iloc[-1]) if len(closes) >= 60 else 0.0

    current_price = closes[-1] if closes else 0.0

    return {
        "ma5": round(ma5, 3),
        "ma10": round(ma10, 3),
        "ma20": round(ma20, 3),
        "ma60": round(ma60, 3),
        "bias_ma5": round(calc_bias(current_price, ma5), 2),
        "bias_ma10": round(calc_bias(current_price, ma10), 2),
        "bias_ma20": round(calc_bias(current_price, ma20), 2),
    }
