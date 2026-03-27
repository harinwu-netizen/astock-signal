# -*- coding: utf-8 -*-
"""
持仓模型
"""

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from config import get_config

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """单只持仓"""
    id: str              # UUID
    code: str            # 股票代码
    name: str            # 股票名称

    # 建仓信息
    buy_date: str        # 建仓日期 "YYYY-MM-DD"
    buy_price: float     # 买入价
    quantity: int        # 持仓量（手，1手=100股）
    cost: float          # 总成本（含手续费）

    # 当前状态
    current_price: float = 0.0  # 当前价
    unrealized_pnl: float = 0.0  # 浮动盈亏
    pnl_pct: float = 0.0         # 盈亏比例(%)

    # 止损止盈
    stop_loss: float = 0.0      # 止损价（ATR动态）
    take_profit: float = 0.0    # 止盈价
    trailing_stop: float = 0.0  # 移动止损价

    # 信号追踪
    latest_buy_signals: int = 0  # 最新买点信号数
    latest_sell_signals: int = 0  # 最新卖点信号数

    # 状态
    status: str = "open"   # open / closed / stopped
    closed_at: str = ""    # 平仓时间
    closed_reason: str = "" # 平仓原因

    def update_current(self, current_price: float):
        """更新当前价格和盈亏"""
        self.current_price = current_price
        self.unrealized_pnl = (current_price - self.buy_price) * self.quantity * 100
        self.pnl_pct = (current_price - self.buy_price) / self.buy_price * 100

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(**d)


@dataclass
class Portfolio:
    """账户组合总览"""
    total_capital: float           # 总本金
    available_cash: float          # 可用资金
    total_value: float            # 总市值
    positions: List[Position]      # 当前持仓列表
    total_pnl: float              # 总盈亏（含手续费）
    total_pnl_pct: float          # 总盈亏比例

    @property
    def position_count(self) -> int:
        return len([p for p in self.positions if p.status == "open"])

    @property
    def locked_capital(self) -> float:
        """锁定资金（持仓占用）"""
        return sum(p.cost for p in self.positions if p.status == "open")

    def to_dict(self) -> dict:
        return {
            "total_capital": self.total_capital,
            "available_cash": self.available_cash,
            "total_value": self.total_value,
            "positions": [p.to_dict() for p in self.positions],
            "total_pnl": self.total_pnl,
            "total_pnl_pct": self.total_pnl_pct,
            "position_count": self.position_count,
        }


class PositionStore:
    """持仓持久化"""

    def __init__(self, filepath: str = ""):
        config = get_config()
        self.filepath = filepath or config.positions_path
        Path(self.filepath).parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> List[Position]:
        """加载持仓列表"""
        path = Path(self.filepath)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [Position.from_dict(p) for p in data]
        except Exception as e:
            logger.error(f"加载持仓失败: {e}")
            return []

    def save(self, positions: List[Position]) -> bool:
        """保存持仓列表"""
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump([p.to_dict() for p in positions], f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存持仓失败: {e}")
            return False

    def get_open_positions(self) -> List[Position]:
        """获取所有未平仓持仓"""
        return [p for p in self.load() if p.status == "open"]

    def find_position(self, code: str) -> Optional[Position]:
        """查找某股票的持仓"""
        for p in self.load():
            if p.code == code and p.status == "open":
                return p
        return None
