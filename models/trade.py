# -*- coding: utf-8 -*-
"""
交易记录模型
"""

import sqlite3
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from config import get_config

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """交易记录"""
    id: str              # UUID
    code: str            # 股票代码
    name: str            # 股票名称
    action: str          # BUY / SELL / STOP_LOSS / TAKE_PROFIT
    price: float         # 成交价
    quantity: int        # 成交量（手）
    amount: float        # 成交金额
    commission: float    # 手续费
    stamp_tax: float     # 印花税（卖时）
    buy_signals: int     # 买入信号数
    sell_signals: int    # 卖出信号数
    atr: float           # ATR值
    stop_loss: float     # 设置的止损价
    position_id: str      # 对应持仓ID
    pre_check_passed: bool  # 交易前检查是否通过
    created_at: str      # 时间戳
    trade_date: str      # 交易日期 "YYYY-MM-DD"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TradeRecord":
        return cls(**d)


class TradeStore:
    """交易记录持久化（SQLite）"""

    def __init__(self, db_path: str = ""):
        config = get_config()
        self.db_path = db_path or config.database_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                name TEXT,
                action TEXT NOT NULL,
                price REAL,
                quantity INTEGER,
                amount REAL,
                commission REAL,
                stamp_tax REAL,
                buy_signals INTEGER,
                sell_signals INTEGER,
                atr REAL,
                stop_loss REAL,
                position_id TEXT,
                pre_check_passed INTEGER,
                created_at TEXT,
                trade_date TEXT
            )
        """)
        conn.commit()
        conn.close()

    def add(self, trade: TradeRecord) -> bool:
        """添加交易记录"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            d = trade.to_dict()
            cursor.execute("""
                INSERT INTO trades (
                    id, code, name, action, price, quantity, amount,
                    commission, stamp_tax, buy_signals, sell_signals,
                    atr, stop_loss, position_id, pre_check_passed,
                    created_at, trade_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["id"], d["code"], d["name"], d["action"], d["price"],
                d["quantity"], d["amount"], d["commission"], d["stamp_tax"],
                d["buy_signals"], d["sell_signals"], d["atr"], d["stop_loss"],
                d["position_id"], 1 if d["pre_check_passed"] else 0,
                d["created_at"], d["trade_date"],
            ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"保存交易记录失败: {e}")
            return False

    def get_all(self) -> List[TradeRecord]:
        """获取所有交易记录"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        return [TradeRecord(**dict(row)) for row in rows]

    def get_by_code(self, code: str) -> List[TradeRecord]:
        """获取某股票的所有交易记录"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE code=? ORDER BY created_at DESC", (code,))
        rows = cursor.fetchall()
        conn.close()
        return [TradeRecord(**dict(row)) for row in rows]

    def get_today_trades(self, trade_date: str = "") -> List[TradeRecord]:
        """获取当日的交易记录"""
        if not trade_date:
            trade_date = datetime.now().strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE trade_date=? ORDER BY created_at DESC", (trade_date,))
        rows = cursor.fetchall()
        conn.close()
        return [TradeRecord(**dict(row)) for row in rows]

    def has_traded_today(self, code: str) -> bool:
        """检查某股票今日是否已交易"""
        today = datetime.now().strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM trades WHERE code=? AND trade_date=?",
            (code, today)
        )
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
