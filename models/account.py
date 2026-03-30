# -*- coding: utf-8 -*-
"""
账户模型（回测/模拟交易用）
"""
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime


@dataclass
class Account:
    """账户模型"""
    initial_capital: float = 100000.0   # 初始本金
    total_capital: float = 100000.0    # 总资产（含市值）
    available_capital: float = 100000.0 # 可用资金
    frozen_capital: float = 0.0        # 冻结资金（持仓占用）
    market_value: float = 0.0          # 市值
    total_return: float = 0.0          # 总收益率（%）
    total_return_abs: float = 0.0      # 总收益（绝对值）
    max_drawdown: float = 0.0          # 最大回撤（%）
    max_drawdown_abs: float = 0.0     # 最大回撤（绝对值）
    total_trades: int = 0              # 总交易次数
    winning_trades: int = 0            # 盈利次数
    losing_trades: int = 0             # 亏损次数

    def update(self, positions_value: float = 0.0):
        """更新账户状态"""
        self.market_value = positions_value
        self.total_capital = self.available_capital + positions_value
        self.total_return = (self.total_capital - self.initial_capital) / self.initial_capital * 100
        self.total_return_abs = self.total_capital - self.initial_capital

    def freeze(self, amount: float):
        """冻结资金（买入时）"""
        self.available_capital -= amount
        self.frozen_capital += amount

    def unfreeze(self, amount: float):
        """解冻资金（卖出后）"""
        self.available_capital += amount
        self.frozen_capital -= amount

    def record_trade(self, pnl: float):
        """记录一笔交易（用于统计）"""
        self.total_trades += 1
        if pnl > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1

    def calc_max_drawdown(self, peak_capital: float) -> float:
        """计算当前回撤"""
        if peak_capital <= 0:
            return 0.0
        dd = (peak_capital - self.total_capital) / peak_capital * 100
        if dd > self.max_drawdown:
            self.max_drawdown = dd
            self.max_drawdown_abs = peak_capital - self.total_capital
        return dd

    def to_dict(self) -> dict:
        return {
            "initial_capital": self.initial_capital,
            "total_capital": round(self.total_capital, 2),
            "available_capital": round(self.available_capital, 2),
            "market_value": round(self.market_value, 2),
            "total_return": round(self.total_return, 2),
            "total_return_abs": round(self.total_return_abs, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "max_drawdown_abs": round(self.max_drawdown_abs, 2),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
        }
