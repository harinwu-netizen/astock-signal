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

        # ===== ATR止损/止盈价（根据市场状态使用不同倍数）=====
        # 注意：这里仅用于展示在信号里；实际风控以 position 记录的值和你账户的 _make_decision 为准
        if market_status == MarketStatus.WEAK:
            _atr_mult = 1.5
        elif market_status == MarketStatus.STRONG:
            _atr_mult = 3.0
        else:
            _atr_mult = 2.0
        atr_stop = calc_atr_stop_loss(current_price, atr, _atr_mult)
        atr_take_profit = calc_take_profit(current_price, atr)

        # ===== 计算弱市反弹信号（3指标，满足2个=可考虑买入）=====
        rebound_signals, rebound_count = self._count_rebound_signals(
            rsi, prev_rsi, closes, current_price, volume, vol_ma5
        )

        # ===== 计算强市趋势信号（3指标，满足2个=可考虑买入）=====
        # 计算前一日MACD柱（用于判断是否扩散）
        prev_macd_dif, prev_macd_dea, prev_macd_bar = 0.0, 0.0, 0.0
        if len(closes) >= 2:
            prev_macd_dif, prev_macd_dea, prev_macd_bar = calc_macd(closes[:-1])
        trend_signals, trend_count = self._count_trend_signals(
            ma5, ma10, ma20, dif, macd_bar, prev_macd_bar,
            volume, vol_ma5, current_price, closes[-1] if len(closes) >= 1 else 0
        )

        # ===== 计算震荡市波段信号（v4.3新增）=====
        # 计算布林上轨（震荡市止盈用）
        if len(closes) >= 20:
            std20 = (sum((c - ma20) ** 2 for c in closes[-20:]) / 20) ** 0.5
            bb_upper = ma20 + 2 * std20
        else:
            bb_upper = ma20 * 1.1

        consolidate_buy_signals, consolidate_buy_count = self._count_consolidate_buy_signals(
            rsi, ma10, closes, current_price, volume, vol_ma5
        )
        consolidate_sell_signals, consolidate_sell_count = self._count_consolidate_sell_signals(
            rsi, ma20, bb_upper, current_price
        )

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
            rebound_signals=rebound_signals,
            rebound_count=rebound_count,
            trend_signals=trend_signals,
            trend_count=trend_count,
            consolidate_buy_signals=consolidate_buy_signals,
            consolidate_buy_count=consolidate_buy_count,
            consolidate_sell_signals=consolidate_sell_signals,
            consolidate_sell_count=consolidate_sell_count,
            bb_upper=bb_upper,
            atr_stop_loss=atr_stop,
            take_profit_price=atr_take_profit,
            buy_signals_detail=buy_detail,
            sell_signals_detail=sell_detail,
            market_status=market_status,
            market_change_pct=realtime.get("market_change_pct", 0),
        )

        # ===== 计算决策 =====
        signal.decision = self._make_decision(signal, buy_price)
        signal.position_ratio = self._calc_position_ratio(signal, market_status)

        return signal

    def _make_decision(self, signal: RealTimeSignal, buy_price: float) -> Decision:
        """
        根据市场状态 + 信号决定操作（v4.3）

        强市：趋势3指标满足≥2 + 乖离率≤5% + RSI≤70
        弱市：反弹3指标满足≥2 + MA20止盈
        震荡市（v4.3）：波段3指标满足≥2（RSI适中回调+回踩均线+缩量整固）
        """

        regime = signal.market_status
        current_price = signal.price
        atr = signal.atr
        bias = signal.bias_ma5
        rsi = signal.rsi_6

        # ========== 止损优先（所有市场状态）==========
        if buy_price > 0 and atr > 0:
            if regime == MarketStatus.WEAK:
                weak_stop = buy_price * (1 - 0.03)
                weak_atr_stop = buy_price - 2.0 * atr
                if current_price <= min(weak_stop, weak_atr_stop):
                    return Decision.STOP_LOSS
            elif regime == MarketStatus.STRONG:
                strong_stop = buy_price * (1 - 0.15)
                strong_atr_stop = buy_price - 3.0 * atr
                if current_price <= max(strong_stop, strong_atr_stop):
                    return Decision.STOP_LOSS
            else:
                # 震荡市：-5% 或 2x ATR
                cons_stop_pct = buy_price * (1 - 0.05)
                cons_atr_stop = buy_price - 2.0 * atr
                if current_price <= min(cons_stop_pct, cons_atr_stop):
                    return Decision.STOP_LOSS

        # ========== 止盈 ==========
        if buy_price > 0:
            if regime == MarketStatus.WEAK:
                ma20 = signal.ma20
                if ma20 > 0 and current_price >= ma20:
                    return Decision.TAKE_PROFIT
            elif regime == MarketStatus.STRONG:
                if current_price >= buy_price * 1.25:
                    return Decision.TAKE_PROFIT
            else:
                # 震荡市波段止盈：RSI>55 或 触及布林上轨
                if rsi > 55 or (signal.bb_upper > 0 and current_price >= signal.bb_upper * 0.98):
                    return Decision.TAKE_PROFIT

        # ========== 弱市卖出 ==========
        if regime == MarketStatus.WEAK and buy_price > 0:
            if rsi > 65:
                return Decision.SELL

        # ========== 震荡市波段卖出信号 ==========
        if regime == MarketStatus.CONSOLIDATE and buy_price > 0:
            if signal.consolidate_sell_count >= 2:
                return Decision.SELL

        # ========== 震荡/强市经典卖出信号 ==========
        sell = signal.sell_count
        if regime == MarketStatus.CONSOLIDATE and sell >= 3:
            return Decision.SELL
        if regime == MarketStatus.STRONG and sell >= 5:
            return Decision.SELL

        # ========== 买入决策 ==========
        if regime == MarketStatus.WEAK:
            if signal.rebound_count >= 2:
                return Decision.BUY
            return Decision.WATCH

        elif regime == MarketStatus.STRONG:
            if signal.trend_count < 2:
                return Decision.WATCH
            if bias > 5 or rsi > 70:  # 追高拦截
                return Decision.WATCH
            return Decision.BUY

        else:
            # 震荡市：波段3指标满足≥2个（v4.3）
            if signal.consolidate_buy_count >= 2:
                return Decision.BUY
            return Decision.WATCH

    def _calc_std(self, values: list) -> float:
        """计算标准差"""
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return variance ** 0.5

    def _calc_position_ratio(self, signal, market_status: MarketStatus = None) -> float:
        """
        根据市场状态 + 信号数量计算建议仓位（v4.3）
        - 弱市：rebound_count → 仓位
        - 震荡市：consolidate_buy_count → 仓位（改用波段信号）
        - 强市：trend_count → 仓位
        """
        if market_status == MarketStatus.WEAK:
            # 弱市：仓位与反弹信号数挂钩，上限30%
            return min(0.1 * max(1, signal.rebound_count), 0.3)

        elif market_status == MarketStatus.STRONG:
            # 强市：趋势信号决定仓位
            count = signal.trend_count
            if count >= 3:
                return 1.0
            elif count == 2:
                return 0.5
            else:
                return 0.0

        else:
            # 震荡市：波段买入信号决定仓位（v4.3 改用 consolidate_buy_count）
            count = signal.consolidate_buy_count
            if count >= 3:
                return 1.0
            elif count == 2:
                return 0.5
            else:
                return 0.0

    def _count_rebound_signals(
        self,
        rsi: float,
        prev_rsi: float,
        closes: list,
        current_price: float,
        volume: float,
        vol_ma5: float,
    ) -> tuple:
        """
        计算弱市反弹3指标
        指标1: RSI超卖反弹
        指标2: 价格接近布林下轨/前低
        指标3: 缩量企稳

        Returns:
            (signals_list, count)
        """
        signals = []
        triggered = 0

        # 指标①：RSI超卖反弹（RSI<35 且 相比前一日回升）
        if rsi < 35 and rsi > prev_rsi:
            signals.append(Signal(
                name="RSI超卖反弹",
                triggered=True,
                reason=f"RSI={rsi:.1f}<35 且拐头向上(前一日RSI={prev_rsi:.1f})"
            ))
            triggered += 1
        else:
            signals.append(Signal(
                name="RSI超卖反弹",
                triggered=False,
                reason=f"RSI={rsi:.1f} {'≥35' if rsi >= 35 else '未回升'}"
            ))

        # 指标②：价格接近布林下轨/近20日前低
        import numpy as np
        if len(closes) >= 20:
            recent_low = min(closes[-20:])
        else:
            recent_low = min(closes)
        price_near_low = current_price <= recent_low * 1.05

        if price_near_low:
            signals.append(Signal(
                name="价格接近前低",
                triggered=True,
                reason=f"现价{current_price:.2f} ≤ 前低{recent_low:.2f}×1.05={recent_low*1.05:.2f}"
            ))
            triggered += 1
        else:
            signals.append(Signal(
                name="价格接近前低",
                triggered=False,
                reason=f"现价{current_price:.2f} > 前低{recent_low:.2f}×1.05={recent_low*1.05:.2f}"
            ))

        # 指标③：缩量企稳（成交量 < 5日均量 × 0.75）
        vol_ratio = volume / vol_ma5 if vol_ma5 > 0 else 1.0
        if vol_ratio < 0.75:
            signals.append(Signal(
                name="缩量企稳",
                triggered=True,
                reason=f"量比={vol_ratio:.2f}<0.75，抛压减轻"
            ))
            triggered += 1
        else:
            signals.append(Signal(
                name="缩量企稳",
                triggered=False,
                reason=f"量比={vol_ratio:.2f}≥0.75"
            ))

        return signals, triggered

    def _count_trend_signals(
        self,
        ma5: float,
        ma10: float,
        ma20: float,
        dif: float,
        macd_bar: float,
        prev_macd_bar: float,
        volume: float,
        vol_ma5: float,
        current_price: float,
        prev_close: float,
    ) -> tuple:
        """
        计算强市趋势3指标
        指标1: 均线多头排列
        指标2: MACD扩散
        指标3: 量价齐升

        Returns:
            (signals_list, count)
        """
        signals = []
        triggered = 0

        # 指标①：均线多头排列（MA5>MA10>MA20）
        if ma5 > ma10 > ma20:
            signals.append(Signal(
                name="均线多头排列",
                triggered=True,
                reason=f"MA5={ma5:.2f}>MA10={ma10:.2f}>MA20={ma20:.2f}"
            ))
            triggered += 1
        else:
            signals.append(Signal(
                name="均线多头排列",
                triggered=False,
                reason=f"MA5={ma5:.2f},MA10={ma10:.2f},MA20={ma20:.2f} 未满足多头"
            ))

        # 指标②：MACD扩散（DIF>0 且 MACD柱比前一日放大）
        macd_expanding = dif > 0 and macd_bar > prev_macd_bar
        if macd_expanding:
            signals.append(Signal(
                name="MACD扩散",
                triggered=True,
                reason=f"DIF={dif:.4f}>0 且 MACD柱放大({macd_bar:.4f} > {prev_macd_bar:.4f})"
            ))
            triggered += 1
        else:
            signals.append(Signal(
                name="MACD扩散",
                triggered=False,
                reason=f"DIF={'>0' if dif > 0 else '≤0'}, MACD柱{'放大' if macd_bar > prev_macd_bar else '未放大'}"
            ))

        # 指标③：量价齐升（成交量>1.3x均量 且 收盘上涨）
        vol_ratio = volume / vol_ma5 if vol_ma5 > 0 else 1.0
        price_rising = prev_close > 0 and current_price > prev_close
        volume_price_rising = vol_ratio > 1.3 and price_rising

        if volume_price_rising:
            signals.append(Signal(
                name="量价齐升",
                triggered=True,
                reason=f"量比={vol_ratio:.2f}>1.3 且上涨{((current_price-prev_close)/prev_close*100):.2f}%"
            ))
            triggered += 1
        else:
            reason_parts = []
            if vol_ratio <= 1.3:
                reason_parts.append(f"量比={vol_ratio:.2f}≤1.3")
            if not price_rising:
                reason_parts.append(f"价格未上涨")
            signals.append(Signal(
                name="量价齐升",
                triggered=False,
                reason=" ".join(reason_parts) if reason_parts else "未满足"
            ))

        return signals, triggered

    # ==================================================================== #
    #  震荡市波段信号（v4.3新增）
    # ==================================================================== #

    def _count_consolidate_buy_signals(
        self,
        rsi: float,
        ma10: float,
        closes: list,
        current_price: float,
        volume: float,
        vol_ma5: float,
    ) -> tuple:
        """
        震荡市买入波段信号（3指标满足2个）
        ① RSI在30~50区间（适中回调，不是超卖也不是超买）
        ② 价格回踩MA10均线（支撑位）
        ③ 缩量整固（量比<0.8，抛压轻）
        """
        signals = []
        triggered = 0

        # 指标①：RSI适中回调（30~50区间）
        if 30 <= rsi <= 50:
            signals.append(Signal(
                name="RSI适中回调",
                triggered=True,
                reason=f"RSI={rsi:.1f}在[30,50]区间，回调到位"
            ))
            triggered += 1
        else:
            signals.append(Signal(
                name="RSI适中回调",
                triggered=False,
                reason=f"RSI={rsi:.1f}不在[30,50]区间"
            ))

        # 指标②：价格回踩MA10（≤1.02倍）
        price_near_ma10 = current_price <= ma10 * 1.02
        if price_near_ma10:
            signals.append(Signal(
                name="价格回踩均线",
                triggered=True,
                reason=f"现价{current_price:.2f}≤MA10×1.02={ma10*1.02:.2f}"
            ))
            triggered += 1
        else:
            signals.append(Signal(
                name="价格回踩均线",
                triggered=False,
                reason=f"现价{current_price:.2f}>MA10×1.02={ma10*1.02:.2f}"
            ))

        # 指标③：缩量整固（量比<0.8）
        vol_ratio = volume / vol_ma5 if vol_ma5 > 0 else 1.0
        if vol_ratio < 0.8:
            signals.append(Signal(
                name="缩量整固",
                triggered=True,
                reason=f"量比={vol_ratio:.2f}<0.8，抛压轻"
            ))
            triggered += 1
        else:
            signals.append(Signal(
                name="缩量整固",
                triggered=False,
                reason=f"量比={vol_ratio:.2f}≥0.8"
            ))

        return signals, triggered

    def _count_consolidate_sell_signals(
        self,
        rsi: float,
        ma20: float,
        bb_upper: float,
        current_price: float,
    ) -> tuple:
        """
        震荡市卖出波段信号（3指标满足2个）
        ① RSI>55（回到正常/偏强区间）
        ② 价格触及布林上轨（区间上沿）
        ③ 持仓超5天（时间止损）
        注意：持仓天数由外部持仓状态决定，此处只提供前两个信号
        """
        signals = []
        triggered = 0

        # 指标①：RSI回升>55
        if rsi > 55:
            signals.append(Signal(
                name="RSI回升止盈",
                triggered=True,
                reason=f"RSI={rsi:.1f}>55，回到正常区间"
            ))
            triggered += 1
        else:
            signals.append(Signal(
                name="RSI回升止盈",
                triggered=False,
                reason=f"RSI={rsi:.1f}≤55"
            ))

        # 指标②：价格触及布林上轨
        if bb_upper > 0 and current_price >= bb_upper * 0.98:
            signals.append(Signal(
                name="触及布林上轨",
                triggered=True,
                reason=f"现价{current_price:.2f}≥布林上轨×0.98={bb_upper*0.98:.2f}"
            ))
            triggered += 1
        else:
            signals.append(Signal(
                name="触及布林上轨",
                triggered=False,
                reason=f"布林上轨×0.98={bb_upper*0.98:.2f}，现价{current_price:.2f}"
            ))

        # 指标③：持仓超5天（由持仓状态决定，此处预判）
        # 持仓天数无法从信号本身判断，留空
        signals.append(Signal(
            name="持仓超5天",
            triggered=False,
            reason="由持仓状态判断（时间止损）"
        ))

        return signals, triggered

    def _empty_signal(self, realtime: dict) -> RealTimeSignal:
        """返回空信号"""
        return RealTimeSignal(
            code=realtime.get("code", ""),
            name=realtime.get("name", ""),
            price=realtime.get("price", 0),
            change_pct=realtime.get("change_pct", 0),
            timestamp=realtime.get("timestamp", ""),
        )
