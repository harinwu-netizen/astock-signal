# -*- coding: utf-8 -*-
"""
三层决策路由 v5.0

架构：三个独立信号系统 + 智能路由

弱市 → 弱市反弹系统（快进快出，RSI+布林+量能）
强市 → 强市趋势系统（趋势跟随，MA+MACD+量价）
震荡 → 震荡波段系统（高抛低吸，布林+RSI+量比）

每个系统独立计算信号，最终决策由路由层综合决定：
  1. 优先使用当前市场状态对应的系统
  2. 其他系统的信号可作为否决信号
  3. 止损信号拥有最高优先级（P0，不可绕过）
"""

from dataclasses import dataclass, field
from typing import List, Optional
from models.signal import MarketStatus, Decision as EnumDecision
from indicators.signal_weak import analyze_weak, WeakSignal
from indicators.signal_strong import analyze_strong, StrongSignal
from indicators.signal_consolidate import analyze_consolidate, ConsolidateSignal


@dataclass
class UnifiedSignal:
    """
    统一信号输出（兼容旧 SignalCounter 接口）
    内部包含三个子系统的分析结果
    """
    code: str = ""
    name: str = ""
    price: float = 0.0
    change_pct: float = 0.0
    timestamp: str = ""

    # 三个子系统信号
    weak: Optional[WeakSignal] = None
    strong: Optional[StrongSignal] = None
    consolidate: Optional[ConsolidateSignal] = None

    # 路由决策
    market_status: MarketStatus = MarketStatus.CONSOLIDATE
    primary_decision: str = "WATCH"   # BUY / SELL / HOLD / WATCH / STOP_LOSS / TAKE_PROFIT
    primary_reason: str = ""

    # 兼容旧接口的字段
    buy_count: int = 0
    sell_count: int = 0
    buy_signals_detail: List[str] = field(default_factory=list)
    sell_signals_detail: List[str] = field(default_factory=list)
    position_ratio: float = 0.0
    atr: float = 0.0
    rsi_6: float = 50.0
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0

    @property
    def decision(self) -> EnumDecision:
        """兼容旧接口：映射 primary_decision 为枚举"""
        mapping = {
            "BUY": EnumDecision.BUY,
            "SELL": EnumDecision.SELL,
            "HOLD": EnumDecision.HOLD,
            "WATCH": EnumDecision.WATCH,
            "STOP_LOSS": EnumDecision.STOP_LOSS,
            "TAKE_PROFIT": EnumDecision.TAKE_PROFIT,
        }
        return mapping.get(self.primary_decision, EnumDecision.WATCH)

    @property
    def buy_signals(self) -> list:
        return self.buy_signals_detail

    @property
    def sell_signals(self) -> list:
        return self.sell_signals_detail

    @property
    def rebound_count(self) -> int:
        """兼容：弱市反弹信号数"""
        return self.weak.buy_count if self.weak else 0

    @property
    def trend_count(self) -> int:
        """兼容：强市趋势信号数"""
        return self.strong.buy_count if self.strong else 0

    @property
    def consolidate_buy_count(self) -> int:
        """兼容：震荡市买入信号数"""
        return self.consolidate.buy_count if self.consolidate else 0

    @property
    def consolidate_sell_count(self) -> int:
        """兼容：震荡市卖出信号数"""
        return self.consolidate.sell_count if self.consolidate else 0

    @property
    def bb_upper(self) -> float:
        """兼容：震荡市布林上轨"""
        return self.consolidate.bb_upper if self.consolidate else 0.0

    @property
    def decision_enum(self) -> EnumDecision:
        return self.decision


def analyze_unified(
    history: List[dict],
    realtime: dict,
    market_status: MarketStatus = MarketStatus.CONSOLIDATE,
    buy_price: float = 0.0,
) -> UnifiedSignal:
    """
    统一信号分析入口

    同时运行三个子系统，取当前市场状态对应的结果作为主决策，
    并用其他系统的信号做否决/确认参考。

    Args:
        history: 历史K线
        realtime: 实时行情
        market_status: 大盘状态（由外部判断后传入）
        buy_price: 持仓成本（用于止损检查）

    Returns:
        UnifiedSignal 统一信号对象
    """
    if not history or not realtime:
        return UnifiedSignal(
            code=realtime.get("code", ""),
            name=realtime.get("name", ""),
            price=realtime.get("price", 0),
        )

    code = realtime.get("code", "")
    name = realtime.get("name", "")
    current_price = realtime.get("price", history[-1]["close"])

    # 同时计算三个子系统
    weak_sig = analyze_weak(history, realtime, buy_price)
    strong_sig = analyze_strong(history, realtime, buy_price)
    cons_sig = analyze_consolidate(history, realtime, buy_price)

    # ===== 路由决策 =====
    decision = "WATCH"
    reason = ""
    buy_count = 0
    sell_count = 0
    buy_detail = []
    sell_detail = []
    position_ratio = 0.0

    # 当前市场状态的系统结果
    if market_status == MarketStatus.WEAK:
        primary = weak_sig
        buy_count = weak_sig.buy_count
        sell_count = weak_sig.sell_count
        buy_detail = weak_sig.buy_signals
        sell_detail = weak_sig.sell_signals
        position_ratio = weak_sig.position_ratio
    elif market_status == MarketStatus.STRONG:
        primary = strong_sig
        buy_count = strong_sig.buy_count
        sell_count = strong_sig.sell_count
        buy_detail = strong_sig.buy_signals
        sell_detail = strong_sig.sell_signals
        position_ratio = strong_sig.position_ratio
    else:
        primary = cons_sig
        buy_count = cons_sig.buy_count
        sell_count = cons_sig.sell_count
        buy_detail = cons_sig.buy_signals
        sell_detail = cons_sig.sell_signals
        position_ratio = cons_sig.position_ratio

    # ===== 跨系统否决机制（仅针对非本市场状态的信号）=====
    # 原则：弱市系统不否决强市，强市系统不否决弱市，只有震荡市的信号才对其他市场有参考价值
    # 震荡市出卖出信号 → 可否决弱市买入（弱势不该买震荡高位的票）
    # 强市出卖出信号 → 可否决震荡市买入（区间上沿+趋势破坏，双重风险）
    # 弱市出强卖(RSI>65) → 可否决震荡市买入（不抢弱势中的高位）
    cons_sell = cons_sig.sell_count > 0
    strong_sell = strong_sig.sell_count > 0
    weak_rsi_overbuy = (weak_sig.rsi_6 > 65) if weak_sig else False

    # ===== 最终决策 =====
    # 持仓中（buy_price > 0）：优先检查卖出信号
    if buy_price > 0:
        if primary.decision in ("STOP_LOSS",):
            decision = "STOP_LOSS"
            reason = primary.sell_signals[0] if primary.sell_signals else "止损"
        elif primary.decision in ("SELL",):
            decision = "SELL"
            reason = primary.sell_signals[0] if primary.sell_signals else "卖出"
        elif primary.decision in ("TAKE_PROFIT",):
            decision = "TAKE_PROFIT"
            reason = primary.sell_signals[0] if primary.sell_signals else "止盈"
        elif primary.sell_count > 0:
            # 子系统出了卖出信号（但 decision 可能还是 HOLD/WATCH）
            decision = "SELL"
            reason = f"[{market_status.value}] {' + '.join(primary.sell_signals)}"
        else:
            decision = "HOLD"
            reason = f"[{market_status.value}] 持仓中，无卖出信号"
    # 无持仓：检查买入信号
    elif primary.decision == "BUY":
        if market_status == MarketStatus.CONSOLIDATE:
            if strong_sell:
                decision = "WATCH"
                reason = f"【否决】强市趋势破坏({strong_sig.sell_signals})"
            elif weak_rsi_overbuy:
                decision = "WATCH"
                reason = f"【否决】弱市RSI超买({weak_sig.rsi_6:.1f}>65)"
            else:
                decision = "BUY"
                reason = f"[{market_status.value}] {' + '.join(primary.buy_signals)}"
        elif market_status == MarketStatus.STRONG:
            if cons_sig.rsi_14 > 60:
                decision = "WATCH"
                reason = f"【否决】全局RSI({cons_sig.rsi_14:.1f})>60，不追强市"
            else:
                decision = "BUY"
                reason = f"[{market_status.value}] {' + '.join(primary.buy_signals)}"
        else:
            decision = "BUY"
            reason = f"[{market_status.value}] {' + '.join(primary.buy_signals)}"
    else:
        decision = "WATCH"
        reason = f"信号不足[{primary.buy_count}/2]"

    return UnifiedSignal(
        code=code,
        name=name,
        price=current_price,
        change_pct=realtime.get("change_pct", 0),
        timestamp=realtime.get("timestamp", ""),
        weak=weak_sig,
        strong=strong_sig,
        consolidate=cons_sig,
        market_status=market_status,
        primary_decision=decision,
        primary_reason=reason,
        buy_count=buy_count,
        sell_count=sell_count,
        buy_signals_detail=buy_detail,
        sell_signals_detail=sell_detail,
        position_ratio=position_ratio,
        atr=cons_sig.atr if cons_sig else 0.0,
        rsi_6=weak_sig.rsi_6 if weak_sig else 50.0,
        ma5=strong_sig.ma5 if strong_sig else 0.0,
        ma10=strong_sig.ma10 if strong_sig else 0.0,
        ma20=cons_sig.bb_middle if cons_sig else 0.0,
    )


# ============================================================================
# 兼容层：SignalCounter 接口代理（保持向后兼容）
# ============================================================================

class SignalCounter:
    """
    兼容层：对内调用 analyze_unified，对外保持旧接口
    """

    def count_signals(self, history, realtime, market_status=None, buy_price=0):
        """旧接口，直接透传给 analyze_unified"""
        if market_status is None:
            market_status = MarketStatus.CONSOLIDATE
        return analyze_unified(history, realtime, market_status, buy_price)

    def _make_decision(self, signal, buy_price):
        """兼容：直接返回 primary_decision"""
        return signal.primary_decision

    def _calc_position_ratio(self, signal, market_status=None):
        """兼容：直接返回 position_ratio"""
        return signal.position_ratio
