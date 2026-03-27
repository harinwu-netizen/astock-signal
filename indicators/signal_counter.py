# -*- coding: utf-8 -*-
"""
信号计数器
核心模块：计算10个买入信号 + 6个卖出信号
"""

import logging
from typing import List, Tuple, Optional
from models.signal import (
    Signal, RealTimeSignal, TrendStatus, MarketStatus, Decision
)
from indicators.ma import calc_all_ma, check_ma_alignment, check_price_ma_support, calc_bias
from indicators.macd import calc_macd, check_macd_signals
from indicators.rsi import calc_rsi, check_rsi_signals
from indicators.atr import calc_atr, calc_atr_stop_loss, calc_take_profit

logger = logging.getLogger(__name__)


class SignalCounter:
    """
    信号计数器

    10个买入信号:
      1. MA多头排列    MA5>MA10>MA20
      2. 价格回踩MA5   收盘价<=MA5*1.01
      3. 价格回踩MA10  收盘价<=MA10*1.01
      4. MACD金叉      DIF上穿DEA
      5. MACD零轴上方  DIF>0 且 DEA>0
      6. RSI超卖反弹   RSI(6)<30 且拐头向上
      7. 缩量回调      成交量<5日均量*0.7
      8. 乖离率修复    bias_MA5>3%，回调中
      9. 筹码集中      90%筹码集中度<15%（暂用成交量比替代）
     10. 大盘配合      上证MA5>MA10 多头

    6个卖出信号:
      1. MA空头排列   MA5<MA10<MA20
      2. 乖离率过大    收盘价偏离MA5>5%
      3. MACD死叉     DIF下穿DEA
      4. RSI超买      RSI(6)>75
      5. 放量破位     成交量>5日均量*1.8 且跌破支撑
      6. ATR止损触发  收盘价<买入价-2*ATR
    """

    def __init__(self):
        self.buy_signal_names = [
            "MA多头排列",
            "价格回踩MA5",
            "价格回踩MA10",
            "MACD金叉",
            "MACD零轴上方",
            "RSI超卖反弹",
            "缩量回调",
            "乖离率修复",
            "筹码集中",
            "大盘配合",
        ]

        self.sell_signal_names = [
            "MA空头排列",
            "乖离率过大(追高)",
            "MACD死叉",
            "RSI超买",
            "放量破位",
            "ATR止损触发",
        ]

    def count_signals(
        self,
        history: List[dict],
        realtime: dict,
        market_status: MarketStatus = MarketStatus.CONSOLIDATE,
        buy_price: float = 0,  # 持仓成本价，止损检查用
    ) -> RealTimeSignal:
        """
        计算一只股票的全部信号

        Args:
            history: 历史K线列表，每项包含 date/open/high/low/close/volume
            realtime: 实时行情dict
            market_status: 大盘状态
            buy_price: 持仓买入价（用于ATR止损检查）

        Returns:
            RealTimeSignal 对象
        """
        if not history or not realtime:
            return self._empty_signal(realtime)

        # 提取数据
        closes = [h["close"] for h in history]
        highs = [h["high"] for h in history]
        lows = [h["low"] for h in history]
        volumes = [h["volume"] for h in history]

        current_price = realtime.get("price", closes[-1] if closes else 0)
        code = realtime.get("code", "")
        name = realtime.get("name", code)

        # ===== 计算各项指标 =====
        ma_data = calc_all_ma(closes)
        ma5, ma10, ma20 = ma_data["ma5"], ma_data["ma10"], ma_data["ma20"]
        bias_ma5 = ma_data["bias_ma5"]

        # MACD（前一日数据用于金叉判断）
        prev_dif, prev_dea = 0.0, 0.0
        if len(closes) >= 27:
            prev_closes = closes[:-1]
            prev_dif, prev_dea, _ = calc_macd(prev_closes)
        dif, dea, macd_bar = calc_macd(closes)

        # RSI
        prev_rsi = calc_rsi(closes[:-1], 6) if len(closes) > 6 else 50.0
        rsi = calc_rsi(closes, 6)

        # ATR
        atr = calc_atr(highs, lows, closes)

        # 成交量分析
        volume = realtime.get("volume", volumes[-1] if volumes else 0)
        vol_ma5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else volume
        vol_ratio = volume / vol_ma5 if vol_ma5 > 0 else 1.0

        # ===== 均线趋势 =====
        is_bullish, ma_desc = check_ma_alignment(ma5, ma10, ma20)
        trend = TrendStatus.BULL if is_bullish else TrendStatus.BEAR
        if "多头" not in ma_desc and "空头" not in ma_desc:
            trend = TrendStatus.CONSOLIDATION

        # ===== 计算买入信号 =====
        buy_signals = []
        buy_triggered = []

        # 信号1: MA多头排列
        if is_bullish and ma5 > 0 and ma10 > 0 and ma20 > 0:
            buy_triggered.append(True)
            buy_signals.append(Signal(name=self.buy_signal_names[0], triggered=True, reason=ma_desc))
        else:
            buy_signals.append(Signal(name=self.buy_signal_names[0], triggered=False))

        # 信号2: 价格回踩MA5
        if ma5 > 0 and current_price <= ma5 * 1.01 and current_price >= ma5 * 0.98:
            buy_triggered.append(True)
            buy_signals.append(Signal(
                name=self.buy_signal_names[1], triggered=True,
                reason=f"价格{current_price}回踩MA5({ma5:.2f})"
            ))
        else:
            buy_signals.append(Signal(name=self.buy_signal_names[1], triggered=False))

        # 信号3: 价格回踩MA10
        if ma10 > 0 and current_price <= ma10 * 1.01 and current_price >= ma10 * 0.97:
            buy_triggered.append(True)
            buy_signals.append(Signal(
                name=self.buy_signal_names[2], triggered=True,
                reason=f"价格{current_price}回踩MA10({ma10:.2f})"
            ))
        else:
            buy_signals.append(Signal(name=self.buy_signal_names[2], triggered=False))

        # 信号4: MACD金叉
        golden_cross, _, macd_desc = check_macd_signals(dif, dea, macd_bar, prev_dif, prev_dea)
        if golden_cross:
            buy_triggered.append(True)
            buy_signals.append(Signal(name=self.buy_signal_names[3], triggered=True, reason=macd_desc))
        else:
            buy_signals.append(Signal(name=self.buy_signal_names[3], triggered=False))

        # 信号5: MACD零轴上方
        if dif > 0 and dea > 0:
            buy_triggered.append(True)
            buy_signals.append(Signal(
                name=self.buy_signal_names[4], triggered=True,
                reason=f"DIF={dif:.4f} DEA={dea:.4f} 在零轴上方"
            ))
        else:
            buy_signals.append(Signal(name=self.buy_signal_names[4], triggered=False))

        # 信号6: RSI超卖反弹
        oversold_rebound, rsi_desc = check_rsi_signals(rsi, prev_rsi)
        if oversold_rebound:
            buy_triggered.append(True)
            buy_signals.append(Signal(name=self.buy_signal_names[5], triggered=True, reason=rsi_desc))
        else:
            buy_signals.append(Signal(name=self.buy_signal_names[5], triggered=False))

        # 信号7: 缩量回调（量比<0.7）
        if vol_ratio < 0.7 and bias_ma5 > 0:
            buy_triggered.append(True)
            buy_signals.append(Signal(
                name=self.buy_signal_names[6], triggered=True,
                reason=f"缩量回调(量比={vol_ratio:.2f})"
            ))
        else:
            buy_signals.append(Signal(name=self.buy_signal_names[6], triggered=False))

        # 信号8: 乖离率修复（偏好在3-5%区间回调，不是追高）
        if 2 < bias_ma5 < 5 and not is_bullish:
            buy_triggered.append(True)
            buy_signals.append(Signal(
                name=self.buy_signal_names[7], triggered=True,
                reason=f"乖离率{bias_ma5:.1f}%，修复中"
            ))
        else:
            buy_signals.append(Signal(name=self.buy_signal_names[7], triggered=False))

        # 信号9: 筹码集中（简化：换手率低表示筹码集中）
        turnover = realtime.get("turnover_rate", 0)
        if 0 < turnover < 5:  # 换手率低于5%表示筹码相对集中
            buy_triggered.append(True)
            buy_signals.append(Signal(
                name=self.buy_signal_names[8], triggered=True,
                reason=f"换手率{turnover:.2f}%，筹码集中"
            ))
        else:
            buy_signals.append(Signal(name=self.buy_signal_names[8], triggered=False))

        # 信号10: 大盘配合
        if market_status == MarketStatus.STRONG:
            buy_triggered.append(True)
            buy_signals.append(Signal(
                name=self.buy_signal_names[9], triggered=True,
                reason="大盘处于强势状态"
            ))
        else:
            buy_signals.append(Signal(name=self.buy_signal_names[9], triggered=False))

        buy_count = len(buy_triggered)
        buy_detail = [s.name for s in buy_signals if s.triggered]

        # ===== 计算卖出信号 =====
        sell_signals = []
        sell_triggered = []

        # 信号1: MA空头排列
        is_bearish = ma5 < ma10 < ma20
        if is_bearish:
            sell_triggered.append(True)
            sell_signals.append(Signal(name=self.sell_signal_names[0], triggered=True, reason=ma_desc))
        else:
            sell_signals.append(Signal(name=self.sell_signal_names[0], triggered=False))

        # 信号2: 乖离率过大（追高）
        if bias_ma5 > 5:
            sell_triggered.append(True)
            sell_signals.append(Signal(
                name=self.sell_signal_names[1], triggered=True,
                reason=f"乖离率{bias_ma5:.1f}%，价格偏离过大"
            ))
        else:
            sell_signals.append(Signal(name=self.sell_signal_names[1], triggered=False))

        # 信号3: MACD死叉
        death_cross = prev_dif > prev_dea and dif < dea
        if death_cross:
            sell_triggered.append(True)
            sell_signals.append(Signal(name=self.sell_signal_names[2], triggered=True, reason=macd_desc))
        else:
            sell_signals.append(Signal(name=self.sell_signal_names[2], triggered=False))

        # 信号4: RSI超买
        if rsi > 75:
            sell_triggered.append(True)
            sell_signals.append(Signal(
                name=self.sell_signal_names[3], triggered=True,
                reason=f"RSI={rsi:.1f}，超买区域"
            ))
        else:
            sell_signals.append(Signal(name=self.sell_signal_names[3], triggered=False))

        # 信号5: 放量破位（量比>1.8且跌破MA10）
        if vol_ratio > 1.8 and ma10 > 0 and current_price < ma10 * 0.98:
            sell_triggered.append(True)
            sell_signals.append(Signal(
                name=self.sell_signal_names[4], triggered=True,
                reason=f"放量{vol_ratio:.1f}倍跌破MA10"
            ))
        else:
            sell_signals.append(Signal(name=self.sell_signal_names[4], triggered=False))

        # 信号6: ATR止损触发（针对持仓）
        if buy_price > 0 and atr > 0:
            stop_price = buy_price - 2 * atr
            if current_price <= stop_price:
                sell_triggered.append(True)
                sell_signals.append(Signal(
                    name=self.sell_signal_names[5], triggered=True,
                    reason=f"触发ATR止损({stop_price:.2f})"
                ))
            else:
                sell_signals.append(Signal(name=self.sell_signal_names[5], triggered=False))
        else:
            sell_signals.append(Signal(name=self.sell_signal_names[5], triggered=False))

        sell_count = len(sell_triggered)
        sell_detail = [s.name for s in sell_signals if s.triggered]

        # ===== ATR止损/止盈价 =====
        atr_stop = calc_atr_stop_loss(current_price, atr)
        atr_take_profit = calc_take_profit(current_price, atr)

        # ===== 构建结果 =====
        signal = RealTimeSignal(
            code=code,
            name=name,
            price=current_price,
            change_pct=realtime.get("change_pct", 0),
            timestamp=realtime.get("timestamp", ""),
            ma5=ma5,
            ma10=ma10,
            ma20=ma20,
            ma60=ma_data.get("ma60", 0),
            bias_ma5=bias_ma5,
            macd_dif=dif,
            macd_dea=dea,
            macd_bar=macd_bar,
            rsi_6=rsi,
            atr=atr,
            volume=volume,
            volume_ma5=vol_ma5,
            volume_ratio=vol_ratio,
            chip_concentration=turnover if turnover > 0 else 0,
            trend_status=trend,
            buy_signals=buy_signals,
            buy_count=buy_count,
            sell_signals=sell_signals,
            sell_count=sell_count,
            atr_stop_loss=atr_stop,
            take_profit_price=atr_take_profit,
            buy_signals_detail=buy_detail,
            sell_signals_detail=sell_detail,
            market_status=market_status,
            market_change_pct=realtime.get("market_change_pct", 0),
        )

        # ===== 计算决策 =====
        signal.decision = self._make_decision(signal, buy_price)
        signal.position_ratio = self._calc_position_ratio(signal.buy_count)

        return signal

    def _make_decision(self, signal: RealTimeSignal, buy_price: float) -> Decision:
        """根据信号数量决定操作"""
        buy = signal.buy_count
        sell = signal.sell_count

        # 止损优先
        if buy_price > 0 and signal.price <= signal.atr_stop_loss:
            return Decision.STOP_LOSS

        # 止盈
        if buy_price > 0 and signal.price >= signal.take_profit_price:
            return Decision.TAKE_PROFIT

        # 卖出信号优先
        if sell >= 3:
            return Decision.SELL

        # 弱势市场严格限制买入
        if signal.market_status == MarketStatus.WEAK:
            if buy >= 7:  # 弱势市场要更强的信号才考虑买入
                return Decision.HOLD
            return Decision.WATCH

        # 买入信号判断
        if buy >= 5:
            return Decision.BUY

        # 观望
        return Decision.WATCH

    def _calc_position_ratio(self, buy_count: int) -> float:
        """根据信号数量计算建议仓位"""
        if buy_count >= 8:
            return 1.0
        elif buy_count == 7:
            return 0.8
        elif buy_count == 6:
            return 0.5
        elif buy_count == 5:
            return 0.3
        return 0.0

    def _empty_signal(self, realtime: dict) -> RealTimeSignal:
        """返回空信号"""
        return RealTimeSignal(
            code=realtime.get("code", ""),
            name=realtime.get("name", ""),
            price=realtime.get("price", 0),
            change_pct=realtime.get("change_pct", 0),
            timestamp=realtime.get("timestamp", ""),
        )
