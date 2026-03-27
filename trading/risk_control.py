# -*- coding: utf-8 -*-
"""
风控模块
运行时风控检查和熔断机制
"""

import logging
from datetime import datetime, time
from typing import List, Tuple
from config import get_config
from models.position import Position, PositionStore
from models.signal import MarketStatus

logger = logging.getLogger(__name__)


class RiskController:
    """
    运行时风控控制器

    风控规则（P0-P3）:
    P0 强制止损（不可绕过）
      · 大盘单日跌幅 > 2% → 全仓强平
      · 单股持仓亏损 > 10% → 强制止损

    P1 禁止开仓
      · 弱势市场（大盘MA5<MA10）
      · 非开仓时间段（14:30前）
      · 同一股票同日已交易

    P2 仓位限制
      · 单只仓位 ≤ 总资金30%
      · 最大持仓数 ≤ 3只
      · 总仓位 ≤ 80%

    P3 信号要求
      · 买入：信号 ≥ 5
      · 卖出：信号 ≥ 3
    """

    def __init__(self):
        self.config = get_config()

    def check_risk_level(self) -> str:
        """
        检查当前整体风险等级

        Returns:
            SAFE / CAUTION / WARNING / DANGER
        """
        config = self.config
        market_filter = None

        # 导入在这里避免循环依赖
        from strategy.market_filter import get_market_filter
        market_filter = get_market_filter()

        market_status, market_change = market_filter.get_market_status()

        # DANGER: 大盘暴跌
        if market_change < config.market_crash_threshold:
            return "DANGER"

        # WARNING: 弱势市场
        if market_status == MarketStatus.WEAK:
            return "WARNING"

        # 检查持仓
        position_store = PositionStore()
        positions = position_store.get_open_positions()

        # 检查单股止损
        for p in positions:
            if p.pnl_pct < -10:
                return "WARNING"

        # CAUTION: 仓位过重
        total_cost = sum(p.cost for p in positions)
        position_ratio = total_cost / config.total_capital
        if position_ratio > config.max_total_position_pct / 100:
            return "CAUTION"

        return "SAFE"

    def should_force_close_all(self, market_change: float) -> bool:
        """是否需要强平所有持仓"""
        if market_change < self.config.market_crash_threshold:
            logger.warning(f"🚨 大盘暴跌{market_change:.2f}%，触发强平熔断！")
            return True
        return False

    def should_force_stop(self, position: Position, current_price: float) -> bool:
        """单股是否需要强制止损"""
        # 亏损超过10%
        if position.pnl_pct < -10:
            logger.warning(f"🚨 {position.name}亏损{position.pnl_pct:.2f}%，触发强制止损")
            return True
        return False

    def get_position_limit(self, code: str) -> float:
        """获取某只股票的仓位上限"""
        return self.config.total_capital * (self.config.max_single_position_pct / 100)

    def can_open_position(self, code: str) -> Tuple[bool, str]:
        """
        检查是否可以开新仓

        Returns:
            (can_open, reason)
        """
        config = self.config

        # 1. 时间窗口检查
        now = datetime.now().time()
        open_start = datetime.strptime(config.open_window_start, "%H:%M").time()
        open_end = datetime.strptime(config.open_window_end, "%H:%M").time()
        if not (open_start <= now <= open_end):
            return False, f"不在开仓窗口({config.open_window_start}-{config.open_window_end})内"

        # 2. 持仓上限检查
        position_store = PositionStore()
        positions = position_store.get_open_positions()
        if len(positions) >= config.max_positions:
            return False, f"已达最大持仓数{config.max_positions}只"

        # 3. 资金检查
        locked = sum(p.cost for p in positions)
        if locked >= config.total_capital * (config.max_total_position_pct / 100):
            return False, f"总仓位已达{config.max_total_position_pct}%上限"

        return True, "可以开仓"

    def get_sell_ratio(self, signal_count: int, position_ratio: float) -> float:
        """
        根据信号数量计算建议卖出比例

        Args:
            signal_count: 卖出信号数量
            position_ratio: 当前持仓比例

        Returns:
            建议卖出比例 0.0-1.0
        """
        if signal_count >= 5:
            return 1.0  # 全卖
        elif signal_count == 4:
            return 0.5  # 卖一半
        elif signal_count == 3:
            return 0.3  # 轻仓卖
        return 0.0



