# -*- coding: utf-8 -*-
"""
震荡市波段信号系统

目标：低买高卖，区间上下沿操作，不追涨杀跌

核心指标（3选2买）：
  1. RSI(14) 在 35~55 区间（适中回调）
  2. 价格回踩布林中轨（MA20）或布林下轨
  3. 缩量整固（量比 < 0.8）

卖出：
  - 价格触及布林上轨
  - RSI > 65 止盈

止损：
  - 布林下轨 - 1%ATR

注：震荡市不抄底（RSI<30是弱势思维），不追高（RSI>55是强势思维）
"""

from dataclasses import dataclass, field
from typing import List
from indicators.rsi import calc_rsi
from indicators.atr import calc_atr


@dataclass
class ConsolidateSignal:
    """震荡市信号结果"""
    code: str = ""
    name: str = ""
    price: float = 0.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0   # 布林中轨 = MA20
    bb_lower: float = 0.0
    atr: float = 0.0
    rsi_14: float = 50.0
    volume_ratio: float = 1.0
    buy_signals: List[str] = field(default_factory=list)
    sell_signals: List[str] = field(default_factory=list)
    buy_count: int = 0
    sell_count: int = 0
    decision: str = "WATCH"
    position_ratio: float = 0.0


def calc_bollinger(closes: List[float], period: int = 20, mult: float = 2.0) -> tuple:
    """计算布林带 (上轨, 中轨, 下轨)"""
    if len(closes) < period:
        return 0.0, 0.0, 0.0
    import statistics
    mean = statistics.mean(closes[-period:])
    stdev = statistics.stdev(closes[-period:]) if len(closes) >= period else 0
    middle = mean
    upper = middle + mult * stdev
    lower = middle - mult * stdev
    return upper, middle, lower


def analyze_consolidate(
    history: List[dict],
    realtime: dict,
    buy_price: float = 0.0,
) -> ConsolidateSignal:
    """
    分析震荡市波段信号
    """
    if not history or not realtime:
        return ConsolidateSignal(code=realtime.get("code",""))

    closes = [h["close"] for h in history]
    highs = [h["high"] for h in history]
    lows = [h["low"] for h in history]
    volumes = [h["volume"] for h in history]

    current_price = realtime.get("price", closes[-1])
    code = realtime.get("code", "")
    name = realtime.get("name", "")
    volume = realtime.get("volume", volumes[-1] if volumes else 0)

    result = ConsolidateSignal(code=code, name=name, price=current_price)

    # ===== 布林带 =====
    bb_upper, bb_middle, bb_lower = calc_bollinger(closes)
    result.bb_upper = bb_upper
    result.bb_middle = bb_middle
    result.bb_lower = bb_lower

    # ===== ATR =====
    if len(closes) >= 15:
        atr = calc_atr(
            [h["high"] for h in history[-15:]],
            [h["low"] for h in history[-15:]],
            [h["close"] for h in history[-15:]],
        )
        result.atr = atr

    # ===== RSI(14) =====
    rsi = calc_rsi(closes, 14)
    result.rsi_14 = rsi

    # ===== 量比 =====
    vol_ma5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else volume
    result.volume_ratio = volume / vol_ma5 if vol_ma5 > 0 else 1.0

    # ===== 计算3个买入指标 =====
    buy_triggered = []

    # 指标1: RSI低位拐头（RSI(14)在35~50区间且处于局部低位，比"适中回调"更有底部信号）
    rsi_prev = calc_rsi(closes[:-1], 14) if len(closes) >= 2 else rsi
    rsi_bottom = 35 <= rsi <= 50 and rsi < rsi_prev  # RSI在低位且拐头向下（还没反弹）
    rsi_recovering = 35 <= rsi <= 55 and rsi >= rsi_prev and rsi_prev < rsi  # RSI从低位开始回升
    if rsi_bottom or rsi_recovering:
        buy_triggered.append("RSI低位整固")
        result.buy_signals.append(f"RSI={rsi:.1f}低位整固({rsi_prev:.1f}→{rsi:.1f})")
    elif rsi < 35:
        buy_triggered.append("RSI偏低")
        result.buy_signals.append(f"RSI={rsi:.1f}<35 偏低")

    # 指标2: 价格回踩布林下轨（严格：必须在下轨附近才算有效支撑）
    price_near_lower = bb_lower > 0 and current_price <= bb_lower * 1.01
    if price_near_lower:
        buy_triggered.append("回踩布林下轨")
        result.buy_signals.append(f"回踩布林下轨({bb_lower:.2f})")

    # 指标3: 缩量整固（严格：量比<0.7，缩量明显）
    if result.volume_ratio < 0.7:
        buy_triggered.append("缩量整固")
        result.buy_signals.append(f"量比={result.volume_ratio:.2f}<0.7")

    result.buy_count = len(buy_triggered)

    # ===== 卖出信号 =====
    # 触及布林上轨（区间上沿，高抛）
    if bb_upper > 0 and current_price >= bb_upper * 0.98:
        result.sell_signals.append(f"触及布林上轨({bb_upper:.2f})")
        result.sell_count += 1

    # RSI从反弹位回落（恢复失败，平仓）
    if len(closes) >= 3:
        rsi_prev1 = calc_rsi(closes[:-1], 14)
        rsi_prev2 = calc_rsi(closes[:-2], 14)
        # RSI从较高位置回落（反弹结束信号）
        if rsi_prev1 > rsi_prev2 and rsi < rsi_prev1 and rsi_prev1 >= 55:
            result.sell_signals.append(f"RSI回落({rsi_prev1:.1f}→{rsi:.1f})")
            result.sell_count += 1

    # RSI超买（互斥，只触发一个）
    if rsi > 65:
        result.sell_signals.append(f"RSI={rsi:.1f}>65 超买")
        result.sell_count += 1
    # RSI回到正常偏强（>60止盈）
    elif rsi > 60:
        result.sell_signals.append(f"RSI={rsi:.1f}>60 止盈")
        result.sell_count += 1

    # ===== 持仓中止损（放宽，给波动空间）=====
    if buy_price > 0:
        # ATR止损（从 0.5ATR 放宽到 1.5ATR，正常波动不触发）
        if result.atr > 0:
            stop_price = bb_lower - result.atr * 1.5
            if current_price <= stop_price:
                result.decision = "STOP_LOSS"
                result.sell_signals.append(f"ATR止损({stop_price:.2f})")
                return result
        # 固定止损 -10%（从-8%放宽到-10%）
        loss_pct = (current_price - buy_price) / buy_price * 100
        if loss_pct <= -10:
            result.decision = "STOP_LOSS"
            result.sell_signals.append(f"亏损{loss_pct:.1f}%超限")
            return result

    # ===== 决策 =====
    if buy_price == 0:
        # 无持仓 → 买入
        if len(buy_triggered) >= 2:
            result.decision = "BUY"
            if len(buy_triggered) == 3:
                result.position_ratio = 1.0
            else:
                result.position_ratio = 0.5
        else:
            result.decision = "WATCH"
    else:
        # 持仓中
        if result.sell_count >= 1:
            result.decision = "SELL"
        else:
            result.decision = "HOLD"

    return result
