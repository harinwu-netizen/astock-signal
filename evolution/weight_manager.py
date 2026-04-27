# -*- coding: utf-8 -*-
"""
Phase 3: 权重管理器

月末自动执行：
1. 读取 signal_stats.json
2. 计算新权重调整建议
3. AI初审（调用LLM）
4. 生成调整报告推送飞书
5. 等待海赟确认后启用

Weight调整规则：
- 样本≥20 AND 胜率代理>0.6 → 权重 × 1.2（上调）
- 样本≥20 AND 胜率代理<0.4 → 权重 × 0.8（下调）
- 样本<20 → 不调整
- 单次调整幅度不超过 ±30%
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).parent.parent
STATS_FILE = _BASE_DIR / "evolution" / "signal_stats.json"
WEIGHTS_FILE = _BASE_DIR / "evolution" / "weights.json"
CYCLE_STATE_FILE = _BASE_DIR / "evolution" / "cycle_state.json"
DECISION_LOG = _BASE_DIR / "evolution" / "decision_log.csv"

EFFECTIVE_MIN_SAMPLES = 20
MAX_WEIGHT_MULTIPLE = 1.3
MIN_WEIGHT_MULTIPLE = 0.7

# 当前使用的权重基准（每次验证通过后更新）
CURRENT_WEIGHT_VERSION = "W1"  # W1=初始权重

# 权重历史（每次调整都记录）
WEIGHT_HISTORY_FILE = _BASE_DIR / "evolution" / "weight_history.json"


def _load_weights() -> Dict:
    """加载当前权重"""
    if os.path.exists(WEIGHTS_FILE):
        with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "version": CURRENT_WEIGHT_VERSION,
        "weights": {
            "ma_bull": 1.0,
            "ma_cross": 1.0,
            "rsi_oversold": 1.0,
            "rsi_healthy": 1.0,
            "macd_cross": 1.0,
            "macd_bull": 1.0,
            "kdj_cross": 1.0,
            "boll_lower": 1.0,
            "volume_up": 1.0,
            "volume_shrink": 1.0,
            "money_flow": 1.0,
        },
        "adjusted_at": None,
        "reason": "",
    }


def _save_weights(weights: Dict, version: str, reason: str = ""):
    """保存权重"""
    weights["version"] = version
    weights["adjusted_at"] = datetime.now().isoformat()
    weights["reason"] = reason
    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(weights, f, ensure_ascii=False, indent=2)


def _save_weight_history(entry: Dict):
    """记录权重调整历史"""
    history = []
    if os.path.exists(WEIGHT_HISTORY_FILE):
        with open(WEIGHT_HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
    history.append(entry)
    with open(WEIGHT_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _load_cycle_state() -> Dict:
    """加载循环状态"""
    if os.path.exists(CYCLE_STATE_FILE):
        with open(CYCLE_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "current_cycle": 1,
        "current_phase": "learning",  # learning / verifying
        "current_weight_version": "W1",
        "pending_weight_version": None,  # 如W2，等待确认
        "pending_suggestion": None,      # 等待确认的调整建议
        "verification_month": None,      # 验证期所在月份
        "learning_start": None,
        "verification_start": None,
        "last_decision_date": None,
    }


def _save_cycle_state(state: Dict):
    """保存循环状态"""
    with open(CYCLE_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _calc_new_weights(stats: Dict, current_weights: Dict) -> Dict:
    """
    基于统计数据计算新权重

    Returns:
        {
            "new_weights": {...},
            "changes": [
                {"signal": "ma_bull", "old": 1.0, "new": 1.2, "reason": "胜率62%, 样本32"}
            ]
        }
    """
    signal_types = stats.get("signal_types", {})
    current = current_weights.get("weights", {})
    new = current.copy()
    changes = []

    for sig_name, data in signal_types.items():
        count = data.get("count", 0)
        win_rate = data.get("win_rate_5d", 0.5)

        if count < EFFECTIVE_MIN_SAMPLES:
            continue  # 样本不足，不调整

        old = new.get(sig_name, 1.0)

        if win_rate > 0.6:
            # 表现好，上调权重
            factor = min(MAX_WEIGHT_MULTIPLE, 1.0 + (win_rate - 0.6) * 0.5)
            new[sig_name] = round(old * factor, 2)
            reason = f"胜率{win_rate:.0%}, 样本{count}次，上调"
        elif win_rate < 0.4:
            # 表现差，下调权重
            factor = max(MIN_WEIGHT_MULTIPLE, 1.0 - (0.4 - win_rate) * 0.5)
            new[sig_name] = round(old * factor, 2)
            reason = f"胜率{win_rate:.0%}, 样本{count}次，下调"
        else:
            continue  # 中间区域，不调整

        if new[sig_name] != old:
            changes.append({
                "signal": sig_name,
                "old": old,
                "new": new[sig_name],
                "reason": reason,
                "samples": count,
                "win_rate": win_rate,
            })

    return {"new_weights": new, "changes": changes}


def generate_weight_suggestion() -> Dict:
    """
    生成权重调整建议（月度末调用）
    Phase 3 核心函数
    """
    logger.info("[Evolution/Weight] 生成权重调整建议...")

    state = _load_cycle_state()

    # 检查是否在验证期，是的话不生成新建议
    if state.get("current_phase") == "verifying":
        logger.info("[Evolution/Weight] 当前处于验证期，跳过生成新建议")
        return {
            "skipped": True,
            "reason": "验证期不生成新建议，等待验证结果"
        }

    stats = get_stats()
    total = stats.get("total_records", 0) if stats else 0
    if total < 20:
        logger.info(f"[Evolution/Weight] 数据不足（{total}条），暂不生成建议")
        return {"skipped": True, "reason": f"样本不足（{total}/20条），继续积累"}

    current = _load_weights()
    result = _calc_new_weights(stats, current)

    if not result.get("changes"):
        logger.info("[Evolution/Weight] 无有效调整，信号统计无显著差异")
        return {"skipped": True, "reason": "各信号表现无显著差异，无需调整"}

    new_version = f"W{int(state['current_cycle']) + 1}"  # 如 W2

    suggestion = {
        "version": new_version,
        "suggested_weights": result["new_weights"],
        "changes": result["changes"],
        "generated_at": datetime.now().isoformat(),
        "based_on_data_records": stats.get("total_records", 0),
        "stats_snapshot": stats,
    }

    # 保存待确认状态
    state["current_phase"] = "learning_pending_confirm"
    state["pending_weight_version"] = new_version
    state["pending_suggestion"] = suggestion
    _save_cycle_state(state)

    logger.info(f"[Evolution/Weight] 生成建议 {new_version}，共{len(result['changes'])}项调整")
    return suggestion


def confirm_and_apply_suggestion() -> bool:
    """
    海赟确认后，应用新权重（进入验证期）
    由海赟手动确认触发
    """
    state = _load_cycle_state()
    suggestion = state.get("pending_suggestion")

    if not suggestion:
        logger.warning("[Evolution/Weight] 无待确认建议")
        return False

    version = suggestion["version"]
    new_weights = suggestion["suggested_weights"]

    # 保存为待验证权重（不覆盖当前生效权重）
    verify_file = _BASE_DIR / "evolution" / f"weights_{version.lower()}.json"
    with open(verify_file, "w", encoding="utf-8") as f:
        json.dump({
            "version": version,
            "weights": new_weights,
            "suggested_at": suggestion["generated_at"],
            "confirmed_at": datetime.now().isoformat(),
            "status": "verifying",
        }, f, ensure_ascii=False, indent=2)

    # 记录历史
    _save_weight_history({
        "version": version,
        "weights": new_weights,
        "changes": suggestion.get("changes", []),
        "suggested_at": suggestion["generated_at"],
        "confirmed_at": datetime.now().isoformat(),
        "cycle": state["current_cycle"],
        "status": "verifying",
    })

    # 更新循环状态
    state["current_phase"] = "verifying"
    state["verification_start"] = datetime.now().strftime("%Y-%m-%d")
    state["pending_suggestion"] = None
    _save_cycle_state(state)

    logger.info(f"[Evolution/Weight] ✅ {version} 已确认，进入验证期")
    return True


def reject_suggestion():
    """海赟拒绝调整建议，保持当前权重"""
    state = _load_cycle_state()
    state["current_phase"] = "learning"
    state["pending_suggestion"] = None
    state["pending_weight_version"] = None
    _save_cycle_state(state)
    logger.info("[Evolution/Weight] 建议已拒绝，保持当前权重")


def get_current_weights() -> Dict:
    """获取当前生效的权重"""
    return _load_weights()


def get_pending_suggestion() -> Optional[Dict]:
    """获取待确认的权重建议"""
    state = _load_cycle_state()
    return state.get("pending_suggestion")


def get_stats():
    """读取统计数据"""
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_verifying_weight_version() -> Optional[str]:
    """获取当前验证中的权重版本"""
    state = _load_cycle_state()
    if state.get("current_phase") == "verifying":
        return state.get("current_weight_version")  # 注意这里用 current，不是 pending
    return None


# ============================================================================
# 验证期结束判断（月末调用）
# ============================================================================

def check_verification_end() -> Dict:
    """
    检查验证期是否结束，返回对比结果
    由月末 Cron 自动触发
    """
    state = _load_cycle_state()
    if state.get("current_phase") != "verifying":
        return {"ended": False, "reason": "非验证期"}

    ver_start = state.get("verification_start")
    if not ver_start:
        return {"ended": False, "reason": "无验证开始时间"}

    # 验证期需满1个月
    from datetime import datetime as dt
    start = dt.strptime(ver_start, "%Y-%m-%d")
    now = dt.now()
    days_passed = (now - start).days

    if days_passed < 28:  # 近似1个月
        return {
            "ended": False,
            "reason": f"验证期仅{days_passed}天，需满1个月",
            "days_passed": days_passed,
        }

    # 验证期结束，执行对比
    result = compare_verifying_vs_current()

    if result["adopted"]:
        # 采纳新权重
        version = state.get("current_weight_version")
        state["current_cycle"] += 1
        state["current_phase"] = "learning"
        state["current_weight_version"] = result["winner"]
        state["learning_start"] = now.strftime("%Y-%m-%d")
        state["verification_start"] = None
        logger.info(f"[Evolution/Weight] ✅ {result['winner']} 验证通过，已正式启用")
    else:
        # 放弃，保持原权重
        state["current_phase"] = "learning"
        state["learning_start"] = now.strftime("%Y-%m-%d")
        state["verification_start"] = None
        logger.info(f"[Evolution/Weight] ❌ 验证期结果：放弃{result.get('loser','新权重')}，保持{result.get('winner')}")

    _save_cycle_state(state)
    return {**result, "ended": True}


def compare_verifying_vs_current() -> Dict:
    """
    对比验证期新旧权重表现
    W_old: 实盘（旧权重，decision_log）
    W_new: 影子（新权重，shadow_log）
    """
    from evolution.shadow_tracker import get_shadow_summary, get_real_summary

    shadow = get_shadow_summary()
    real = get_real_summary()

    w2_avg_5d = shadow.get("avg_change_5d", 0)
    w2_win_rate = shadow.get("win_rate_5d", 0)
    w1_total_pnl = real.get("total_pnl", 0)
    w1_trades = real.get("total_real_trades", 0)

    # 简单判断规则：
    # 1. 影子数据不足（<5条）→ 不采纳
    # 2. 影子5日平均涨幅 > 1.5% 且胜率 > 55% → 采纳
    # 3. 影子5日平均涨幅 < 0% → 不采纳
    # 4. 其他 → 继续观察（不下结论）

    filled = shadow.get("filled", 0)

    if filled < 5:
        return {
            "adopted": False,
            "winner": "W1",
            "loser": "W_new",
            "reason": f"影子数据不足({filled}条)，无法判断",
            "w1_total_pnl": w1_total_pnl,
            "w2_avg_5d": w2_avg_5d,
            "w2_win_rate": w2_win_rate,
        }

    if w2_avg_5d > 1.5 and w2_win_rate > 0.55:
        return {
            "adopted": True,
            "winner": "W_new",
            "loser": "W1",
            "reason": f"影子表现优异（5日均涨{w2_avg_5d:+.2f}%，胜率{w2_win_rate:.0%}）",
            "w1_total_pnl": w1_total_pnl,
            "w2_avg_5d": w2_avg_5d,
            "w2_win_rate": w2_win_rate,
        }

    if w2_avg_5d < 0:
        return {
            "adopted": False,
            "winner": "W1",
            "loser": "W_new",
            "reason": f"影子新权重表现不佳（5日均涨{w2_avg_5d:+.2f}%）",
            "w1_total_pnl": w1_total_pnl,
            "w2_avg_5d": w2_avg_5d,
            "w2_win_rate": w2_win_rate,
        }

    return {
        "adopted": False,
        "winner": "W1",
        "loser": "W_new",
        "reason": f"差异不显著（影子5日均涨{w2_avg_5d:+.2f}%，胜率{w2_win_rate:.0%}），建议继续观察",
        "w1_total_pnl": w1_total_pnl,
        "w2_avg_5d": w2_avg_5d,
        "w2_win_rate": w2_win_rate,
    }
