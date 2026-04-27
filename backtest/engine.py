# -*- coding: utf-8 -*-
"""
回测引擎 v4.4
支持：单股 / 多日 / 信号驱动 模拟交易
弱强市自适应：市场状态每日检测，买卖决策由 signal.decision 驱动
"""

import logging
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from config import get_config
from models.signal import Decision, MarketStatus
from indicators.signal_counter import SignalCounter
from indicators.atr import calc_atr, calc_atr_stop_loss
from indicators.market_regime import detect_market_regime, MarketRegime
from data_provider.data_selector import get_selector
from data_provider.data_clean import clean_kline_data
from trading.cost_calculator import (
    calculate_buy_cost, calculate_sell_cost,
    calc_real_buy_price, calc_real_sell_price,
    check_volume_limit,
)

logger = logging.getLogger(__name__)

MAX_POSITIONS = 3


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class BTTrade:
    seq: int
    date: str
    action: str
    code: str
    name: str
    price: float
    quantity: int
    amount: float         # 净成交金额（含手续费）
    commission: float    # 手续费合计
    pnl: float = 0.0
    hold_days: int = 0
    reason: str = ""
    signal_count: int = 0
    rebound_signals: int = 0   # v4.0 弱市反弹信号数
    trend_signals: int = 0     # v4.0 强市趋势信号数
    market_regime: str = ""     # v4.0 开仓时市场状态

    def to_dict(self) -> dict:
        return {
            "seq": self.seq, "date": self.date, "action": self.action,
            "code": self.code, "name": self.name,
            "price": self.price, "quantity": self.quantity,
            "amount": round(self.amount, 2),
            "commission": round(self.commission, 2),
            "pnl": round(self.pnl, 2),
            "hold_days": self.hold_days,
            "reason": self.reason,
            "signal_count": self.signal_count,
            "rebound_signals": self.rebound_signals,
            "trend_signals": self.trend_signals,
            "market_regime": self.market_regime,
        }


@dataclass
class BTPosition:
    """回测持仓（v4.0 支持 regime-aware 参数）"""
    code: str
    name: str
    buy_date: str
    buy_price: float
    buy_commission: float   # 买入手续费（含滑点）
    quantity: int
    stop_loss: float
    take_profit: float
    trailing_stop: float
    atr: float = 0.0
    buy_signals: int = 0

    # v4.0 新增：开仓时市场状态及对应风控参数
    market_regime: MarketStatus = MarketStatus.CONSOLIDATE
    atr_multiplier: float = 2.0          # ATR止损倍数
    stop_loss_pct: float = 10.0         # 亏损止损线（%）
    take_profit_pct: float = 15.0       # 止盈线（%）
    max_hold_days: int = 10             # 最大持仓天数
    rebound_signals: int = 0             # 开仓时反弹信号数
    trend_signals: int = 0              # 开仓时趋势信号数
    ma20_take_profit: float = 0.0     # 弱市MA20止盈目标价（v4.1新增）

    def market_value(self, current_price: float) -> float:
        return current_price * self.quantity * 100

    def cost_basis(self) -> float:
        return self.buy_price * self.quantity * 100 + self.buy_commission

    def unrealized_pnl(self, current_price: float) -> float:
        return self.market_value(current_price) - self.cost_basis()

    def unrealized_pct(self, current_price: float) -> float:
        """未实现盈亏比例（%）"""
        return (current_price - self.buy_price) / self.buy_price * 100


@dataclass
class BacktestResult:
    code: str
    name: str
    start_date: str
    end_date: str
    days: int
    total_return: float = 0.0
    annual_return: float = 0.0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_hold_days: float = 0.0
    trades: List[BTTrade] = field(default_factory=list)   # 卖出记录（指标计算用）
    round_trips: List[dict] = field(default_factory=list)  # 完整买卖配对（含买入）
    equity_curve: List[dict] = field(default_factory=list)
    final_capital: float = 0.0
    initial_capital: float = 0.0

    def to_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name,
            "start_date": self.start_date, "end_date": self.end_date,
            "days": self.days,
            "total_return": round(self.total_return, 2),
            "annual_return": round(self.annual_return, 2),
            "win_rate": round(self.win_rate, 2),
            "profit_loss_ratio": round(self.profit_loss_ratio, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "avg_hold_days": round(self.avg_hold_days, 1),
            "final_capital": round(self.final_capital, 2),
            "initial_capital": round(self.initial_capital, 2),
            "trades": [t.to_dict() for t in self.trades],
            "equity_curve": self.equity_curve,
        }


# ============================================================================
# 回测引擎
# ============================================================================

class BacktestEngine:
    """
    回测引擎 v4.4

    弱强市自适应：
    - 每日检测市场状态（强市/震荡/弱市）
    - 买卖决策完全由 signal.decision 驱动
    - 持仓记录开仓时的 regime，对应不同的风控参数
    """

    def __init__(
        self,
        initial_capital: float = 100000.0,
        max_positions: int = MAX_POSITIONS,
    ):
        self.initial_capital = initial_capital
        self.max_positions = max_positions
        self.counter = SignalCounter()
        self.selector = get_selector()
        self.cfg = get_config()

    def _detect_regime_for_date(
        self,
        date: str,
        index_hist: List[dict],
        index_date_to_idx: dict,
    ) -> MarketStatus:
        """
        给定日期，检测当日的市场状态（v5.7 P2 置信度版）
        用该日之前25日指数数据判断，含置信度计算和低置信震荡市降级
        """
        idx = index_date_to_idx.get(date)
        if idx is None or idx < 25:
            return MarketStatus.CONSOLIDATE

        window = index_hist[max(0, idx - 24):idx + 1]
        if len(window) < 20:
            return MarketStatus.CONSOLIDATE

        try:
            closes = [d["close"] for d in window]
            ma5 = sum(closes[-5:]) / 5
            ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else ma5
            ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else ma10

            close_5d_ago = closes[-6] if len(closes) >= 6 else closes[0]
            change_5d = (closes[-1] - close_5d_ago) / close_5d_ago * 100 if close_5d_ago != 0 else 0

            ma_bull = ma5 > ma10 > ma20
            ma_bear = ma5 < ma10 < ma20

            # ---- P2新增：置信度计算 ----
            ma_spread = abs(ma5 - ma10) / ma10 * 100 if ma10 > 0 else 0
            trend_strength = abs(ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0

            if change_5d > 0 and ma_bull:
                confidence = min(trend_strength / 5.0, 1.0)
                regime = MarketStatus.STRONG
            elif change_5d < -1.5 or ma_bear:
                confidence = min(trend_strength / 5.0, 1.0)
                regime = MarketStatus.WEAK
            else:
                confidence = max(0.0, 1.0 - (ma_spread / 3.0))
                regime = MarketStatus.CONSOLIDATE

            # ---- P2新增：低置信震荡市降级为弱市 ----
            if regime == MarketStatus.CONSOLIDATE and confidence < 0.4:
                regime = MarketStatus.WEAK

            return regime
        except Exception:
            return MarketStatus.CONSOLIDATE

    def _get_regime_params(self, regime: MarketStatus) -> dict:
        """根据市场状态获取对应风控参数"""
        cfg = self.cfg
        if regime == MarketStatus.WEAK:
            return {
                "atr_multiplier": cfg.weak_atr_multiplier,
                "stop_loss_pct": cfg.weak_stop_loss_pct,
                "take_profit_pct": cfg.weak_take_profit_pct,
                "max_hold_days": cfg.weak_max_hold_days,
                "position_pct": cfg.weak_single_position_pct / 100,
                "sell_atr_mult": cfg.weak_atr_multiplier,
            }
        elif regime == MarketStatus.STRONG:
            return {
                "atr_multiplier": cfg.strong_atr_multiplier,
                "stop_loss_pct": cfg.strong_stop_loss_pct,
                "take_profit_pct": cfg.strong_take_profit_pct,
                "max_hold_days": cfg.strong_max_hold_days,
                "position_pct": cfg.strong_single_position_pct / 100,
                "sell_atr_mult": cfg.strong_atr_multiplier,
            }
        else:
            return {
                "atr_multiplier": cfg.consolidate_atr_multiplier,
                "stop_loss_pct": cfg.consolidate_stop_loss_pct,
                "take_profit_pct": cfg.consolidate_take_profit_pct,
                "max_hold_days": cfg.consolidate_max_hold_days,
                "position_pct": cfg.consolidate_single_position_pct / 100,
                "sell_atr_mult": cfg.consolidate_atr_multiplier,
            }

    def run(self, code: str, days: int = 60,
            start_date: str = None, end_date: str = None) -> BacktestResult:
        """
        运行回测

        Args:
            code: 股票代码
            days: 回测天数（自然日）

        Returns:
            BacktestResult
        """
        # ---- 加载股票K线 ----
        hist_raw = self.selector.get_history(code, days=days + 90)
        if not hist_raw or len(hist_raw) < 40:
            return self._empty_result(code)

        hist = clean_kline_data(hist_raw)
        if not hist or len(hist) < 40:
            return self._empty_result(code)

        name = hist_raw[-1].get("name", code)

        # ---- 加载上证指数K线（用于每日市场状态判断）----
        index_raw = self.selector.get_history("sh000001", days=days + 90)
        if index_raw and len(index_raw) >= 20:
            index_hist = clean_kline_data(index_raw)
            index_date_to_idx = {d["date"]: i for i, d in enumerate(index_hist)}
        else:
            index_hist = []
            index_date_to_idx = {}

        # ---- 构建回测区间 ----
        start_idx = 20
        bt_hist = hist[start_idx:]

        if start_date:
            bt_hist = [d for d in bt_hist if d["date"] >= start_date]
        if end_date:
            bt_hist = [d for d in bt_hist if d["date"] <= end_date]
        if not bt_hist:
            return self._empty_result(code)

        logger.info(
            f"[Backtest v4.2] {code}({name}) 回测: "
            f"{bt_hist[0]['date']}~{bt_hist[-1]['date']}，共{len(bt_hist)}天"
        )

        # ---- 建立 date -> hist index 映射 ----
        hist_index = {d["date"]: idx for idx, d in enumerate(hist)}

        # ---- v4.2 新增：连续亏损 & 锁仓状态 ----
        from collections import defaultdict
        from datetime import timedelta
        loss_streak: dict = defaultdict(int)
        lock_until: dict = {}

        # ---- 初始化账户 ----
        cash = self.initial_capital
        positions: List[BTPosition] = []
        trades: List[BTTrade] = []
        round_trips: List[dict] = []
        equity_curve: List[dict] = []
        peak = self.initial_capital
        max_dd = 0.0
        trade_seq = 0

        # ---- 按日迭代 ----
        for i, day in enumerate(bt_hist):
            date = day["date"]
            close = day["close"]
            volume = day["volume"]

            # ---- 检测当日市场状态 ----
            market_status = self._detect_regime_for_date(date, index_hist, index_date_to_idx)

            # ---- 构建窗口（用于信号计算）----
            hist_i = hist_index.get(date, i)
            window = hist[max(0, hist_i - 59):hist_i + 1]
            if len(window) < 20:
                equity_curve.append({
                    "date": date,
                    "capital": cash + sum(p.market_value(close) for p in positions),
                    "regime": market_status.value,
                })
                continue

            fake_rt = {
                "code": code, "name": name,
                "price": close, "change_pct": 0,
                "volume": volume, "turnover_rate": 0,
            }

            # ---- 计算信号（传入真实市场状态）----
            try:
                signal = self.counter.count_signals(window, fake_rt, market_status=market_status, buy_price=0, skip_money_flow=True)
            except Exception as e:
                logger.debug(f"[Backtest] {date} 信号异常: {e}")
                equity_curve.append({
                    "date": date,
                    "capital": cash + sum(p.market_value(close) for p in positions),
                    "regime": market_status.value,
                })
                continue

            # ---- ATR ----
            atr_win = window[-15:]
            atr = calc_atr(
                [h["high"] for h in atr_win],
                [h["low"] for h in atr_win],
                [h["close"] for h in atr_win],
            ) if len(atr_win) >= 15 else 0.0

            # ---- 更新持仓（追踪止损 + ATR更新）----
            for pos in positions:
                if atr > 0:
                    # 追踪止损用开仓时的ATR倍数
                    new_stop = close - pos.atr_multiplier * atr
                    pos.trailing_stop = max(pos.trailing_stop, new_stop)
                pos.atr = atr

            # ---- 卖出检查（使用 signal.decision 驱动）----
            sell_queue = []
            for pos in positions:
                hold_days = (datetime.strptime(date, "%Y-%m-%d") -
                             datetime.strptime(pos.buy_date, "%Y-%m-%d")).days
                unrealized_pct = pos.unrealized_pct(close)

                # 1. ATR追踪止损
                if close <= pos.trailing_stop:
                    sell_queue.append((pos, "止损", f"ATR追踪@{pos.trailing_stop:.2f}", signal))
                    continue

                # 2. 持仓超期（震荡10天/强市30天；弱市已取消此限制，改用MA20止盈）
                if pos.market_regime != MarketStatus.WEAK and hold_days >= pos.max_hold_days:
                    sell_queue.append((pos, "超期平仓", f"持仓{hold_days}天超限({pos.max_hold_days}天)", signal))
                    continue

                # 3. 固定亏损止损（弱市-3%，震荡-10%，强市-15%）
                if unrealized_pct <= -pos.stop_loss_pct:
                    sell_queue.append((pos, "止损", f"亏损{unrealized_pct:.1f}%触及止损({pos.stop_loss_pct}%)", signal))
                    continue

                # 4. 止盈（弱市=MA20 / 震荡&强市=固定%）
                take_profit_triggered = False
                if pos.market_regime == MarketStatus.WEAK:
                    # 弱市：价格反弹至MA20即止盈
                    if pos.ma20_take_profit > 0 and close >= pos.ma20_take_profit:
                        sell_queue.append((pos, "止盈", f"弱市MA20止盈({close:.2f}≥MA20{pos.ma20_take_profit:.2f})", signal))
                        continue
                else:
                    if unrealized_pct >= pos.take_profit_pct:
                        sell_queue.append((pos, "止盈", f"盈利{unrealized_pct:.1f}%触及止盈({pos.take_profit_pct}%)", signal))
                        continue

                # 5. RSI反弹到位（仅弱市持仓）
                if pos.market_regime == MarketStatus.WEAK and signal.rsi_6 > self.cfg.weak_rsi_sell_threshold:
                    sell_queue.append((pos, "卖出", f"弱市RSI反弹到位({signal.rsi_6:.1f}>65)", signal))
                    continue

                # 6. signal.decision == SELL（震荡/强市用经典信号数）
                if signal.decision == Decision.SELL:
                    sell_queue.append((pos, "卖出", f"决策卖出({signal.sell_count}个卖出信号)", signal))
                    continue

            # 执行卖出
            for pos, reason, detail, sig in sell_queue:
                trade_seq += 1
                sell_price = calc_real_sell_price(close)
                gross_amount = sell_price * pos.quantity * 100
                cost = calculate_sell_cost(gross_amount)
                pnl = cost["net_proceeds"] - pos.cost_basis()
                hd = (datetime.strptime(date, "%Y-%m-%d") -
                      datetime.strptime(pos.buy_date, "%Y-%m-%d")).days

                trades.append(BTTrade(
                    seq=trade_seq, date=date, action=reason,
                    code=pos.code, name=pos.name,
                    price=sell_price, quantity=pos.quantity,
                    amount=cost["net_proceeds"],
                    commission=cost["total_cost"],
                    pnl=pnl, hold_days=hd,
                    reason=detail,
                    signal_count=sig.sell_count,
                    rebound_signals=sig.rebound_count,
                    trend_signals=sig.trend_count,
                    market_regime=pos.market_regime,
                ))

                round_trips.append({
                    "seq": trade_seq,
                    "buy_date": pos.buy_date,
                    "buy_price": pos.buy_price,
                    "buy_quantity": pos.quantity,
                    "sell_date": date,
                    "sell_price": sell_price,
                    "sell_quantity": pos.quantity,
                    "pnl": pnl,
                    "hold_days": hd,
                    "reason": detail,
                    "market_regime": pos.market_regime.value if hasattr(pos.market_regime, "value") else pos.market_regime,
                    "buy_rebound": pos.rebound_signals,
                    "buy_trend": pos.trend_signals,
                })

                cash += cost["net_proceeds"]
                positions.remove(pos)

                # ---- v4.2 ③：更新连续亏损计数 ----
                if pnl < 0:
                    loss_streak[pos.code] = loss_streak.get(pos.code, 0) + 1
                    if loss_streak[pos.code] >= self.cfg.consecutive_stop_loss_lock:
                        lock_until[pos.code] = (
                            datetime.strptime(date, "%Y-%m-%d") +
                            timedelta(days=self.cfg.consecutive_stop_loss_lock_days)
                        ).strftime("%Y-%m-%d")
                        logger.info(f"[v4.2 锁仓] {pos.code} 连续{loss_streak[pos.code]}笔亏损，锁至{lock_until[pos.code]}")
                else:
                    loss_streak[pos.code] = 0

            # ---- 买入检查（v4.2：锁仓检查 + 弱势连续亏损强化）----
            if (len(positions) < self.max_positions
                    and signal.decision == Decision.BUY
                    and not any(p.code == code for p in positions)):

                # v4.2 ③：锁仓期检查
                if lock_until.get(code) and date <= lock_until[code]:
                    equity_curve.append({
                        "date": date,
                        "capital": cash + sum(p.market_value(close) for p in positions),
                        "regime": market_status.value,
                    })
                    continue

                # v4.2 ④：弱市连续亏损后要求更多信号
                if market_status == MarketStatus.WEAK:
                    if loss_streak.get(code, 0) >= self.cfg.weak_consecutive_loss_count:
                        if signal.rebound_count < 2 + self.cfg.weak_consecutive_loss_extra_signals:
                            equity_curve.append({
                                "date": date,
                                "capital": cash + sum(p.market_value(close) for p in positions),
                                "regime": market_status.value,
                            })
                            continue

                regime_params = self._get_regime_params(market_status)

                buy_price = calc_real_buy_price(close)
                # 仓位：取 signal.position_ratio 和 regime 上限中的较小值
                position_ratio = min(signal.position_ratio, regime_params["position_pct"])
                position_ratio = max(position_ratio, 0.05)  # 最低5%

                max_amount = cash * position_ratio
                quantity = max(1, int(max_amount / (buy_price * 100)))

                # 成交量检查
                valid_qty, quantity, _ = check_volume_limit(quantity, volume)
                if quantity < 1:
                    equity_curve.append({
                        "date": date,
                        "capital": cash + sum(p.market_value(close) for p in positions),
                        "regime": market_status.value,
                    })
                    continue

                gross_amount = buy_price * quantity * 100
                cost = calculate_buy_cost(gross_amount)

                if cost["total_cost"] > cash:
                    equity_curve.append({
                        "date": date,
                        "capital": cash + sum(p.market_value(close) for p in positions),
                        "regime": market_status.value,
                    })
                    continue

                trade_seq += 1
                trades.append(BTTrade(
                    seq=trade_seq, date=date, action="BUY",
                    code=code, name=name,
                    price=buy_price, quantity=quantity,
                    amount=cost["total_cost"],
                    commission=cost["commission"] + cost["slippage_cost"],
                    reason=f"决策买入[{market_status.value}] 反弹{int(signal.rebound_count)}/趋势{int(signal.trend_count)}/经典{int(signal.buy_count)}",
                    signal_count=signal.buy_count,
                    rebound_signals=signal.rebound_count,
                    trend_signals=signal.trend_count,
                    market_regime=market_status,
                ))

                cash -= cost["total_cost"]

                # 止损止盈用 regime 对应参数（含地板保护）
                if atr > 0:
                    floor_map = {MarketStatus.WEAK: 0.03, MarketStatus.STRONG: 0.15, MarketStatus.CONSOLIDATE: 0.10}
                    floor_pct = floor_map.get(market_status, 0.10)
                    stop_loss = calc_atr_stop_loss(buy_price, atr, regime_params["atr_multiplier"], floor_pct)
                else:
                    stop_loss = buy_price * (1 - regime_params["stop_loss_pct"] / 100)

                # 弱市止盈目标 = MA20（其他市场用固定%）
                ma20_tp = signal.ma20 if market_status == MarketStatus.WEAK else 0.0
                if market_status != MarketStatus.WEAK:
                    take_profit = buy_price * (1 + regime_params["take_profit_pct"] / 100)
                else:
                    take_profit = ma20_tp  # 弱市用MA20作为止盈目标价

                positions.append(BTPosition(
                    code=code, name=name, buy_date=date,
                    buy_price=buy_price,
                    buy_commission=cost["commission"] + cost["slippage_cost"],
                    quantity=quantity,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    trailing_stop=stop_loss,
                    atr=atr,
                    buy_signals=signal.buy_count,
                    market_regime=market_status,
                    atr_multiplier=regime_params["atr_multiplier"],
                    stop_loss_pct=regime_params["stop_loss_pct"],
                    take_profit_pct=regime_params["take_profit_pct"],
                    max_hold_days=regime_params["max_hold_days"],
                    rebound_signals=signal.rebound_count,
                    trend_signals=signal.trend_count,
                    ma20_take_profit=ma20_tp,
                ))

            # ---- 记录每日净值 ----
            total_capital = cash + sum(p.market_value(close) for p in positions)
            if total_capital > peak:
                peak = total_capital
            dd = (peak - total_capital) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

            equity_curve.append({
                "date": date,
                "capital": round(total_capital, 2),
                "regime": market_status.value,
            })

        # ---- 回测结束，平掉所有剩余持仓 ----
        last_date = bt_hist[-1]["date"]
        last_close = bt_hist[-1]["close"]
        for pos in list(positions):
            trade_seq += 1
            sell_price = calc_real_sell_price(last_close)
            gross_amount = sell_price * pos.quantity * 100
            cost = calculate_sell_cost(gross_amount)
            pnl = cost["net_proceeds"] - pos.cost_basis()
            hd = (datetime.strptime(last_date, "%Y-%m-%d") -
                  datetime.strptime(pos.buy_date, "%Y-%m-%d")).days

            trades.append(BTTrade(
                seq=trade_seq, date=last_date, action="最后平仓",
                code=pos.code, name=pos.name,
                price=sell_price, quantity=pos.quantity,
                amount=cost["net_proceeds"],
                commission=cost["total_cost"],
                pnl=pnl, hold_days=hd,
                reason="回测结束平仓",
                signal_count=0,
                rebound_signals=pos.rebound_signals,
                trend_signals=pos.trend_signals,
                market_regime=pos.market_regime,
            ))
            round_trips.append({
                "seq": trade_seq,
                "buy_date": pos.buy_date,
                "buy_price": pos.buy_price,
                "buy_quantity": pos.quantity,
                "sell_date": last_date,
                "sell_price": sell_price,
                "sell_quantity": pos.quantity,
                "pnl": pnl,
                "hold_days": hd,
                "reason": "回测结束平仓",
                "market_regime": pos.market_regime.value if hasattr(pos.market_regime, "value") else pos.market_regime,
            })
            cash += cost["net_proceeds"]
            positions.remove(pos)

        final_capital = cash
        return self._calc_metrics(
            code, name,
            bt_hist[0]["date"], bt_hist[-1]["date"], len(bt_hist),
            trades, equity_curve, round_trips,
            self.initial_capital, final_capital, max_dd,
        )

    def _calc_metrics(self, code, name, start_date, end_date, days,
                      trades, equity_curve, round_trips,
                      initial_capital, final_capital, max_dd):

        closed = [t for t in trades if t.action not in ("BUY",)]
        winning = [t for t in closed if t.pnl > 0]
        losing = [t for t in closed if t.pnl <= 0]

        win_rate = len(winning) / len(closed) * 100 if closed else 0
        avg_win = sum(t.pnl for t in winning) / len(winning) if winning else 0
        avg_loss = abs(sum(t.pnl for t in losing) / len(losing)) if losing else 1
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        total_return = (final_capital - initial_capital) / initial_capital * 100
        annual_return = total_return / days * 250 if days > 0 else 0

        sharpe = 0.0
        if len(equity_curve) > 5:
            capitals = [e["capital"] for e in equity_curve]
            returns = [capitals[i] / capitals[i - 1] - 1 for i in range(1, len(capitals))]
            if returns and np.std(returns) > 0:
                sharpe = (np.mean(returns) * 250) / (np.std(returns) * (250 ** 0.5))

        hold_days = [t.hold_days for t in closed]
        avg_hold = sum(hold_days) / len(hold_days) if hold_days else 0

        # v4.0: 统计各市场状态开仓次数
        regime_stats = {}
        for t in trades:
            if t.action == "BUY" and t.market_regime:
                regime_stats[t.market_regime.value if hasattr(t.market_regime, 'value') else t.market_regime] = regime_stats.get(t.market_regime, 0) + 1

        result = BacktestResult(
            code=code, name=name,
            start_date=start_date, end_date=end_date, days=days,
            total_return=total_return, annual_return=annual_return,
            win_rate=win_rate, profit_loss_ratio=profit_loss_ratio,
            max_drawdown=max_dd, sharpe_ratio=sharpe,
            total_trades=len(closed),
            winning_trades=len(winning),
            losing_trades=len(losing),
            avg_hold_days=avg_hold,
            trades=closed, round_trips=round_trips, equity_curve=equity_curve,
            final_capital=final_capital, initial_capital=initial_capital,
        )

        regime_info = ", ".join(f"{k}={v}次" for k, v in sorted(regime_stats.items()))
        logger.info(
            f"[Backtest v4.0] {code} 完成: 收益率={result.total_return:.2f}%, "
            f"夏普={result.sharpe_ratio:.2f}, 胜率={result.win_rate:.1f}%, "
            f"交易={result.total_trades}次, 最大回撤={result.max_drawdown:.2f}% "
            f"[{regime_info}]"
        )
        return result

    def _empty_result(self, code) -> BacktestResult:
        return BacktestResult(code=code, name=code)
