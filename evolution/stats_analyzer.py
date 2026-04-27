# -*- coding: utf-8 -*-
"""
Phase 2: 信号有效性统计分析

每日收盘后自动运行（绑定收盘扫描 Cron）
从 decision_log.csv 读取历史数据，统计各信号类型的胜率和有效性
输出 signal_stats.json，供 Phase 3 权重调整使用
"""

import json
import logging
import os
import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).parent.parent
DECISION_LOG = _BASE_DIR / "evolution" / "decision_log.csv"
STATS_FILE = _BASE_DIR / "evolution" / "signal_stats.json"

# 默认权重配置
DEFAULT_WEIGHTS = {
    "ma_bull": 1.0,       # MA多头排列
    "ma_cross": 1.0,      # MA金叉（5>10）
    "rsi_oversold": 1.0,  # RSI超卖反弹
    "rsi_healthy": 1.0,   # RSI健康区间
    "macd_cross": 1.0,    # MACD金叉
    "macd_bull": 1.0,     # MACD多头
    "kdj_cross": 1.0,     # KDJ金叉
    "boll_lower": 1.0,    # 布林下轨支撑
    "volume_up": 1.0,      # 放量上涨
    "volume_shrink": 1.0,  # 缩量下跌
    "money_flow": 1.0,     # 资金流入
}

# 最低样本门槛
MIN_SAMPLES = 5  # 小于此值不参与统计（Phase 1 早期用）
EFFECTIVE_MIN_SAMPLES = 20  # 达到此门槛才建议调整权重

# 调整幅度限制
MAX_WEIGHT_MULTIPLE = 1.3
MIN_WEIGHT_MULTIPLE = 0.7


def _load_logs(days: int = 90) -> List[dict]:
    """加载近N天的决策日志"""
    if not os.path.exists(DECISION_LOG):
        return []

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = []

    with open(DECISION_LOG, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("trade_date", "") >= cutoff:
                rows.append(row)

    return rows


def _parse_signals(buy_signals_str: str) -> List[str]:
    """解析买入信号JSON字符串为列表"""
    if not buy_signals_str:
        return []
    try:
        return json.loads(buy_signals_str)
    except:
        return [s.strip() for s in buy_signals_str.split("+") if s.strip()]


def _extract_signal_types(rows: List[dict]) -> Dict[str, List[dict]]:
    """
    从日志中提取各信号类型的记录

    Returns:
        {
            "MA多头排列": [row, row, ...],
            "RSI超卖": [row, ...],
            ...
        }
    """
    signal_rows = defaultdict(list)

    for row in rows:
        decision = row.get("decision", "")
        buy_signals = _parse_signals(row.get("buy_signals_detail", ""))

        for sig in buy_signals:
            sig_clean = sig.strip()
            if not sig_clean or sig_clean == "观望":
                continue
            # 分类信号
            if "MA" in sig_clean or "均线" in sig_clean or "多头" in sig_clean:
                signal_rows["ma_bull"].append(row)
            if "金叉" in sig_clean or "MA5>MA10" in sig_clean:
                signal_rows["ma_cross"].append(row)
            if "RSI" in sig_clean:
                if "超卖" in sig_clean or "<" in sig_clean:
                    signal_rows["rsi_oversold"].append(row)
                else:
                    signal_rows["rsi_healthy"].append(row)
            if "MACD" in sig_clean:
                if "金叉" in sig_clean:
                    signal_rows["macd_cross"].append(row)
                if "多头" in sig_clean or " DIF" in sig_clean:
                    signal_rows["macd_bull"].append(row)
            if "KDJ" in sig_clean or "J值" in sig_clean:
                signal_rows["kdj_cross"].append(row)
            if "布林" in sig_clean or "下轨" in sig_clean:
                signal_rows["boll_lower"].append(row)
            if "放量" in sig_clean:
                signal_rows["volume_up"].append(row)
            if "缩量" in sig_clean:
                signal_rows["volume_shrink"].append(row)

    return dict(signal_rows)


def _evaluate_signals(signal_rows: Dict[str, List[dict]], lookback_days: int = 30) -> Dict:
    """
    评估各信号类型的有效性

    Args:
        signal_rows: 各信号的记录
        lookback_days: 统计近N天的结果

    Returns:
        {
            "ma_bull": {
                "count": 30,
                "win_rate_5d": 0.62,
                "avg_change_5d": 2.3,
                "avg_change_10d": 3.1,
                "effective": True/False
            }
        }
    """
    results = {}

    for sig_name, rows in signal_rows.items():
        if len(rows) < MIN_SAMPLES:
            results[sig_name] = {
                "count": len(rows),
                "win_rate_5d": 0.0,
                "avg_change_5d": 0.0,
                "avg_change_10d": 0.0,
                "effective": False,
                "reason": f"样本不足({len(rows)}<{MIN_SAMPLES})",
            }
            continue

        # 近 lookback_days 天的记录（这里简化处理，默认日志里的 result_5d 在记录时为空，
        # 实际需要在后续补充。但由于Phase1数据尚无收益数据，
        # 先用决策质量做代理指标）
        # TODO: 后续在影子交易结果补充后，这里改为真实收益数据

        # 代理指标：用 buy_count / (buy_count + sell_count) 作为信号强度
        valid = [r for r in rows if r.get("decision") in ("BUY", "HOLD", "WATCH")]
        buy_decisions = [r for r in valid if r.get("decision") == "BUY"]

        if not valid:
            results[sig_name] = {
                "count": len(rows),
                "win_rate_5d": 0.0,
                "avg_change_5d": 0.0,
                "avg_change_10d": 0.0,
                "effective": False,
                "reason": "无有效决策样本",
            }
            continue

        # 用平均 position_ratio 作为信号质量代理
        avg_pos = sum(float(r.get("position_ratio", 0)) for r in buy_decisions) / max(1, len(buy_decisions))
        avg_buy_count = sum(float(r.get("buy_count", 0)) for r in valid) / len(valid)

        # 信号触发后的上涨概率代理：买入信号数多的记录往往后续表现更好
        # 这里用 buy_count >= 3 作为强势信号阈值
        strong_signals = [r for r in valid if float(r.get("buy_count", 0)) >= 3]
        strong_rate = len(strong_signals) / len(valid) if valid else 0

        results[sig_name] = {
            "count": len(rows),
            "win_rate_5d": strong_rate,  # 代理指标：强势信号占比
            "avg_change_5d": avg_buy_count,  # 代理指标：平均买入信号数
            "avg_change_10d": avg_pos * 100,  # 代理指标：平均仓位建议
            "effective": len(rows) >= EFFECTIVE_MIN_SAMPLES,
            "reason": "OK",
        }

    return results


def update_stats() -> Dict:
    """
    更新信号统计（每日收盘后调用）

    Returns:
        signal_stats 字典
    """
    logger.info("[Evolution/Stats] 开始更新信号有效性统计...")

    rows = _load_logs()
    if not rows:
        logger.info("[Evolution/Stats] 决策日志为空，跳过统计")
        return {}

    signal_rows = _extract_signal_types(rows)
    evaluation = _evaluate_signals(signal_rows)

    stats = {
        "updated_at": datetime.now().isoformat(),
        "total_records": len(rows),
        "signal_types": evaluation,
        "weights": _load_weights(),
    }

    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    logger.info(f"[Evolution/Stats] 统计已更新，共{len(rows)}条记录，{len(evaluation)}种信号类型")

    # 打印有效信号
    effective = {k: v for k, v in evaluation.items() if v.get("effective")}
    if effective:
        logger.info(f"[Evolution/Stats] 有效信号（样本≥{EFFECTIVE_MIN_SAMPLES}）：{list(effective.keys())}")

    return stats


def _load_weights() -> Dict:
    """加载当前权重配置"""
    WEIGHTS_FILE = _BASE_DIR / "evolution" / "weights.json"
    if os.path.exists(WEIGHTS_FILE):
        with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_WEIGHTS.copy()


def get_stats() -> Dict:
    """读取当前统计结果"""
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ============================================================================
# CLI: 手动触发统计
# ============================================================================

if __name__ == "__main__":
    stats = update_stats()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
