# -*- coding: utf-8 -*-
"""
震荡市波段信号系统

目标：低买高卖，区间上下沿操作，不追涨杀跌

核心指标（3选2买，v5.7起改为加权得分）：
  1. RSI(14) 在 35~55 区间低位拐头     权重 0.5
  2. 价格回踩布林下轨                   权重 0.4
  3. 缩量整固（量比 < 0.7）             权重 0.3

买入阈值：加权得分 >= 0.5

卖出：
  - 价格触及布林上轨
  - RSI > 65 止盈

止损：
  - 布林下轨 - 1%ATR

注：震荡市不抄底（RSI<30是弱势思维），不追高（RSI>55是强势思维）
"""

# ===== P3新增：信号权重字典 =====
CONS_BUY_WEIGHTS = {
    "RSI低位整固":  0.5,    # 真正有意义的底部信号
    "RSI偏低":      0.2,    # 单独偏低意义弱
    "回踩布林下轨": 0.4,    # 支撑确认
    "缩量整固":     0.3,    # 配合信号
}

CONS_BUY_THRESHOLD = 0.5    # P3：加权得分 >= 0.5 才买入


def calc_weighted_score(triggered: list, weights: dict) -> float:
    """P3新增：计算加权得分"""
    return sum(weights.get(sig, 0.0) for sig in triggered)

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


def calc_bollinger_bandwidth(closes: List[float], period: int = 20, mult: float = 2.0) -> float:
    """
    计算布林带宽（Bandwidth）：衡量波动率收缩/扩张
    Bandwidth = (Upper - Lower) / Middle
    带宽极低（< 0.1）= 布林带收缩 = 真震荡市，适合波段操作
    带宽高（> 0.2）= 布林带扩张 = 趋势行情，不是震荡市
    """
    upper, middle, lower = calc_bollinger(closes, period, mult)
    if middle == 0 or upper == lower:
        return 0.0
    return (upper - lower) / middle


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

    # ===== P4新增：布林带宽过滤（只做真震荡市）=====
    # 带宽 >= 0.20 → 布林带扩张，趋势行情，不适合震荡市波段
    # 带宽 < 0.10 → 真震荡（极度收敛），最佳波段机会
    # 带宽 0.10~0.20 → 普通震荡，正常判断
    bw = calc_bollinger_bandwidth(closes)
    is_true_consolidation = bw < 0.15   # 真震荡标志，信号打折使用
    is_high_volatility = bw >= 0.20    # 高波动/趋势，不做震荡市买入

    if is_high_volatility:
        # 布林带扩张 = 趋势市场，震荡市策略不适用，直接 WATCH
        result.decision = "WATCH"
        result.position_ratio = 0.0
        return result

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

    # ===== P3+P4：加权得分买入决策（受带宽门控）=====
    def calc_ws(triggered): return sum(CONS_BUY_WEIGHTS.get(s, 0.0) for s in triggered)
    ws = calc_ws(buy_triggered)

    # 真震荡（带宽<0.10）：额外加分，宽松通过
    if bw < 0.10 and ws >= 0.3:
        ws = min(ws * 1.3, 1.5)  # 真震荡信号强化，最多不超过1.5

    if buy_price == 0:
        if ws >= CONS_BUY_THRESHOLD:
            result.decision = "BUY"
            result.position_ratio = min(ws / 1.2, 1.0)
        else:
            result.decision = "WATCH"
    else:
        if result.sell_count >= 1:
            result.decision = "SELL"
        else:
            result.decision = "HOLD"

    return result
