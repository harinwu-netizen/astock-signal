# -*- coding: utf-8 -*-
"""
弱市反弹信号系统

目标：跌势中的短线反弹，快进快出，不抄底不格局

核心指标（3选2买，v5.7起改为加权得分）：
  1. RSI(6) < 30 且拐头向上          权重 0.5
  2. 价格触及布林下轨                 权重 0.4
  3. 缩量（量比 < 0.6）              权重 0.3

买入阈值：加权得分 >= 0.5

卖出：
  - RSI > 50 止盈
  - RSI > 65 强制卖出

止损：
  - 硬止损：买入价 - 2%
"""

# ===== P3新增：信号权重字典 =====
WEAK_BUY_WEIGHTS = {
    "RSI超卖反弹": 0.5,    # RSI<30 拐头，质量最高
    "RSI偏低":     0.2,    # RSI<35 偏低，单独意义弱
    "触及布林下轨": 0.4,    # 独立支撑信号
    "缩量":        0.3,    # 配合信号有价值
}

WEAK_BUY_THRESHOLD = 0.5    # P3：加权得分 >= 0.5 才买入


def calc_weighted_score(triggered: list, weights: dict) -> float:
    """P3新增：计算加权得分"""
    return sum(weights.get(sig, 0.0) for sig in triggered)

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

    # 指标1: RSI超卖反弹（v5.8加量能确认+更严格RSI阈值）
    # v5.9改动：RSI<25才算真正超卖（原来<30太宽），同时要求价格已处于局部低点
    price_at_local_low = False
    if len(lows) >= 5:
        recent_lows = sorted(lows[-10:])  # 近10日低点排序
        low_threshold = recent_lows[len(recent_lows) // 3]  # 低30%分位
        price_at_local_low = current_price <= low_threshold

    # v5.9新增：量能确认 — 弱市反弹必须是放量的，才有效
    # 放量标准：当日成交量 > 5日均量的 0.8倍（即不过度缩量，但也不需要放量，只要不是地量）
    # 真正的问题是有时缩量反弹是"死猫跳"，需要一定量能支撑
    # 量比 0.6~1.5 之间是健康反弹，过低=无资金参与，过高=恐慌抛盘
    vol_confirmed = 0.6 <= result.volume_ratio <= 2.0

    if rsi < 25 and rsi > prev_rsi and price_at_local_low and vol_confirmed:
        buy_triggered.append("RSI超卖反弹")
        result.buy_signals.append(f"RSI={rsi:.1f}<25拐头↑+局部低点+量能确认")
    elif rsi < 30 and rsi > prev_rsi and price_at_local_low:
        buy_triggered.append("RSI超卖反弹")
        result.buy_signals.append(f"RSI={rsi:.1f}<30拐头↑+局部低点(无量能确认)")
    elif rsi < 35 and price_at_local_low:
        buy_triggered.append("RSI偏低")
        result.buy_signals.append(f"RSI={rsi:.1f}<35+局部低点")
    elif rsi < 30 and rsi > prev_rsi:
        buy_triggered.append("RSI偏低")
        result.buy_signals.append(f"RSI={rsi:.1f}<30拐头↑(无局部低点)")

    # 指标2: 触及布林下轨
    if bb_lower > 0 and current_price <= bb_lower * 1.02:
        buy_triggered.append("触及布林下轨")
        result.buy_signals.append(f"触及布林下轨({bb_lower:.2f})")

    # 指标3: 缩量
    if result.volume_ratio < 0.6:
        buy_triggered.append("缩量")
        result.buy_signals.append(f"量比={result.volume_ratio:.2f}<0.6")

    result.buy_count = len(buy_triggered)

    # ===== 卖出信号（弱市：让利润跑，不主动止盈）=====
    # 弱市不主动止盈（RSI45/55太早下车），只认趋势破坏和硬止损
    # RSI进入超买区才考虑走（给反弹足够空间）
    # v5.9改动：止盈阈值从65提至72 — 原来65就止盈太早，反弹还没走完
    # 弱市RSI能到80以上说明是真正强反弹，应让利润走完
    if rsi > 75:
        result.sell_signals.append(f"RSI={rsi:.1f}>75 超买")
        result.sell_count += 1
    elif rsi > 72:
        result.sell_signals.append(f"RSI={rsi:.1f}>72 止盈")
        result.sell_count += 1
    elif rsi > 65:
        # v5.9：65-72之间保留关注，但不触发卖出，让子弹再飞一会儿
        result.sell_signals.append(f"RSI={rsi:.1f}>65 观察中")

    # 弱市：硬止损 -2%（不抗单）
    if buy_price > 0:
        if current_price <= buy_price * 0.98:
            result.decision = "STOP_LOSS"
            result.sell_signals.append("触发-2%硬止损")
            return result

    # ===== P3替换：加权得分买入决策 =====
    weighted_score = calc_weighted_score(buy_triggered, WEAK_BUY_WEIGHTS)
    if weighted_score >= WEAK_BUY_THRESHOLD:
        result.decision = "BUY"
        result.position_ratio = min(weighted_score / 1.2, 0.3)  # 归一化，封顶30%
    elif buy_price > 0:
        # 持仓中：让利润跑，RSI>65才止盈，RSI>70超买强走
        if rsi > 70:
            result.decision = "SELL"
        elif rsi > 65:
            result.decision = "TAKE_PROFIT"
        else:
            result.decision = "HOLD"
    else:
        result.decision = "WATCH"

    return result
