# -*- coding: utf-8 -*-
"""
Phase 1: 决策日志记录器

每次信号灯扫描完成后，自动记录每只股票的分析结果。
静默运行，不影响现有信号灯逻辑。

日志文件：evolution/decision_log.csv
影子日志：evolution/shadow_log.csv  (Phase 4 使用)
"""

import csv
import os
import json
import logging
from datetime import datetime
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# 日志文件路径
_BASE_DIR = Path(__file__).parent.parent
DECISION_LOG = _BASE_DIR / "evolution" / "decision_log.csv"
SHADOW_LOG = _BASE_DIR / "evolution" / "shadow_log.csv"
STATS_FILE = _BASE_DIR / "evolution" / "signal_stats.json"
WEIGHTS_FILE = _BASE_DIR / "evolution" / "weights.json"
CYCLE_STATE_FILE = _BASE_DIR / "evolution" / "cycle_state.json"

# 字段名（CSV 表头）
DECISION_FIELDS = [
    "timestamp",           # 记录时间
    "scan_time",           # 扫描时间（HH:MM:SS）
    "trade_date",           # 交易日期（YYYY-MM-DD）
    "code",                # 股票代码
    "name",                # 股票名称
    "price",               # 当前价格
    "change_pct",          # 涨跌幅%
    "market_status",       # 大盘状态
    "decision",            # 决策结论（BUY/WATCH/SELL/HOLD/STOP_LOSS/TAKE_PROFIT）
    "buy_count",           # 买入信号数
    "sell_count",          # 卖出信号数
    "rebound_count",       # 弱市反弹信号数
    "trend_count",         # 强市趋势信号数
    "consolidate_buy_count", # 震荡市买入信号数
    "position_ratio",      # 建议仓位 0.0-1.0
    "atr",                 # ATR波动率
    "rsi_6",              # RSI(6)
    "macd_dif",            # MACD DIF
    "macd_dea",            # MACD DEA
    "macd_bar",            # MACD 柱状图
    "ma5",                 # MA5
    "ma10",                # MA10
    "ma20",                # MA20
    "volume_ratio",        # 量比
    "buy_signals_detail",  # 买入信号明细（JSON字符串）
    "sell_signals_detail",  # 卖出信号明细（JSON字符串）
    "decision_reason",      # 决策原因
    "weight_system",        # 使用的权重系统（old/new）
    "shadow_price",        # 影子买入价格（仅shadow_log使用）
    "shadow_result_5d",    # 影子5日涨跌%（事后补充）
    "shadow_result_10d",   # 影子10日涨跌%（事后补充）
    "result_filled",       # 是否已补充结果（0/1）
]

# 影子日志额外字段
SHADOW_EXTRA = ["shadow_price", "shadow_result_5d", "shadow_result_10d", "result_filled"]


def _ensure_dir():
    """确保目录存在"""
    os.makedirs(os.path.dirname(DECISION_LOG), exist_ok=True)


def _get_fields(is_shadow: bool = False) -> list:
    """获取字段列表"""
    fields = DECISION_FIELDS[:]
    if is_shadow:
        for f in SHADOW_EXTRA:
            if f not in fields:
                fields.append(f)
    return fields


def _signal_detail_to_json(signals) -> str:
    """将信号列表转为JSON字符串"""
    if not signals:
        return "[]"
    if isinstance(signals[0], str):
        return json.dumps(signals, ensure_ascii=False)
    return json.dumps([s if isinstance(s, dict) else {"name": s} for s in signals], ensure_ascii=False)


# ============================================================================
# Phase 1: 记录决策日志
# ============================================================================

def log_decision(signal, market_status: str, weight_system: str = "old") -> bool:
    """
    记录单只股票的扫描决策到日志

    调用时机：每次 signal 计算完成后，返回前调用
    完全静默，不影响原逻辑

    Args:
        signal: UnifiedSignal / RealTimeSignal 对象
        market_status: 大盘状态字符串（强势/弱势/震荡）
        weight_system: 使用的权重系统（old/new）

    Returns:
        bool: 是否记录成功
    """
    try:
        _ensure_dir()

        now = datetime.now()
        trade_date = now.strftime("%Y-%m-%d")
        scan_time = now.strftime("%H:%M:%S")

        # 构建行数据
        row = {
            "timestamp": now.isoformat(),
            "scan_time": scan_time,
            "trade_date": trade_date,
            "code": signal.code,
            "name": signal.name,
            "price": signal.price,
            "change_pct": signal.change_pct,
            "market_status": market_status,
            "decision": signal.primary_decision if hasattr(signal, "primary_decision") else signal.decision.value,
            "buy_count": signal.buy_count,
            "sell_count": signal.sell_count,
            "rebound_count": signal.rebound_count,
            "trend_count": signal.trend_count,
            "consolidate_buy_count": signal.consolidate_buy_count,
            "position_ratio": signal.position_ratio,
            "atr": signal.atr if hasattr(signal, "atr") else 0.0,
            "rsi_6": signal.rsi_6 if hasattr(signal, "rsi_6") else 0.0,
            "macd_dif": signal.macd_dif if hasattr(signal, "macd_dif") else 0.0,
            "macd_dea": signal.macd_dea if hasattr(signal, "macd_dea") else 0.0,
            "macd_bar": signal.macd_bar if hasattr(signal, "macd_bar") else 0.0,
            "ma5": signal.ma5 if hasattr(signal, "ma5") else 0.0,
            "ma10": signal.ma10 if hasattr(signal, "ma10") else 0.0,
            "ma20": signal.ma20 if hasattr(signal, "ma20") else 0.0,
            "volume_ratio": getattr(signal, "volume_ratio", 0.0),
            "buy_signals_detail": _signal_detail_to_json(signal.buy_signals_detail),
            "sell_signals_detail": _signal_detail_to_json(signal.sell_signals_detail),
            "decision_reason": signal.primary_reason if hasattr(signal, "primary_reason") else "",
            "weight_system": weight_system,
        }

        file_path = SHADOW_LOG if weight_system == "new" else DECISION_LOG
        is_shadow = weight_system == "new"
        fields = _get_fields(is_shadow)

        file_exists = os.path.exists(file_path)

        with open(file_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

        logger.debug(f"[Evolution] 记录决策: {signal.code} {signal.name} decision={row['decision']} weight={weight_system}")
        return True

    except Exception as e:
        logger.error(f"[Evolution] 记录决策失败: {e}")
        return False


def log_shadow_decision(signal, market_status: str, shadow_price: float) -> bool:
    """
    记录影子交易决策（Phase 4 使用）

    Args:
        signal: 信号对象
        market_status: 大盘状态
        shadow_price: 影子买入价格（当日收盘价）
    """
    try:
        _ensure_dir()
        now = datetime.now()
        trade_date = now.strftime("%Y-%m-%d")
        scan_time = now.strftime("%H:%M:%S")

        decision = signal.primary_decision if hasattr(signal, "primary_decision") else signal.decision.value

        row = {
            "timestamp": now.isoformat(),
            "scan_time": scan_time,
            "trade_date": trade_date,
            "code": signal.code,
            "name": signal.name,
            "price": signal.price,
            "change_pct": signal.change_pct,
            "market_status": market_status,
            "decision": decision,
            "buy_count": signal.buy_count,
            "sell_count": signal.sell_count,
            "rebound_count": signal.rebound_count,
            "trend_count": signal.trend_count,
            "consolidate_buy_count": signal.consolidate_buy_count,
            "position_ratio": signal.position_ratio,
            "atr": signal.atr if hasattr(signal, "atr") else 0.0,
            "rsi_6": signal.rsi_6 if hasattr(signal, "rsi_6") else 0.0,
            "macd_dif": signal.macd_dif if hasattr(signal, "macd_dif") else 0.0,
            "macd_dea": signal.macd_dea if hasattr(signal, "macd_dea") else 0.0,
            "macd_bar": signal.macd_bar if hasattr(signal, "macd_bar") else 0.0,
            "ma5": signal.ma5 if hasattr(signal, "ma5") else 0.0,
            "ma10": signal.ma10 if hasattr(signal, "ma10") else 0.0,
            "ma20": signal.ma20 if hasattr(signal, "ma20") else 0.0,
            "volume_ratio": getattr(signal, "volume_ratio", 0.0),
            "buy_signals_detail": _signal_detail_to_json(signal.buy_signals_detail),
            "sell_signals_detail": _signal_detail_to_json(signal.sell_signals_detail),
            "decision_reason": signal.primary_reason if hasattr(signal, "primary_reason") else "",
            "weight_system": "new",
            "shadow_price": shadow_price,
            "shadow_result_5d": "",
            "shadow_result_10d": "",
            "result_filled": "0",
        }

        fields = _get_fields(is_shadow=True)
        file_exists = os.path.exists(SHADOW_LOG)

        with open(SHADOW_LOG, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

        logger.debug(f"[Evolution] 影子记录: {signal.code} shadow_price={shadow_price}")
        return True

    except Exception as e:
        logger.error(f"[Evolution] 影子记录失败: {e}")
        return False


# ============================================================================
# Phase 1 辅助工具
# ============================================================================

def get_decision_log(trade_date: str = None) -> list:
    """
    读取决策日志

    Args:
        trade_date: 可选，筛选特定日期

    Returns:
        list of dict
    """
    if not os.path.exists(DECISION_LOG):
        return []

    with open(DECISION_LOG, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if trade_date:
        rows = [r for r in rows if r.get("trade_date") == trade_date]

    return rows


def get_shadow_log(trade_date: str = None) -> list:
    """读取影子日志"""
    if not os.path.exists(SHADOW_LOG):
        return []

    with open(SHADOW_LOG, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if trade_date:
        rows = [r for r in rows if r.get("trade_date") == trade_date]

    return rows


def count_records(weight_system: str = "old") -> int:
    """统计已有记录数（不含表头）"""
    path = SHADOW_LOG if weight_system == "new" else DECISION_LOG
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    if not lines:
        return 0
    return max(0, len(lines) - 1)  # 减掉表头，空文件返回0
