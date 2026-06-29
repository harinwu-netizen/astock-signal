# -*- coding: utf-8 -*-
"""
三层决策路由 (v5.0 架构 + v6.13 静默修复)

架构(v5.0 三个独立信号系统 + 智能路由):
- 弱市 → 弱市反弹系统(快进快出,RSI+布林+量能)
- 强市 → 强市趋势系统(趋势跟随,MA+MACD+量价)
- 震荡 → 震荡波段系统(高抛低吸,布林+RSI+量比)

v6.13 (2026-06-14) 静默修复叠加:
- 资金流失败时 BUY 保留 + 降仓 50%(替代 WATCH 降级)
- amount<1000 不再静默 continue,改写告警
- 止损独立检查(不依赖 actionable_signals)

每个系统独立计算信号，最终决策由路由层综合决定：
  1. 优先使用当前市场状态对应的系统
  2. 其他系统的信号可作为否决信号
  3. 止损信号拥有最高优先级（P0，不可绕过）
"""

from dataclasses import dataclass, field
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)
from models.signal import MarketStatus, Decision as EnumDecision
from indicators.signal_weak import analyze_weak, WeakSignal
from indicators.signal_strong import analyze_strong, StrongSignal
from indicators.signal_consolidate import analyze_consolidate, ConsolidateSignal
from data_provider.money_flow import get_money_flow, MoneyFlowData


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
    market_change_pct: float = 0.0

    # 资金流数据（可选，失败时为 None）
    money_flow: Optional[MoneyFlowData] = None

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
    def trend_status(self):
        """兼容：基于市场状态的简单趋势判断"""
        from models.signal import TrendStatus
        mapping = {
            MarketStatus.STRONG: TrendStatus.STRONG_BULL,
            MarketStatus.WEAK: TrendStatus.WEAK_BEAR,
            MarketStatus.CONSOLIDATE: TrendStatus.CONSOLIDATION,
        }
        return mapping.get(self.market_status, TrendStatus.CONSOLIDATION)

    def get_trend_emoji(self) -> str:
        """获取趋势emoji"""
        mapping = {
            "STRONG_BULL": "🟢⬆️",
            "BULL": "🟢",
            "WEAK_BULL": "🟡⬆️",
            "CONSOLIDATION": "🟡",
            "WEAK_BEAR": "🔴⬇️",
            "BEAR": "🔴",
            "STRONG_BEAR": "🔴⬇️",
        }
        return mapping.get(str(self.trend_status).split('.')[-1], "⚪")

    def get_decision_emoji(self) -> str:
        """兼容：获取决策emoji"""
        mapping = {
            "BUY": "🟢",
            "HOLD": "🟡",
            "SELL": "🔴",
            "WATCH": "⚪",
            "STOP_LOSS": "🚨",
            "TAKE_PROFIT": "🎯",
        }
        return mapping.get(self.primary_decision, "⚪")

    @property
    def macd_dif(self) -> float:
        """兼容：MACD DIF值"""
        return self.strong.dif if self.strong else 0.0

    @property
    def macd_dea(self) -> float:
        """兼容：MACD DEA值"""
        return getattr(self.strong, 'dea', 0.0)

    @property
    def atr_stop_loss(self) -> float:
        """兼容：ATR止损价格"""
        if self.atr > 0:
            return self.price - self.atr * 2.0
        return self.price * 0.95

    @property
    def take_profit_price(self) -> float:
        """兼容：止盈价格"""
        if self.atr > 0:
            return self.price + self.atr * 3.0
        return self.price * 1.10

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
    skip_money_flow: bool = False,
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
            market_change_pct=realtime.get("market_change_pct", 0),
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

    # ===== 资金流检查 (v6.14 改造) =====
    # 原逻辑：每只股票都调 get_money_flow()，导致妙想配额浪费 93%
    # 新逻辑：BUY 信号验证推迟到 _handle_buy_signals 阶段（按需调用）
    # 此处保留 mf_data / mf_fetch_failed 变量定义，下方资金面否决代码保持兼容
    mf_data = None
    mf_fetch_failed = False
    if not skip_money_flow:
        # v6.14: 资金流查询从"扫描时无条件调用"改为"BUY 触发后验证"
        # 调用方: main.py::_handle_buy_signals 调用 verify_buy_with_money_flow()
        # 此处不调用，下方 mf_data / mf_fetch_failed 保持 None/False
        pass

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

    # ===== 资金流否决机制（v6.13 改造：失败=保留信号+降仓位，不进 WATCH）=====
    # 旧逻辑（v6.3 引入 P1）：资金流获取失败时 BUY 降级为 WATCH
    # 问题（2026-06-14 发现）：6/3-6/12 几乎全天资金流失败 → 所有 BUY 静默降级
    #   → 模拟交易 0 笔新交易（虽然有 101 次 BUY 决策但都被 WATCH 吃掉）
    # 新逻辑：保留 BUY 决策 + position_ratio * 0.5（保守降仓）+ log + outbox 告警
    if mf_fetch_failed:
        if decision == "BUY":
            # 保留 BUY 决策，但建议仓位降 50%
            if position_ratio > 0:
                position_ratio = position_ratio * 0.5
            else:
                position_ratio = 0.05  # 默认 5% (超保守)
            # 不修改 decision, 让 _handle_buy_signals 继续处理
            # 但附加 warning 让海赟知道资金流有风险
            reason = f"{reason} ⚠️[资金流缺失,建议降仓至{position_ratio:.1%}]"
        elif decision in ("HOLD", "TAKE_PROFIT"):
            # 持仓中断言风险保留，但追加警告
            reason = f"{reason} ⚠️资金流数据获取失败"
    elif mf_data is not None:
        # 资金流获取成功时：按原逻辑否决
        if decision == "BUY" and mf_data.main_net < 0:
            decision = "WATCH"
            reason = f"【资金否决】{mf_data.veto_reason()}"
        elif decision == "SELL" and mf_data.main_net < 0:
            reason = f"{reason} + {mf_data.veto_reason()}"

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
        market_change_pct=realtime.get("market_change_pct", 0),
        money_flow=mf_data,
    )


# ============================================================================
# 兼容层：SignalCounter 接口代理（保持向后兼容）
# ============================================================================

class SignalCounter:
    """
    兼容层：对内调用 analyze_unified，对外保持旧接口
    """

    def count_signals(self, history, realtime, market_status=None, buy_price=0, skip_money_flow=False):
        """旧接口，直接透传给 analyze_unified"""
        if market_status is None:
            market_status = MarketStatus.CONSOLIDATE
        return analyze_unified(history, realtime, market_status, buy_price, skip_money_flow)

    def _make_decision(self, signal, buy_price):
        """兼容：直接返回 primary_decision"""
        return signal.primary_decision

    def _calc_position_ratio(self, signal, market_status=None):
        """兼容：直接返回 position_ratio"""
        return signal.position_ratio


# ============================================================================
# v6.14: BUY 信号资金流验证（按需调用）
# ============================================================================

def verify_buy_with_money_flow(code: str, name: str = "") -> tuple:
    """
    BUY 信号触发后，调用资金流进行二次验证（v6.14 新增）

    设计目标：妙想配额从 ~100次/日 降到 ~10次/日
    - 扫描时不再无条件调用资金流
    - 仅当决策=BUY 时才查资金流验证
    - 验证失败时返回 False，由调用方决定是否记入 evolution 负样本

    Args:
        code: 股票代码 (sz000629 / sh600519)
        name: 股票名称（可选，辅助日志）

    Returns:
        (passed: bool, reason: str, mf_data: MoneyFlowData or None)
        - passed=True: 资金面验证通过 / 资金流缺失（v6.13 兼容行为）
        - passed=False: 资金面背离，建议跳过本次 BUY
    """
    try:
        mf = get_money_flow(code, name)
    except Exception as e:
        logger.warning(f"[MoneyFlow] 验证异常 (code={code}): {e}")
        # v6.13 兼容：异常时按"保留 BUY + 降仓"处理
        return True, "⚠️资金流异常(建议降仓50%)", None

    if mf is None:
        # 资金流获取失败（端点全部触限）— v6.13 行为：保留 BUY + 降仓
        return True, "⚠️资金流缺失(建议降仓50%)", None

    # 资金流获取成功：验证主力/大单方向
    if mf.main_net > 0 or mf.big_net > 0:
        return True, f"✅资金面验证通过({mf.signal})", mf
    else:
        # 资金面背离：建议跳过
        return False, f"❌资金面否决: 主力净流出{mf.main_net:+.0f}万, 大单{mf.big_net:+.0f}万", mf
