# -*- coding: utf-8 -*-
"""
Phase 4: 影子交易追踪器

在验证期（W_new 影子追踪 vs W_old 实盘）同时运行：
- 实盘交易：用 W_old 权重，正常执行
- 影子交易：用 W_new 权重，记录"按新权重会买什么、什么价格"

关键技术点：
- 影子买入价格 = 当日收盘价（而非成交价）
- 影子卖出价格 = N日后收盘价
- 不占用实际资金，只记录用于对比
"""

import csv
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).parent.parent
DECISION_LOG = _BASE_DIR / "evolution" / "decision_log.csv"
SHADOW_LOG = _BASE_DIR / "evolution" / "shadow_log.csv"
STATS_FILE = _BASE_DIR / "evolution" / "signal_stats.json"
CYCLE_STATE_FILE = _BASE_DIR / "evolution" / "cycle_state.json"


def is_verifying() -> bool:
    """检查当前是否处于验证期"""
    if not os.path.exists(CYCLE_STATE_FILE):
        return False
    with open(CYCLE_STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    return state.get("current_phase") == "verifying"


def get_verifying_weight_version() -> Optional[str]:
    """获取当前验证中的权重版本"""
    if not is_verifying():
        return None
    with open(CYCLE_STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    # 验证期的版本 = current_weight_version
    return state.get("current_weight_version", "W1")


def get_verifying_weights() -> Dict:
    """获取验证中的权重配置"""
    version = get_verifying_weight_version()
    if not version:
        return {}
    ver_file = _BASE_DIR / "evolution" / f"weights_{version.lower()}.json"
    if os.path.exists(ver_file):
        with open(ver_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ============================================================================
# 影子跟踪核心
# ============================================================================

def track_shadow(signal, market_status: str, close_price: float) -> bool:
    """
    记录影子交易（验证期调用）

    Args:
        signal: 当前信号对象
        market_status: 大盘状态
        close_price: 当日收盘价（作为影子买入价）
    """
    if not is_verifying():
        return False

    try:
        # 记录到影子日志
        from evolution.decision_logger import log_shadow_decision
        log_shadow_decision(signal, market_status, shadow_price=close_price)
        logger.debug(f"[Shadow] 影子记录: {signal.code} @ {close_price}")
        return True
    except Exception as e:
        logger.error(f"[Shadow] 影子记录失败: {e}")
        return False


def fill_shadow_results():
    """
    补充影子交易的后续收益（收盘后调用）
    读取 shadow_log，对每条BUY记录，计算5日/10日后的收益
    """
    if not os.path.exists(SHADOW_LOG):
        return

    try:
        rows = []
        with open(SHADOW_LOG, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            return

        from data_provider.txstock import TxStock
        tx = TxStock()

        updated = 0
        for i, row in enumerate(rows):
            if row.get("result_filled") == "1":
                continue

            if row.get("decision") != "BUY":
                continue

            code = row.get("code", "")
            shadow_price = float(row.get("shadow_price") or 0)
            trade_date = row.get("trade_date", "")

            if not shadow_price or shadow_price <= 0:
                continue

            # 计算5日后价格
            try:
                start_dt = datetime.strptime(trade_date, "%Y-%m-%d")
            except:
                continue

            # 获取未来10天的历史数据
            end_dt = start_dt + timedelta(days=15)
            end_str = end_dt.strftime("%Y-%m-%d")

            hist = tx.get_history(code, days=20)
            if not hist:
                continue

            # 找到交易日期之后的数据
            future_dates = [h for h in hist if h.get("date", "") > trade_date]
            if len(future_dates) < 5:
                continue

            price_5d = future_dates[4].get("close", 0)
            price_10d = future_dates[9].get("close", 0) if len(future_dates) >= 10 else price_5d

            change_5d = (price_5d - shadow_price) / shadow_price * 100 if shadow_price > 0 else 0
            change_10d = (price_10d - shadow_price) / shadow_price * 100 if shadow_price > 0 else 0

            rows[i]["shadow_result_5d"] = f"{change_5d:.2f}"
            rows[i]["shadow_result_10d"] = f"{change_10d:.2f}"
            rows[i]["result_filled"] = "1"
            updated += 1

        if updated > 0:
            # 写回文件
            fields = list(rows[0].keys())
            with open(SHADOW_LOG, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            logger.info(f"[Shadow] 补充了{updated}条影子收益数据")

    except Exception as e:
        logger.error(f"[Shadow] 补充影子收益失败: {e}")


def get_shadow_summary() -> Dict:
    """
    获取影子交易汇总（验证期结束时使用）
    """
    if not os.path.exists(SHADOW_LOG):
        return {}

    with open(SHADOW_LOG, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    buy_rows = [r for r in rows if r.get("decision") == "BUY"]
    filled = [r for r in buy_rows if r.get("result_filled") == "1" and r.get("shadow_result_5d")]

    if not filled:
        return {"total_shadow_trades": len(buy_rows), "filled": 0}

    changes_5d = [float(r["shadow_result_5d"]) for r in filled]
    changes_10d = [float(r["shadow_result_10d"]) for r in filled if r.get("shadow_result_10d")]

    wins_5d = sum(1 for c in changes_5d if c > 0)
    wins_10d = sum(1 for c in changes_10d if c > 0) if changes_10d else 0

    return {
        "total_shadow_trades": len(buy_rows),
        "filled": len(filled),
        "avg_change_5d": sum(changes_5d) / len(changes_5d) if changes_5d else 0,
        "avg_change_10d": sum(changes_10d) / len(changes_10d) if changes_10d else 0,
        "win_rate_5d": wins_5d / len(changes_5d) if changes_5d else 0,
        "win_rate_10d": wins_10d / len(changes_10d) if changes_10d else 0,
    }


def get_real_summary() -> Dict:
    """
    获取实盘交易汇总（验证期结束时使用）
    从 decision_log 读取BUY记录，结合 position_store 计算实际收益
    """
    from evolution.decision_logger import get_decision_log
    from evolution.weight_manager import _load_cycle_state
    from models.position import PositionStore

    state = _load_cycle_state()
    ver_start = state.get("verification_start")
    if not ver_start:
        return {}

    # 从决策日志取验证期内的BUY记录
    rows = get_decision_log()
    ver_rows = [r for r in rows if r.get("trade_date", "") >= ver_start]
    buy_rows = [r for r in ver_rows if r.get("decision") == "BUY"]

    if not buy_rows:
        return {"total_real_trades": 0}

    # 从 position_store 找每笔BUY对应持仓，计算已实现盈亏
    position_store = PositionStore()
    positions = position_store.get_all_positions()

    realized_pnl_list = []
    unrealized_pnl_list = []

    for buy in buy_rows:
        code = buy.get("code", "")
        buy_price = float(buy.get("price", 0))
        buy_date = buy.get("trade_date", "")

        # 找对应的持仓记录
        pos = None
        for p in positions:
            if p.code == code and p.buy_date == buy_date:
                pos = p
                break

        if pos:
            realized_pnl_list.append(pos.unrealized_pnl)
        else:
            # 持仓已平仓，从持仓记录里找已实现盈亏
            closed = position_store.get_closed_positions()
            for c in closed:
                if c.code == code and c.buy_date == buy_date:
                    realized_pnl_list.append(c.unrealized_pnl)
                    break

    all_pnl = realized_pnl_list + unrealized_pnl_list
    total_real_trades = len(buy_rows)

    return {
        "total_real_trades": total_real_trades,
        "avg_pnl": sum(all_pnl) / len(all_pnl) if all_pnl else 0,
        "total_pnl": sum(all_pnl) if all_pnl else 0,
        "note": "盈亏数据来自持仓记录",
    }
