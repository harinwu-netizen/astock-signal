# -*- coding: utf-8 -*-
"""
信号数据模型
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import List


class TrendStatus(Enum):
    """趋势状态"""
    STRONG_BULL = "强势多头"    # MA5>MA10>MA20且扩大
    BULL = "多头排列"           # MA5>MA10>MA20
    WEAK_BULL = "弱势多头"       # MA5>MA10但MA10<MA20
    CONSOLIDATION = "盘整"      # 均线缠绕
    WEAK_BEAR = "弱势空头"       # MA5<MA10但MA10>MA20
    BEAR = "空头排列"           # MA5<MA10<MA20
    STRONG_BEAR = "强势空头"     # MA5<MA10<MA20且扩大


class MarketStatus(Enum):
    """大盘状态"""
    STRONG = "强势"   # 上证MA5>MA10>MA20，涨跌> -1%
    WEAK = "弱势"     # 上证跌破双均线 or 跌幅>2%
    CONSOLIDATE = "震荡"  # 中间状态


class Decision(Enum):
    """交易决策"""
    BUY = "BUY"       # 买入
    HOLD = "HOLD"     # 持有
    SELL = "SELL"     # 卖出
    WATCH = "WATCH"   # 观望
    STOP_LOSS = "STOP_LOSS"  # 止损
    TAKE_PROFIT = "TAKE_PROFIT"  # 止盈


@dataclass
class Signal:
    """单条信号"""
    name: str         # 信号名称，如 "MA多头排列"
    triggered: bool   # 是否触发
    reason: str = ""  # 触发原因

    def to_dict(self):
        return asdict(self)


@dataclass
class RealTimeSignal:
    """实时信号（单只股票分析结果）"""
    code: str              # 股票代码
    name: str              # 股票名称
    price: float           # 当前价
    change_pct: float      # 涨跌幅(%)
    timestamp: datetime    # 时间戳

    # ===== 均线数据 =====
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    bias_ma5: float = 0.0  # 乖离率%

    # ===== MACD数据 =====
    macd_dif: float = 0.0
    macd_dea: float = 0.0
    macd_bar: float = 0.0

    # ===== RSI数据 =====
    rsi_6: float = 0.0

    # ===== ATR数据 =====
    atr: float = 0.0

    # ===== 成交量数据 =====
    volume: int = 0          # 成交量（手）
    volume_ma5: float = 0.0  # 5日均量
    volume_ratio: float = 0.0  # 量比

    # ===== 筹码数据 =====
    chip_concentration: float = 0.0  # 90%筹码集中度

    # ===== 趋势状态 =====
    trend_status: TrendStatus = TrendStatus.CONSOLIDATION

    # ===== 十大买入信号 =====
    buy_signals: List[Signal] = field(default_factory=list)
    buy_count: int = 0

    # ===== 六大卖出信号 =====
    sell_signals: List[Signal] = field(default_factory=list)
    sell_count: int = 0

    # ===== 风控指标 =====
    atr_stop_loss: float = 0.0    # ATR止损价
    take_profit_price: float = 0.0 # 止盈价

    # ===== 决策 =====
    decision: Decision = Decision.WATCH
    position_ratio: float = 0.0   # 建议仓位 0.0-1.0

    # ===== 大盘状态 =====
    market_status: MarketStatus = MarketStatus.CONSOLIDATE
    market_change_pct: float = 0.0

    # ===== 大盘信号 =====
    buy_signals_detail: List[str] = field(default_factory=list)
    sell_signals_detail: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trend_status"] = self.trend_status.value
        d["decision"] = self.decision.value
        d["market_status"] = self.market_status.value
        d["buy_signals"] = [s.to_dict() for s in self.buy_signals]
        d["sell_signals"] = [s.to_dict() for s in self.sell_signals]
        return d

    def get_decision_emoji(self) -> str:
        """获取决策emoji"""
        mapping = {
            Decision.BUY: "🟢",
            Decision.HOLD: "🟡",
            Decision.SELL: "🔴",
            Decision.WATCH: "⚪",
            Decision.STOP_LOSS: "🚨",
            Decision.TAKE_PROFIT: "🎯",
        }
        return mapping.get(self.decision, "⚪")

    def get_trend_emoji(self) -> str:
        """获取趋势emoji"""
        mapping = {
            TrendStatus.STRONG_BULL: "🟢⬆️",
            TrendStatus.BULL: "🟢",
            TrendStatus.WEAK_BULL: "🟡⬆️",
            TrendStatus.CONSOLIDATION: "🟡",
            TrendStatus.WEAK_BEAR: "🔴⬇️",
            TrendStatus.BEAR: "🔴",
            TrendStatus.STRONG_BEAR: "🔴⬇️",
        }
        return mapping.get(self.trend_status, "⚪")
