# -*- coding: utf-8 -*-
"""
扫描器
批量扫描股票池，计算信号
"""

import logging
from typing import List, Optional
from datetime import datetime
from typing import List
from models.watchlist import WatchlistStore, Watchlist
from models.signal import RealTimeSignal, MarketStatus
from data_provider.txstock import TxStock
from indicators.signal_counter import SignalCounter
from strategy.market_filter import get_market_filter

logger = logging.getLogger(__name__)


class Scanner:
    """
    股票扫描器

    负责:
    1. 加载股票池
    2. 批量获取数据
    3. 计算信号
    4. 返回结果列表
    """

    def __init__(self):
        self.txstock = TxStock()
        self.counter = SignalCounter()
        self.market_filter = get_market_filter()

    def scan_watchlist(
        self,
        watchlist: Watchlist = None,
        market_regime=None,  # MarketRegimeResult from watcher
    ) -> List[RealTimeSignal]:
        """
        扫描整个股票池

        Args:
            watchlist: 股票池（不传则自动加载）
            market_regime: MarketRegimeResult 对象（来自watcher），优先使用

        Returns:
            RealTimeSignal 列表
        """
        if watchlist is None:
            store = WatchlistStore()
            watchlist = store.load()

        enabled_stocks = watchlist.get_enabled_stocks()
        if not enabled_stocks:
            logger.warning("股票池为空或无启用股票")
            return []

        logger.info(f"开始扫描 {len(enabled_stocks)} 只股票...")

        # 确定市场状态：优先用watcher检测的结果，否则用market_filter
        if market_regime is not None:
            # 转换 MarketRegime -> MarketStatus
            from indicators.market_regime import MarketRegime
            regime_map = {
                MarketRegime.STRONG: MarketStatus.STRONG,
                MarketRegime.WEAK: MarketStatus.WEAK,
                MarketRegime.CONSOLIDATE: MarketStatus.CONSOLIDATE,
            }
            market_status = regime_map.get(market_regime.regime, MarketStatus.CONSOLIDATE)
            market_change = market_regime.index_change_pct_5d
            logger.info(f"📊 市场状态（外部）: {market_regime.regime.value} — {market_regime.reason}")
        else:
            market_status, market_change = self.market_filter.get_market_status()
            logger.info(f"📊 市场状态（内部）: {market_status.value} ({market_change:+.2f}%)")

        signals = []
        for entry in enabled_stocks:
            try:
                signal = self._scan_single(
                    entry.code,
                    entry.name,
                    market_status,
                    market_change,
                )
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.error(f"扫描 {entry.code} 失败: {e}")

        logger.info(f"扫描完成: {len(signals)}/{len(enabled_stocks)} 只成功")
        return signals

    def scan_single(self, code: str, name: str = "") -> RealTimeSignal:
        """扫描单只股票"""
        market_status, market_change = self.market_filter.get_market_status()
        return self._scan_single(code, name or code, market_status, market_change)

    def _scan_single(
        self,
        code: str,
        name: str,
        market_status: MarketStatus,
        market_change: float,
    ) -> RealTimeSignal:
        """扫描单只股票（内部方法）"""
        # 获取数据
        history = self.txstock.get_history(code, days=60)
        realtime = self.txstock.get_realtime(code)

        if not history or not realtime:
            raise ValueError(f"获取数据失败: {code}")

        # 更新名称
        if not name or name == code:
            name = realtime.get("name", code)

        realtime["market_change_pct"] = market_change

        # 计算信号
        signal = self.counter.count_signals(history, realtime, market_status)
        signal.name = name

        return signal

    def get_actionable_signals(self, signals: List[RealTimeSignal]) -> dict:
        """
        从信号列表中提取可操作的信号

        Returns:
            dict: {
                "buy": [信号列表],
                "sell": [信号列表],
                "hold": [信号列表],
                "watch": [信号列表],
            }
        """
        result = {
            "buy": [],
            "sell": [],
            "hold": [],
            "watch": [],
            "stop_loss": [],
        }

        for s in signals:
            decision = s.decision.value
            if decision == "BUY":
                result["buy"].append(s)
            elif decision == "SELL":
                result["sell"].append(s)
            elif decision == "HOLD":
                result["hold"].append(s)
            elif decision == "STOP_LOSS":
                result["stop_loss"].append(s)
            else:
                result["watch"].append(s)

        return result
