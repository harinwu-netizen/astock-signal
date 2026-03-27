# -*- coding: utf-8 -*-
"""
股票池模型
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from config import get_config

logger = logging.getLogger(__name__)


@dataclass
class StockEntry:
    """单只股票条目"""
    code: str           # 股票代码，如 "000629"
    name: str           # 股票名称，如 "钒钛股份"
    added_at: str       # 添加日期，格式 "YYYY-MM-DD"
    enabled: bool = True  # 是否启用监控

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StockEntry":
        return cls(
            code=d.get("code", ""),
            name=d.get("name", ""),
            added_at=d.get("added_at", datetime.now().strftime("%Y-%m-%d")),
            enabled=d.get("enabled", True),
        )


@dataclass
class WatchlistSettings:
    """股票池设置"""
    auto_trade: bool = False         # 自动交易开关（默认关闭）
    notify_only: bool = True          # 只推送不交易（默认开启）
    max_positions: int = 3            # 最大持仓数
    total_capital: float = 100000.0   # 总本金

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WatchlistSettings":
        return cls(
            auto_trade=d.get("auto_trade", False),
            notify_only=d.get("notify_only", True),
            max_positions=d.get("max_positions", 3),
            total_capital=float(d.get("total_capital", 100000)),
        )


@dataclass
class Watchlist:
    """股票池"""
    stocks: List[StockEntry]
    settings: WatchlistSettings

    def to_dict(self) -> dict:
        return {
            "stocks": [s.to_dict() for s in self.stocks],
            "settings": self.settings.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Watchlist":
        stocks = [StockEntry.from_dict(s) for s in d.get("stocks", [])]
        settings = WatchlistSettings.from_dict(d.get("settings", {}))
        return cls(stocks=stocks, settings=settings)

    def get_enabled_stocks(self) -> List[StockEntry]:
        """获取所有启用的股票"""
        return [s for s in self.stocks if s.enabled]

    def find_by_code(self, code: str) -> Optional[StockEntry]:
        """根据代码查找股票"""
        for s in self.stocks:
            if s.code == code:
                return s
        return None

    def add_stock(self, code: str, name: str = "") -> bool:
        """添加股票到池中"""
        if self.find_by_code(code):
            logger.warning(f"股票 {code} 已在股票池中")
            return False
        entry = StockEntry(
            code=code,
            name=name or f"股票{code}",
            added_at=datetime.now().strftime("%Y-%m-%d"),
            enabled=True,
        )
        self.stocks.append(entry)
        return True

    def remove_stock(self, code: str) -> bool:
        """从池中删除股票"""
        for i, s in enumerate(self.stocks):
            if s.code == code:
                self.stocks.pop(i)
                return True
        return False

    def enable_stock(self, code: str) -> bool:
        """启用股票监控"""
        s = self.find_by_code(code)
        if s:
            s.enabled = True
            return True
        return False

    def disable_stock(self, code: str) -> bool:
        """禁用股票监控"""
        s = self.find_by_code(code)
        if s:
            s.enabled = False
            return True
        return False


class WatchlistStore:
    """股票池持久化"""

    def __init__(self, filepath: str = ""):
        config = get_config()
        self.filepath = filepath or config.watchlist_path
        # 确保目录存在
        Path(self.filepath).parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Watchlist:
        """从文件加载股票池"""
        path = Path(self.filepath)
        if not path.exists():
            # 返回默认空股票池
            return Watchlist(stocks=[], settings=WatchlistSettings())
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Watchlist.from_dict(data)
        except Exception as e:
            logger.error(f"加载股票池失败: {e}")
            return Watchlist(stocks=[], settings=WatchlistSettings())

    def save(self, watchlist: Watchlist) -> bool:
        """保存股票池到文件"""
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(watchlist.to_dict(), f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存股票池失败: {e}")
            return False
