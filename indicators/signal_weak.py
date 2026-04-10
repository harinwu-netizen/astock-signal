# -*- coding: utf-8 -*-
"""
弱市反弹信号系统

目标：跌势中的短线反弹，快进快出，不抄底不格局

核心指标（3选2买）：
  1. RSI(6) < 30 且拐头向上
  2. 价格触及布林下轨
  3. 缩量（量比 < 0.6）

卖出：
  - RSI > 50 止盈
  - RSI > 65 强制卖出

止损：
  - 硬止损：买入价 - 2%
"""

from dataclasses import dataclass, field
from typing import List
from indicators.rsi import calc_rsi
from indicators.atr import calc_atr


@dataclass
class WeakSignal:
    """弱市信号结果"""
    code: str = ""
    name: str = ""
    price: float = 0.0
    rsi_6: float = 50.0
    prev_rsi_6: float = 50.0
    bb_lower: float = 0.0        # 布林下轨
    volume_ratio: float = 1.0    # 量比
    bias_ma5: float = 0.0        # 偏弱，空头
    buy_signals: List[str] = field(default_factory=list)   # 触发的买入信号名
    sell_signals: List[str] = field(default_factory=list)   # 触发的卖出信号名
    buy_count: int = 0
    sell_count: int = 0
    decision: str = "WATCH"       # BUY / SELL / WATCH / STOP_LOSS
    position_ratio: float = 0.0  # 建议仓位


def calc_bollinger_lower(closes: List[float], period: int = 20, mult: float = 2.0) -> float:
    """计算布林下轨"""
    if len(closes) < period:
        return 0.0
    import statistics
    mean = statistics.mean(closes[-period:])
    stdev = statistics.stdev(closes[-period:]) if len(closes) >= period else 0
    return mean - mult * stdev


def analyze_weak(
    history: List[dict],
    realtime: dict,
    buy_price: float = 0.0,
) -> WeakSignal:
    """
    分析弱市反弹信号

    Args:
        history: 历史K线
        realtime: 实时行情
        buy_price: 持仓成本（用于止损检查）
    """
    if not history or not realtime:
        return WeakSignal(code=realtime.get("code",""))

    closes = [h["close"] for h in history]
    highs = [h["high"] for h in history]
    lows = [h["low"] for h in history]
    volumes = [h["volume"] for h in history]

    current_price = realtime.get("price", closes[-1])
    code = realtime.get("code", "")
    name = realtime.get("name", "")
    volume = realtime.get("volume", volumes[-1] if volumes else 0)

    result = WeakSignal(code=code, name=name, price=current_price)

    # ===== RSI =====
    prev_rsi = calc_rsi(closes[:-1], 6) if len(closes) > 6 else 50.0
    rsi = calc_rsi(closes, 6)
    result.rsi_6 = rsi
    result.prev_rsi_6 = prev_rsi

    # ===== 布林下轨 =====
    bb_lower = calc_bollinger_lower(closes)
    result.bb_lower = bb_lower

    # ===== 量比 =====
    vol_ma5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else volume
    result.volume_ratio = volume / vol_ma5 if vol_ma5 > 0 else 1.0

    # ===== 乖离率（弱市偏弱）=====
    if len(closes) >= 5:
        ma5 = sum(closes[-5:]) / 5
        result.bias_ma5 = (current_price - ma5) / ma5 * 100 if ma5 > 0 else 0

    # ===== 计算3个买入指标 =====
    buy_triggered = []

    # 指标1: RSI超卖反弹（放宽到40，RSI<30只有13%日子）
    if rsi < 30 and rsi > prev_rsi:
        buy_triggered.append("RSI超卖反弹")
        result.buy_signals.append(f"RSI={rsi:.1f}<30 拐头↑")
    elif rsi < 40:
        buy_triggered.append("RSI偏低")
        result.buy_signals.append(f"RSI={rsi:.1f}<40 偏低")

    # 指标2: 触及布林下轨
    if bb_lower > 0 and current_price <= bb_lower * 1.02:
        buy_triggered.append("触及布林下轨")
        result.buy_signals.append(f"触及布林下轨({bb_lower:.2f})")

    # 指标3: 缩量
    if result.volume_ratio < 0.6:
        buy_triggered.append("缩量")
        result.buy_signals.append(f"量比={result.volume_ratio:.2f}<0.6")

    result.buy_count = len(buy_triggered)

    # ===== 卖出信号 =====
    if rsi > 65:
        result.sell_signals.append(f"RSI={rsi:.1f}>65 强制止盈")
        result.sell_count += 1
    elif rsi > 50:
        result.sell_signals.append(f"RSI={rsi:.1f}>50 止盈")
        result.sell_count += 1

    # 弱市：硬止损更严（-2%）
    if buy_price > 0:
        loss_pct = (current_price - buy_price) / buy_price * 100
        if current_price <= buy_price * 0.98:
            result.decision = "STOP_LOSS"
            result.sell_signals.append("触发-2%硬止损")
            return result

    # ===== 决策 =====
    # 弱市买入：3指标满足至少2个
    if len(buy_triggered) >= 2:
        result.decision = "BUY"
        result.position_ratio = min(0.1 * len(buy_triggered), 0.2)  # 2个=20%, 3个=30%
    elif buy_price > 0:
        # 持仓中
        if rsi > 65:
            result.decision = "SELL"
        elif rsi > 50:
            result.decision = "TAKE_PROFIT"
        else:
            result.decision = "HOLD"
    else:
        result.decision = "WATCH"

    return result
