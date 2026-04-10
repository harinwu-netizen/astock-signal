# -*- coding: utf-8 -*-
"""
强市趋势信号系统

目标：跟住大趋势，不轻易下车，吃完整波行情

核心指标（3选2买）：
  1. MA5 > MA10 > MA20（均线多头）
  2. DIF > 0 且 MACD柱放大（动能增强）
  3. 量价齐升（量比 > 1.3 且价格上涨）

持有：
  - 价格不破MA10/MA20
  - 让利润奔跑

止损：
  - 跟踪止损：跌破MA20离场（趋势止损，不主观）

卖出：
  - MA5下穿MA10
  - 收盘跌破MA20
"""

from dataclasses import dataclass, field
from typing import List
from indicators.macd import calc_macd


@dataclass
class StrongSignal:
    """强市信号结果"""
    code: str = ""
    name: str = ""
    price: float = 0.0
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    dif: float = 0.0
    dea: float = 0.0
    macd_bar: float = 0.0
    prev_macd_bar: float = 0.0
    volume_ratio: float = 1.0
    price_change_pct: float = 0.0
    buy_signals: List[str] = field(default_factory=list)
    sell_signals: List[str] = field(default_factory=list)
    buy_count: int = 0
    sell_count: int = 0
    decision: str = "WATCH"
    position_ratio: float = 0.0


def calc_all_ma(closes: List[float]) -> dict:
    """计算均线"""
    if len(closes) < 5:
        return {"ma5": 0, "ma10": 0, "ma20": 0}
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else ma5
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else ma10
    return {"ma5": ma5, "ma10": ma10, "ma20": ma20}


def analyze_strong(
    history: List[dict],
    realtime: dict,
    buy_price: float = 0.0,
) -> StrongSignal:
    """
    分析强市趋势信号
    """
    if not history or not realtime:
        return StrongSignal(code=realtime.get("code",""))

    closes = [h["close"] for h in history]
    volumes = [h["volume"] for h in history]

    current_price = realtime.get("price", closes[-1])
    code = realtime.get("code", "")
    name = realtime.get("name", "")
    volume = realtime.get("volume", volumes[-1] if volumes else 0)

    result = StrongSignal(code=code, name=name, price=current_price)

    # ===== 均线 =====
    ma = calc_all_ma(closes)
    result.ma5 = ma["ma5"]
    result.ma10 = ma["ma10"]
    result.ma20 = ma["ma20"]

    # ===== MACD =====
    prev_dif, prev_dea, prev_bar = 0.0, 0.0, 0.0
    if len(closes) >= 27:
        prev_dif, prev_dea, prev_bar = calc_macd(closes[:-1])
    dif, dea, macd_bar = calc_macd(closes)
    result.dif = dif
    result.dea = dea
    result.macd_bar = macd_bar
    result.prev_macd_bar = prev_bar

    # ===== 量比 =====
    vol_ma5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else volume
    result.volume_ratio = volume / vol_ma5 if vol_ma5 > 0 else 1.0

    # ===== 价格变动 =====
    if len(closes) >= 2:
        result.price_change_pct = (closes[-1] - closes[-2]) / closes[-2] * 100

    # ===== 计算3个买入指标 =====
    buy_triggered = []

    # 指标1: 均线多头排列
    if result.ma5 > result.ma10 > result.ma20 and result.ma20 > 0:
        buy_triggered.append("均线多头排列")
        result.buy_signals.append(f"MA5={result.ma5:.2f}>MA10={result.ma10:.2f}>MA20={result.ma20:.2f}")

    # 指标2: MACD扩散（DIF>0 且 MACD柱比前一日放大）
    if dif > 0 and macd_bar > prev_bar:
        buy_triggered.append("MACD扩散")
        result.buy_signals.append(f"DIF={dif:.4f}>0 且柱状体放大({macd_bar:.4f}>{prev_bar:.4f})")

    # 指标3: 量价齐升
    if result.volume_ratio > 1.3 and result.price_change_pct > 0:
        buy_triggered.append("量价齐升")
        result.buy_signals.append(f"量比={result.volume_ratio:.2f}>1.3 且上涨{result.price_change_pct:.2f}%")

    result.buy_count = len(buy_triggered)

    # ===== 卖出信号 =====
    # MA空头排列
    if result.ma5 < result.ma10 < result.ma20:
        result.sell_signals.append("MA空头排列")
        result.sell_count += 1

    # 跌破MA20（强市趋势破坏）
    if result.ma20 > 0 and current_price < result.ma20:
        result.sell_signals.append(f"跌破MA20({result.ma20:.2f})")
        result.sell_count += 1

    # 缩量滞涨
    if result.volume_ratio < 0.7 and result.price_change_pct < 0:
        result.sell_signals.append(f"缩量滞涨(量比={result.volume_ratio:.2f})")
        result.sell_count += 1

    # ===== 持仓中止损检查 =====
    if buy_price > 0:
        # 跟踪止损：MA20跌破（趋势止损，不主观）
        if result.ma20 > 0 and current_price < result.ma20:
            result.decision = "STOP_LOSS"
            result.sell_signals.append(f"跟踪止损MA20({result.ma20:.2f})")
            return result
        # 固定止损：亏损>8%（收紧）
        loss_pct = (current_price - buy_price) / buy_price * 100
        if loss_pct <= -8:
            result.decision = "STOP_LOSS"
            result.sell_signals.append(f"亏损{loss_pct:.1f}%超限")
            return result

    # ===== 决策 =====
    if buy_price == 0:
        # 无持仓 → 买入判断
        if len(buy_triggered) >= 2:
            result.decision = "BUY"
            if len(buy_triggered) == 3:
                result.position_ratio = 1.0
            else:
                result.position_ratio = 0.5
        else:
            result.decision = "WATCH"
    else:
        # 持仓中 → 持有
        if result.sell_count >= 1:
            result.decision = "SELL"
        else:
            result.decision = "HOLD"

    return result
