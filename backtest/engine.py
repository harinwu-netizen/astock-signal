# -*- coding: utf-8 -*-
"""
回测引擎
支持：单股 / 多日 / 信号驱动 模拟交易
"""

import logging
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from config import get_config
from models.signal import Decision
from indicators.signal_counter import SignalCounter
from indicators.atr import calc_atr, calc_atr_stop_loss
from data_provider.data_selector import get_selector
from data_provider.data_clean import clean_kline_data
from trading.cost_calculator import (
    calculate_buy_cost, calculate_sell_cost,
    calc_real_buy_price, calc_real_sell_price,
    check_volume_limit,
)

logger = logging.getLogger(__name__)

BUY_THRESHOLD = 5
SELL_THRESHOLD = 3
STOP_LOSS_PCT = 10.0
ATR_MULTIPLIER = 2.0
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
        }


@dataclass
class BTPosition:
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

    def market_value(self, current_price: float) -> float:
        """当前市值"""
        return current_price * self.quantity * 100

    def cost_basis(self) -> float:
        """成本基准（含手续费）"""
        return self.buy_price * self.quantity * 100 + self.buy_commission

    def unrealized_pnl(self, current_price: float) -> float:
        """未实现盈亏"""
        return self.market_value(current_price) - self.cost_basis()


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
    trades: List[BTTrade] = field(default_factory=list)
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
    def __init__(
        self,
        initial_capital: float = 100000.0,
        max_positions: int = MAX_POSITIONS,
        buy_threshold: int = BUY_THRESHOLD,
        sell_threshold: int = SELL_THRESHOLD,
        atr_multiplier: float = ATR_MULTIPLIER,
        stop_loss_pct: float = STOP_LOSS_PCT,
    ):
        self.initial_capital = initial_capital
        self.max_positions = max_positions
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.atr_multiplier = atr_multiplier
        self.stop_loss_pct = stop_loss_pct
        self.counter = SignalCounter()
        self.selector = get_selector()

    def run(self, code: str, days: int = 60,
            start_date: str = None, end_date: str = None) -> BacktestResult:
        """
        运行回测

        Args:
            code: 股票代码
            days: 回测天数（自然日，用于获取历史数据）

        Returns:
            BacktestResult
        """
        # 获取并清洗K线
        hist_raw = self.selector.get_history(code, days=days + 60)
        if not hist_raw or len(hist_raw) < 40:
            return self._empty_result(code)

        hist = clean_kline_data(hist_raw)
        if not hist or len(hist) < 40:
            return self._empty_result(code)

        name = hist_raw[-1].get("name", code)

        # 回测区间：跳过前20天（用于计算指标），其余全部回测
        start_idx = 20
        bt_hist = hist[start_idx:]
        logger.info(f"[Backtest] {code}({name}) 回测: {bt_hist[0]['date']}~{bt_hist[-1]['date']}，共{len(bt_hist)}天")

        # ---- 初始化账户 ----
        cash = self.initial_capital
        positions: List[BTPosition] = []
        trades: List[BTTrade] = []
        equity_curve: List[dict] = []
        peak = self.initial_capital
        max_dd = 0.0
        trade_seq = 0

        # ---- 按日迭代 ----
        for i, day in enumerate(bt_hist):
            date = day["date"]
            close = day["close"]
            high = day["high"]
            low = day["low"]
            volume = day["volume"]

            # ---- 信号计算 ----
            # 窗口：包含今天，共20-60天历史（用于均线等指标）
            window = bt_hist[max(0, i - 59):i + 1]
            if len(window) < 20:
                equity_curve.append({"date": date, "capital": cash + sum(p.market_value(close) for p in positions)})
                continue

            fake_rt = {
                "code": code, "name": name,
                "price": close, "change_pct": 0,
                "volume": volume, "turnover_rate": 0,
            }

            try:
                signal = self.counter.count_signals(window, fake_rt, market_status=3, buy_price=0)
            except Exception as e:
                logger.debug(f"[Backtest] {date} 信号异常: {e}")
                equity_curve.append({"date": date, "capital": cash + sum(p.market_value(close) for p in positions)})
                continue

            # ---- ATR（用于止损）----
            atr_win = window[-15:]
            atr = calc_atr(
                [h["high"] for h in atr_win],
                [h["low"] for h in atr_win],
                [h["close"] for h in atr_win],
            ) if len(atr_win) >= 15 else 0.0

            # ---- 更新持仓追踪止损 ----
            for pos in positions:
                if atr > 0:
                    new_stop = close - self.atr_multiplier * atr
                    pos.trailing_stop = max(pos.trailing_stop, new_stop)
                pos.atr = atr
                pos.latest_signal_count = signal.buy_count

            # ---- 卖出检查 ----
            sell_queue = []
            for pos in positions:
                hold_days = (datetime.strptime(date, "%Y-%m-%d") -
                             datetime.strptime(pos.buy_date, "%Y-%m-%d")).days

                # ATR追踪止损
                if close <= pos.trailing_stop:
                    sell_queue.append((pos, "止损", f"ATR追踪止损@{pos.trailing_stop:.2f}"))
                    continue

                # 止盈（持有>=5天且盈利>=15%）
                if hold_days >= 5 and pos.unrealized_pnl(close) / pos.cost_basis() * 100 >= 15:
                    sell_queue.append((pos, "止盈", f"盈利{pos.unrealized_pnl(close)/pos.cost_basis()*100:.1f}%"))
                    continue

                # 卖出信号
                if signal.sell_count >= self.sell_threshold:
                    sell_queue.append((pos, "卖出", f"卖出信号≥{self.sell_threshold}(实际{signal.sell_count})"))

            # 执行卖出
            for pos, reason, detail in sell_queue:
                trade_seq += 1
                sell_price = calc_real_sell_price(close)
                gross_amount = sell_price * pos.quantity * 100
                cost = calculate_sell_cost(gross_amount)
                pnl = cost["net_proceeds"] - pos.cost_basis()
                hold_days = (datetime.strptime(date, "%Y-%m-%d") -
                             datetime.strptime(pos.buy_date, "%Y-%m-%d")).days

                trades.append(BTTrade(
                    seq=trade_seq, date=date, action=reason,
                    code=pos.code, name=pos.name,
                    price=sell_price, quantity=pos.quantity,
                    amount=cost["net_proceeds"],
                    commission=cost["total_cost"],
                    pnl=pnl, hold_days=hold_days,
                    reason=detail,
                    signal_count=signal.sell_count,
                ))

                cash += cost["net_proceeds"]
                positions.remove(pos)

            # ---- 买入检查 ----
            if (len(positions) < self.max_positions
                    and signal.buy_count >= self.buy_threshold
                    and not any(p.code == code for p in positions)):

                buy_price = calc_real_buy_price(close)
                # 使用30%仓位
                max_amount = cash * 0.3
                quantity = max(1, int(max_amount / (buy_price * 100)))

                # 成交量检查
                valid_qty, quantity, _ = check_volume_limit(quantity, volume)
                if quantity < 1:
                    equity_curve.append({"date": date, "capital": cash + sum(p.market_value(close) for p in positions)})
                    continue

                gross_amount = buy_price * quantity * 100
                cost = calculate_buy_cost(gross_amount)

                if cost["total_cost"] > cash:
                    equity_curve.append({"date": date, "capital": cash + sum(p.market_value(close) for p in positions)})
                    continue

                trade_seq += 1
                trades.append(BTTrade(
                    seq=trade_seq, date=date, action="BUY",
                    code=code, name=name,
                    price=buy_price, quantity=quantity,
                    amount=cost["total_cost"],
                    commission=cost["commission"] + cost["slippage_cost"],
                    reason=f"买入信号≥{self.buy_threshold}(实际{signal.buy_count})",
                    signal_count=signal.buy_count,
                ))

                cash -= cost["total_cost"]

                stop_loss = calc_atr_stop_loss(buy_price, atr, self.atr_multiplier) if atr > 0 else buy_price * (1 - self.stop_loss_pct / 100)
                take_profit = buy_price * 1.15

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
                ))

            # ---- 记录每日净值 ----
            total_capital = cash + sum(p.market_value(close) for p in positions)
            if total_capital > peak:
                peak = total_capital
            dd = (peak - total_capital) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

            equity_curve.append({"date": date, "capital": round(total_capital, 2)})

        # ---- 平掉所有剩余持仓 ----
        last_date = bt_hist[-1]["date"]
        last_close = bt_hist[-1]["close"]
        for pos in list(positions):
            trade_seq += 1
            sell_price = calc_real_sell_price(last_close)
            gross_amount = sell_price * pos.quantity * 100
            cost = calculate_sell_cost(gross_amount)
            pnl = cost["net_proceeds"] - pos.cost_basis()
            hold_days = (datetime.strptime(last_date, "%Y-%m-%d") -
                         datetime.strptime(pos.buy_date, "%Y-%m-%d")).days

            trades.append(BTTrade(
                seq=trade_seq, date=last_date, action="最后平仓",
                code=pos.code, name=pos.name,
                price=sell_price, quantity=pos.quantity,
                amount=cost["net_proceeds"],
                commission=cost["total_cost"],
                pnl=pnl, hold_days=hold_days,
                reason="回测结束，最后平仓",
                signal_count=0,
            ))
            cash += cost["net_proceeds"]
            positions.remove(pos)

        final_capital = cash
        return self._calc_metrics(
            code, name,
            bt_hist[0]["date"], bt_hist[-1]["date"], len(bt_hist),
            trades, equity_curve,
            self.initial_capital, final_capital, max_dd,
        )

    def _calc_metrics(self, code, name, start_date, end_date, days,
                      trades, equity_curve, initial_capital, final_capital, max_dd):

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
            trades=closed, equity_curve=equity_curve,
            final_capital=final_capital, initial_capital=initial_capital,
        )

        logger.info(
            f"[Backtest] {code} 完成: 收益率={result.total_return:.2f}%, "
            f"夏普={result.sharpe_ratio:.2f}, 胜率={result.win_rate:.1f}%, "
            f"交易={result.total_trades}次, 最大回撤={result.max_drawdown:.2f}%"
        )
        return result

    def _empty_result(self, code) -> BacktestResult:
        return BacktestResult(code=code, name=code)
