# -*- coding: utf-8 -*-
"""
多股回测引擎 v4.4
  ① 弱市ATR止损 2x→3x（减少被扫）
  ② 弱市取消持仓期限制（已有MA20/RSI双重退出）
  ③ 连续止损锁仓机制（连续2笔→锁5天）
  ④ 弱势连续亏损后提高买点阈值（+1个额外信号）
"""

import sys
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict

from config import get_config
from models.signal import MarketStatus, Decision
from indicators.signal_counter import SignalCounter
from indicators.atr import calc_atr, calc_atr_stop_loss
from data_provider.data_clean import clean_kline_data
from data_provider.data_selector import get_selector
from trading.cost_calculator import (
    calculate_buy_cost, calculate_sell_cost,
    calc_real_buy_price, calc_real_sell_price,
    check_volume_limit,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 多股持仓
# ============================================================================

@dataclass
class MultiPosition:
    code: str
    name: str
    buy_date: str
    buy_price: float
    quantity: int
    cost: float
    atr: float = 0.0
    atr_multiplier: float = 2.0
    stop_loss_pct: float = 10.0
    take_profit_pct: float = 15.0
    max_hold_days: int = 10
    market_regime: MarketStatus = MarketStatus.CONSOLIDATE
    rebound_signals: int = 0
    trend_signals: int = 0
    ma20_take_profit: float = 0.0
    bb_upper: float = 0.0   # 布林上轨（震荡市止盈用，v4.3新增）
    trailing_stop: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0

    def unrealized_pct(self, current_price: float) -> float:
        return (current_price - self.buy_price) / self.buy_price * 100

    def market_value(self, current_price: float) -> float:
        return current_price * self.quantity * 100

    @classmethod
    def create(cls, code, name, buy_date, buy_price, qty, cost, atr,
               atr_mult, sl_pct, tp_pct, max_hold,
               regime, rebound, trend, ma20_tp=0.0, bb_upper=0.0):
        # ATR止损（含地板保护）
        floor_map = {MarketStatus.WEAK: 0.03, MarketStatus.STRONG: 0.15, MarketStatus.CONSOLIDATE: 0.10}
        floor_pct = floor_map.get(regime, sl_pct / 100)
        ts = calc_atr_stop_loss(buy_price, atr, atr_mult, floor_pct) if atr > 0 else buy_price * (1 - floor_pct)
        sl = buy_price * (1 - sl_pct / 100)
        trailing_stop = max(ts, sl)
        stop_loss = trailing_stop
        take_profit = ma20_tp if regime == MarketStatus.WEAK else buy_price * (1 + tp_pct / 100)

        return cls(
            code=code, name=name, buy_date=buy_date,
            buy_price=buy_price, quantity=qty, cost=cost,
            atr=atr, atr_multiplier=atr_mult,
            stop_loss_pct=sl_pct, take_profit_pct=tp_pct,
            max_hold_days=max_hold,
            market_regime=regime,
            rebound_signals=rebound, trend_signals=trend,
            ma20_take_profit=ma20_tp,
            bb_upper=bb_upper,
            trailing_stop=trailing_stop,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )


# ============================================================================
# 多股回测引擎 v4.4
# ============================================================================

class MultiStockBacktestEngine:
    """
    多股回测引擎 v4.4
    - 每日扫描所有股票，信号强度排序，强者优先买
    - 资金共享，最多3只持仓
    - v4.4: 震荡市波段策略 + 布林上轨止盈 + 仓位计算修复
    """

    def __init__(self, codes: List[str], initial_capital: float = 100000.0):
        self.codes = codes
        self.initial_capital = initial_capital
        self.counter = SignalCounter()
        self.selector = get_selector()
        self.cfg = get_config()

    # ------------------------------------------------------------------ #
    #  市场状态 & 参数
    # ------------------------------------------------------------------ #

    def _detect_regime(self, date: str, index_hist: List[dict],
                       index_date_to_idx: dict) -> MarketStatus:
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
            chg5 = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0
            if chg5 > 0 and ma5 > ma10 > ma20:
                return MarketStatus.STRONG
            if chg5 < -1.5 or ma5 < ma10 < ma20:
                return MarketStatus.WEAK
            return MarketStatus.CONSOLIDATE
        except Exception:
            return MarketStatus.CONSOLIDATE

    def _get_regime_params(self, regime: MarketStatus) -> dict:
        cfg = self.cfg
        if regime == MarketStatus.WEAK:
            return {
                "atr_mult": cfg.weak_atr_multiplier,       # v4.2: 3.0（原2.0）
                "stop_loss_pct": cfg.weak_stop_loss_pct,
                "take_profit_pct": cfg.weak_take_profit_pct,
                "max_hold": cfg.weak_max_hold_days,
                "pos_pct": cfg.weak_single_position_pct / 100,
            }
        elif regime == MarketStatus.STRONG:
            return {
                "atr_mult": cfg.strong_atr_multiplier,
                "stop_loss_pct": cfg.strong_stop_loss_pct,
                "take_profit_pct": cfg.strong_take_profit_pct,
                "max_hold": cfg.strong_max_hold_days,
                "pos_pct": cfg.strong_single_position_pct / 100,
            }
        else:
            return {
                "atr_mult": cfg.consolidate_atr_multiplier,
                "stop_loss_pct": cfg.consolidate_stop_loss_pct,
                "take_profit_pct": cfg.consolidate_take_profit_pct,
                "max_hold": cfg.consolidate_max_hold_days,
                "pos_pct": cfg.consolidate_single_position_pct / 100,
            }

    def _calc_atr(self, window: List[dict]) -> float:
        if len(window) < 15:
            return 0.0
        w = window[-15:]
        return calc_atr(
            [h["high"] for h in w],
            [h["low"] for h in w],
            [h["close"] for h in w],
        )

    # ------------------------------------------------------------------ #
    #  主回测
    # ------------------------------------------------------------------ #

    def run(self, days: int = 90) -> dict:

        # ---- 加载所有股票历史 ----
        stock_hists: Dict[str, List[dict]] = {}
        stock_names: Dict[str, str] = {}
        stock_date_idx: Dict[str, dict] = {}

        for code in self.codes:
            raw = self.selector.get_history(code, days=days + 90)
            if not raw or len(raw) < 40:
                continue
            hist = clean_kline_data(raw)
            if len(hist) < 40:
                continue
            stock_hists[code] = hist
            stock_names[code] = raw[-1].get("name", code)
            stock_date_idx[code] = {d["date"]: i for i, d in enumerate(hist)}

        if not stock_hists:
            return self._empty_result()

        # ---- 上证指数 ----
        idx_raw = self.selector.get_history("sh000001", days=days + 90)
        idx_hist = clean_kline_data(idx_raw) if idx_raw and len(idx_raw) >= 20 else []
        idx_date_to_idx = {d["date"]: i for i, d in enumerate(idx_hist)}

        # ---- 回测日期基准 ----
        ref_code = list(stock_hists.keys())[0]
        ref_hist = stock_hists[ref_code]
        bt_hist = ref_hist[20:]
        bt_dates = [d["date"] for d in bt_hist]

        logger.info(
            f"[MultiBacktest v4.2] 回测: {bt_dates[0]} ~ {bt_dates[-1]}，"
            f"{len(bt_dates)}天，股票: {list(stock_hists.keys())}"
        )

        # ---- v4.2 新增：连续亏损 & 锁仓状态 ----
        loss_streak: Dict[str, int] = defaultdict(int)   # 每只股票连续亏损次数
        lock_until: Dict[str, str] = {}                    # 每只股票锁仓截止日

        # ---- 账户状态 ----
        cash = self.initial_capital
        positions: List[MultiPosition] = []
        equity_curve: List[dict] = []
        trades: List[dict] = []
        trade_seq = 0
        peak = self.initial_capital
        max_dd = 0.0

        # ---- 按日迭代 ----
        for date in bt_dates:
            avail = {
                c: stock_date_idx[c][date]
                for c in self.codes
                if date in stock_date_idx[c]
            }
            if not avail:
                equity_curve.append({"date": date, "capital": cash})
                continue

            closes = {c: stock_hists[c][avail[c]]["close"] for c in avail}
            volumes = {c: stock_hists[c][avail[c]]["volume"] for c in avail}
            market_status = self._detect_regime(date, idx_hist, idx_date_to_idx)

            # ---- 计算所有股票信号 ----
            signals = {}
            for code in avail:
                idx = avail[code]
                hist = stock_hists[code]
                window = hist[max(0, idx - 59):idx + 1]
                if len(window) < 20:
                    continue
                rt = {
                    "code": code, "name": stock_names[code],
                    "price": closes[code], "change_pct": 0,
                    "volume": volumes[code],
                }
                # v5.0: 持仓股传入 buy_price，信号系统才知道在持仓状态
                pos = next((p for p in positions if p.code == code), None)
                bp = pos.buy_price if pos else 0.0
                try:
                    signals[code] = self.counter.count_signals(
                        window, rt, market_status=market_status, buy_price=bp
                    )
                except Exception:
                    continue

            # ============================================================== #
            #  卖出检查
            # ============================================================== #
            for pos in list(positions):
                if pos.code not in closes:
                    continue

                close = closes[pos.code]
                sig = signals.get(pos.code)

                # ATR追踪止损重建
                atr = sig.atr if sig else pos.atr
                if atr > 0 and pos.atr_multiplier > 0:
                    new_ts = close - pos.atr_multiplier * atr
                    pos.trailing_stop = max(pos.trailing_stop, new_ts)
                pos.atr = atr
                sl = max(pos.trailing_stop, pos.buy_price * (1 - pos.stop_loss_pct / 100))

                hold_days = (
                    datetime.strptime(date, "%Y-%m-%d") -
                    datetime.strptime(pos.buy_date, "%Y-%m-%d")
                ).days
                upct = pos.unrealized_pct(close)

                # ============================================================== #
                #  v5.0 退出逻辑：信号系统驱动，引擎做安全网
                # ============================================================== #
                triggered = None

                # 0. P0 止损：信号系统发 STOP_LOSS（最高优先级）
                if sig and sig.decision == Decision.STOP_LOSS:
                    triggered = ("止损", f"信号STOP_LOSS")

                # 1. 信号系统发 SELL（主要退出）
                elif sig and sig.decision == Decision.SELL:
                    triggered = ("卖出", f"信号SELL({sig.sell_signals_detail})")

                # 2. 信号系统发 TAKE_PROFIT
                elif sig and sig.decision == Decision.TAKE_PROFIT:
                    triggered = ("止盈", f"信号TAKE_PROFIT({sig.sell_signals_detail})")

                # 3. 安全网1：亏损超过容忍线（最后防线）
                elif upct <= -pos.stop_loss_pct:
                    triggered = ("止损", f"安全网亏{upct:.1f}%")

                # 4. 安全网2：信号系统发 HOLD 但持仓超30天，强制检视
                elif hold_days >= 30:
                    # 只在超长期持仓时给个警告，不自动卖
                    pass

                if triggered:
                    trade_seq += 1
                    sp = calc_real_sell_price(close)
                    gross = sp * pos.quantity * 100
                    cost = calculate_sell_cost(gross)
                    pnl = cost["net_proceeds"] - pos.cost
                    hd = (
                        datetime.strptime(date, "%Y-%m-%d") -
                        datetime.strptime(pos.buy_date, "%Y-%m-%d")
                    ).days
                    trades.append({
                        "seq": trade_seq,
                        "date": date,
                        "action": triggered[0],
                        "code": pos.code,
                        "name": pos.name,
                        "buy_date": pos.buy_date,
                        "buy_price": pos.buy_price,
                        "sell_date": date,
                        "sell_price": sp,
                        "quantity": pos.quantity,
                        "pnl": pnl,
                        "hold_days": hd,
                        "reason": triggered[1],
                        "regime": pos.market_regime.value,
                        "rebound": pos.rebound_signals,
                        "trend": pos.trend_signals,
                    })
                    cash += cost["net_proceeds"]
                    positions.remove(pos)

                    # ---- v4.2 ③：更新连续亏损计数 ----
                    if pnl < 0:
                        loss_streak[pos.code] += 1
                        if loss_streak[pos.code] >= self.cfg.consecutive_stop_loss_lock:
                            lock_until[pos.code] = (
                                datetime.strptime(date, "%Y-%m-%d") +
                                timedelta(days=self.cfg.consecutive_stop_loss_lock_days)
                            ).strftime("%Y-%m-%d")
                            logger.info(
                                f"[v4.2 锁仓] {pos.code} 连续{loss_streak[pos.code]}笔亏损，"
                                f"锁至{lock_until[pos.code]}"
                            )
                    else:
                        loss_streak[pos.code] = 0

            # ============================================================== #
            #  买入检查
            # ============================================================== #
            if len(positions) < 3:
                candidates = []
                for code, sig in signals.items():
                    if any(p.code == code for p in positions):
                        continue
                    if sig.decision != Decision.BUY:
                        continue

                    # ---- v4.2 ③：锁仓期检查 ----
                    if code in lock_until and date <= lock_until[code]:
                        continue

                    # ---- v4.2 ④：弱势连续亏损后提高买点阈值 ----
                    # 弱市且近期有连续亏损 → 反弹信号需要更多
                    if market_status == MarketStatus.WEAK:
                        if loss_streak[code] >= self.cfg.weak_consecutive_loss_count:
                            required_extra = self.cfg.weak_consecutive_loss_extra_signals
                            # 反弹信号数需要满足: rebound_count >= 2 + extra
                            if sig.rebound_count < 2 + required_extra:
                                continue

                    candidates.append((code, sig))

                if candidates:
                    # 按 position_ratio 降序，每次只买最强的1只
                    candidates.sort(key=lambda x: x[1].position_ratio, reverse=True)
                    code, sig = candidates[0]

                    close = closes[code]
                    vol = volumes[code]
                    params = self._get_regime_params(market_status)
                    pos_ratio = min(sig.position_ratio, params["pos_pct"])
                    pos_ratio = max(pos_ratio, 0.05)

                    max_amt = cash * pos_ratio
                    qty = max(1, int(max_amt / (close * 100)))
                    _, qty, _ = check_volume_limit(qty, vol)
                    if qty < 1:
                        equity_curve.append({
                            "date": date,
                            "capital": cash + sum(
                                p.market_value(closes.get(p.code, p.buy_price))
                                for p in positions
                            ),
                        })
                        continue

                    buy_price = calc_real_buy_price(close)
                    gross = buy_price * qty * 100
                    cost = calculate_buy_cost(gross)
                    if cost["total_cost"] > cash:
                        equity_curve.append({
                            "date": date,
                            "capital": cash + sum(
                                p.market_value(closes.get(p.code, p.buy_price))
                                for p in positions
                            ),
                        })
                        continue

                    trade_seq += 1
                    reason_str = (
                        f"买入[{market_status.value}] "
                        f"反弹{int(sig.rebound_count)}/趋势{int(sig.trend_count)}/经典{int(sig.buy_count)}"
                    )
                    if (market_status == MarketStatus.WEAK
                            and loss_streak.get(code, 0) >= self.cfg.weak_consecutive_loss_count):
                        reason_str += f"【连续{loss_streak[code]}亏强化】"

                    trades.append({
                        "seq": trade_seq,
                        "date": date,
                        "action": "BUY",
                        "code": code,
                        "name": stock_names[code],
                        "buy_date": date,
                        "buy_price": buy_price,
                        "sell_date": "",
                        "sell_price": 0.0,
                        "quantity": qty,
                        "pnl": 0.0,
                        "hold_days": 0,
                        "reason": reason_str,
                        "regime": market_status.value,
                        "rebound": sig.rebound_count,
                        "trend": sig.trend_count,
                    })

                    cash -= cost["total_cost"]

                    # ATR
                    idx = avail[code]
                    atr_win = stock_hists[code][max(0, idx - 14):idx + 1]
                    atr = self._calc_atr(atr_win)
                    ma20_tp = sig.ma20 if market_status == MarketStatus.WEAK else 0.0
                    bb_upper = sig.bb_upper if market_status == MarketStatus.CONSOLIDATE else 0.0

                    pos = MultiPosition.create(
                        code=code, name=stock_names[code],
                        buy_date=date, buy_price=buy_price,
                        qty=qty, cost=cost["total_cost"],
                        atr=atr,
                        atr_mult=params["atr_mult"],
                        sl_pct=params["stop_loss_pct"],
                        tp_pct=params["take_profit_pct"],
                        max_hold=params["max_hold"],
                        regime=market_status,
                        rebound=sig.rebound_count,
                        trend=sig.trend_count,
                        ma20_tp=ma20_tp,
                        bb_upper=bb_upper,
                    )
                    positions.append(pos)

            # ---- 净值记录 ----
            total = cash + sum(
                p.market_value(closes.get(p.code, p.buy_price))
                for p in positions
            )
            if total > peak:
                peak = total
            dd = (peak - total) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
            equity_curve.append({"date": date, "capital": round(total, 2)})

        # ---- 平仓 ----
        last_date = bt_dates[-1] if bt_dates else ""
        last_prices = {}
        for code in self.codes:
            if (code in stock_hists
                    and last_date in stock_date_idx.get(code, {})):
                last_prices[code] = (
                    stock_hists[code][stock_date_idx[code][last_date]]["close"]
                )

        for pos in list(positions):
            last_close = last_prices.get(pos.code, pos.buy_price)
            trade_seq += 1
            sp = calc_real_sell_price(last_close)
            gross = sp * pos.quantity * 100
            cost = calculate_sell_cost(gross)
            pnl = cost["net_proceeds"] - pos.cost
            hd = (
                datetime.strptime(last_date, "%Y-%m-%d") -
                datetime.strptime(pos.buy_date, "%Y-%m-%d")
            ).days
            trades.append({
                "seq": trade_seq,
                "date": last_date,
                "action": "最后平仓",
                "code": pos.code,
                "name": pos.name,
                "buy_date": pos.buy_date,
                "buy_price": pos.buy_price,
                "sell_date": last_date,
                "sell_price": sp,
                "quantity": pos.quantity,
                "pnl": pnl,
                "hold_days": hd,
                "reason": "回测结束",
                "regime": pos.market_regime.value,
                "rebound": pos.rebound_signals,
                "trend": pos.trend_signals,
            })
            cash += cost["net_proceeds"]
            positions.remove(pos)

        return self._calc_metrics(trades, equity_curve, cash, max_dd)

    def _calc_metrics(self, trades, equity_curve, final_capital, max_dd):
        closed = [t for t in trades if t["action"] not in ("BUY",)]
        wins = [t for t in closed if t["pnl"] > 0]
        losses = [t for t in closed if t["pnl"] <= 0]
        total_ret = (final_capital - self.initial_capital) / self.initial_capital * 100
        win_rate = len(wins) / len(closed) * 100 if closed else 0
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = abs(sum(t["pnl"] for t in losses) / len(losses)) if losses else 1
        profit_factor = avg_win / avg_loss if avg_loss > 0 else 0

        stock_stats = {}
        for t in closed:
            code = t["code"]
            if code not in stock_stats:
                stock_stats[code] = {"count": 0, "pnl": 0}
            stock_stats[code]["count"] += 1
            stock_stats[code]["pnl"] += t["pnl"]

        return {
            "trades": closed,
            "equity_curve": equity_curve,
            "final_capital": final_capital,
            "total_return": total_ret,
            "max_drawdown": max_dd,
            "win_rate": win_rate,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "total_trades": len(closed),
            "profit_factor": profit_factor,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "stock_stats": stock_stats,
        }

    def _empty_result(self) -> dict:
        return {
            "trades": [],
            "equity_curve": [],
            "final_capital": self.initial_capital,
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_trades": 0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "stock_stats": {},
        }
