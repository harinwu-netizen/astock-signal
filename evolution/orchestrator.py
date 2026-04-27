# -*- coding: utf-8 -*-
"""
evolution/orchestrator.py - 各阶段自动衔接调度器

由 Cron 触发，分别对应不同场景：
- 每日扫描后：Phase 1 记录 + Phase 2 统计更新
- 每日收盘后：Phase 2 统计 + Phase 4 影子结果补充
- 月末：Phase 3/5 报告生成 + AI审核
- 月末验证期结束：Phase 3/5 验证报告
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).parent.parent
CYCLE_STATE_FILE = _BASE_DIR / "evolution" / "cycle_state.json"


def _load_state() -> dict:
    if os.path.exists(CYCLE_STATE_FILE):
        with open(CYCLE_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "current_cycle": 1,
        "current_phase": "learning",  # learning / verifying
        "current_weight_version": "W1",
        "pending_suggestion": None,
        "learning_start": datetime.now().strftime("%Y-%m-%d"),
        "last_report_month": None,
    }


def _save_state(state: dict):
    with open(CYCLE_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ============================================================================
# 每日扫描后触发（Phase 1 记录）
# ============================================================================

def on_scan_completed(signals, market_status: str) -> bool:
    """
    每次扫描完成后调用
    Phase 1: 记录每只股票的决策
    Phase 4: 影子追踪（如处于验证期）
    """
    from evolution.decision_logger import log_decision
    from evolution.shadow_tracker import is_verifying, track_shadow

    if not signals:
        return False

    # 获取当日收盘价（用于影子记录）
    close_prices = {}
    try:
        from data_provider.txstock import TxStock
        tx = TxStock()
        for sig in signals:
            hist = tx.get_history(sig.code, days=2)
            if hist:
                close_prices[sig.code] = hist[-1].get("close", sig.price)
    except:
        pass

    weight_system = "old"
    if is_verifying():
        weight_system = "new"
        # 尝试获取收盘价
        for sig in signals:
            close_price = close_prices.get(sig.code, sig.price)
            track_shadow(sig, market_status, close_price)

    # Phase 1: 记录到 decision_log
    count = 0
    for sig in signals:
        log_decision(sig, market_status, weight_system=weight_system)
        count += 1

    logger.info(f"[Evolution/Orchestrator] Phase 1: 记录{count}条决策")
    return True


# ============================================================================
# 每日收盘后触发（Phase 2 统计）
# ============================================================================

def on_market_close() -> bool:
    """
    每日收盘后调用
    Phase 2: 更新信号有效性统计
    Phase 4: 补充影子交易结果
    """
    from evolution.stats_analyzer import update_stats
    from evolution.shadow_tracker import is_verifying, fill_shadow_results

    # Phase 2: 更新统计
    try:
        update_stats()
        logger.info("[Evolution/Orchestrator] Phase 2: 统计已更新")
    except Exception as e:
        logger.error(f"[Evolution/Orchestrator] Phase 2 更新失败: {e}")

    # Phase 4: 补充影子结果（如在验证期）
    if is_verifying():
        try:
            fill_shadow_results()
            logger.info("[Evolution/Orchestrator] Phase 4: 影子收益已补充")
        except Exception as e:
            logger.error(f"[Evolution/Orchestrator] Phase 4 影子补充失败: {e}")

    return True


# ============================================================================
# 月末触发（Phase 3 学习期报告 / Phase 5 验证期报告）
# ============================================================================

def on_month_end() -> str:
    """
    月末（每月28-31日）自动调用
    根据当前阶段判断：
    - learning_pending_confirm → 生成学习期报告（Phase 3）
    - verifying → 生成验证期报告（Phase 5）
    - learning → 先检查数据量，够则生成建议和报告，否则跳过
    """
    from evolution.monthly_report import build_learning_report, build_verification_report, send_report
    from evolution.weight_manager import generate_weight_suggestion
    from evolution.stats_analyzer import get_stats

    state = _load_state()
    phase = state.get("current_phase", "learning")
    current_month = datetime.now().strftime("%Y-%m")

    logger.info(f"[Evolution/Orchestrator] 月末触发，当前阶段: {phase}")

    if phase == "verifying":
        report = build_verification_report()
        send_report(report)
        state["last_report_month"] = current_month
        _save_state(state)
        return report

    if phase == "learning_pending_confirm":
        report = build_learning_report()
        send_report(report)
        state["last_report_month"] = current_month
        _save_state(state)
        return report

    # learning: 先检查数据量
    stats = get_stats()
    total = stats.get("total_records", 0) if stats else 0

    # 避免同月重复触发
    last_report = state.get("last_report_month")
    if last_report == current_month:
        logger.info(f"[Evolution/Orchestrator] 本月({current_month})已发过报告，跳过")
        return f"skipped: report already sent this month"

    if total < 20:
        logger.info(f"[Evolution/Orchestrator] 数据不足（{total}条），月末报告跳过")
        return f"skipped: insufficient data ({total}/20)"

    suggestion = generate_weight_suggestion()
    if suggestion.get("skipped"):
        logger.info(f"[Evolution/Orchestrator] 权重建议已跳过: {suggestion.get('reason')}")
        return f"skipped: {suggestion.get('reason')}"

    report = build_learning_report()
    send_report(report)

    # 标记本月已报告
    state["last_report_month"] = current_month
    _save_state(state)
    return report


# ============================================================================
# 海赟确认后处理（收到确认指令时调用）
# ============================================================================

def on_user_confirm(confirmed: bool, version: str = None) -> str:
    """
    海赟回复确认/拒绝时调用

    用法（由主 session 收到飞书回复时调用）：
      on_user_feishu_reply("确认W2")  → 确认并进入验证期
      on_user_feishu_reply("拒绝")      → 拒绝建议
    """
    from evolution.weight_manager import confirm_and_apply_suggestion, reject_suggestion, get_pending_suggestion

    suggestion = get_pending_suggestion()
    if not suggestion:
        return "❌ 无待确认的权重建议"

    ver = version or suggestion.get("version", "W2")

    if confirmed:
        ok = confirm_and_apply_suggestion()
        if ok:
            return f"✅ {ver} 已确认，信号灯进入验证期（影子追踪1个月）"
        return "❌ 确认失败，无待确认建议"
    else:
        reject_suggestion()
        return "❌ 已拒绝新权重建议，保持当前权重，当前周期继续学习"


def on_user_feishu_reply(text: str) -> str:
    """
    处理飞书用户回复
    由主 session 收到消息时调用

    支持的格式：
      确认W2 / 确认 / 采纳
      拒绝 / 放弃
    """
    text = text.strip()

    if "确认" in text or "采纳" in text or "同意" in text:
        # 提取版本号
        version = None
        for w in ["W2", "W3", "W4", "W5"]:
            if w in text:
                version = w
                break
        return on_user_confirm(True, version)

    if "拒绝" in text or "放弃" in text or "不同意" in text:
        return on_user_confirm(False)

    return "⚠️ 无法识别回复，请回复「确认W2」或「拒绝」"
