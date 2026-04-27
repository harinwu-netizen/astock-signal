# -*- coding: utf-8 -*-
"""
Phase 5: 月度报告生成器

月末自动触发：
1. 生成学习期报告（含 AI 初审）→ 推送飞书 → 等待确认
2. 生成验证期报告（含 AI 复审）→ 推送飞书 → 等待决定
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).parent.parent
STATS_FILE = _BASE_DIR / "evolution" / "signal_stats.json"
CYCLE_STATE_FILE = _BASE_DIR / "evolution" / "cycle_state.json"
DECISION_LOG = _BASE_DIR / "evolution" / "decision_log.csv"


def _load_cycle_state() -> Dict:
    with open(CYCLE_STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_recent_log(days: int = 35) -> list:
    """读取近N天日志"""
    if not os.path.exists(DECISION_LOG):
        return []
    import csv
    cutoff = (datetime.now() - __import__('datetime').timedelta(days=days)).strftime("%Y-%m-%d")
    rows = []
    with open(DECISION_LOG, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("trade_date", "") >= cutoff:
                rows.append(row)
    return rows


def _ai_review_learning(stats: Dict, suggestion: Dict) -> str:
    """
    AI初审（Phase 3）：评估权重调整建议是否合理
    使用 LLM 分析
    """
    try:
        from notification.llm_analyzer import get_llm_analyzer
        llm = get_llm_analyzer()
        if not llm.is_available:
            return "⚠️ AI分析服务不可用，跳过AI初审"

        changes = suggestion.get("changes", [])
        total_records = stats.get("total_records", 0)

        prompt = f"""你是A股量化交易系统的AI审核员。请审查以下权重调整建议：

数据基础：共{total_records}条历史决策记录

建议调整内容：
{chr(10).join([f"- {c['signal']}: {c['old']} → {c['new']}（{c['reason']}）" for c in changes]) if changes else "无显著调整"}

请从以下角度审查：
1. 数据可信度：样本是否足够？有无非理性极端值？
2. 调整逻辑：上调/下调是否与历史表现一致？
3. 过拟合风险：是否可能只是短期规律？
4. 具体建议：是否应该进入验证期？如需修改，指出哪项应调整

回答要求：中文，简洁，不超过200字，突出风险点。"""

        result = llm.analyze_text(prompt)
        if result:
            return f"🤖 **AI初审意见**\n{result}"
        return ""

    except Exception as e:
        logger.debug(f"[Evolution/AI] 初审调用失败: {e}")
        return ""


def _ai_review_verification(shadow_summary: Dict, real_summary: Dict, cycle_state: Dict) -> str:
    """
    AI复审（Phase 5）：分析验证结果是否显著
    """
    try:
        from notification.llm_analyzer import get_llm_analyzer
        llm = get_llm_analyzer()
        if not llm.is_available:
            return "⚠️ AI分析服务不可用，跳过AI复审"

        w_new = shadow_summary
        w_old = real_summary

        prompt = f"""你是A股量化交易系统的AI审核员。请审查以下验证结果：

验证权重：{cycle_state.get('current_weight_version', '?')}
验证期：{cycle_state.get('verification_start', '?')} ~ {datetime.now().strftime('%Y-%m-%d')}

实盘（旧权重W_old）：
- 影子交易次数：{w_new.get('total_shadow_trades', 0)}

影子交易（新权重{cycle_state.get('current_weight_version', '?')}）：
- 影子交易次数：{w_new.get('total_shadow_trades', 0)}
- 已补充收益数据：{w_new.get('filled', 0)}条
- 5日平均涨跌：{w_new.get('avg_change_5d', 0):.2f}%
- 5日胜率：{w_new.get('win_rate_5d', 0):.0%}
- 10日平均涨跌：{w_new.get('avg_change_10d', 0):.2f}%

请分析：
1. 统计显著性：新权重是否显著跑赢旧权重？（考虑样本量）
2. 市场背景：验证期间大盘环境如何？是否影响结论可靠性？
3. 风险提示：是否可能只是短期因素导致？
4. 最终建议：采纳 / 放弃 / 继续观察？

回答要求：中文，简洁，不超过200字，给出明确建议。"""

        result = llm.analyze_text(prompt)
        if result:
            return f"🤖 **AI复审意见**\n{result}"
        return ""

    except Exception as e:
        logger.debug(f"[Evolution/AI] 复审调用失败: {e}")
        return ""


def build_learning_report() -> str:
    """
    生成学习期报告（Month 1 末）
    包含：数据统计 + 信号有效性 + 权重调整建议 + AI初审
    """
    from evolution.weight_manager import get_pending_suggestion, _load_cycle_state, _load_weights

    state = _load_cycle_state()
    suggestion = get_pending_suggestion()
    current = _load_weights()

    if not suggestion:
        return "⚠️ 无待确认的权重建议"

    stats_file = _BASE_DIR / "evolution" / "signal_stats.json"
    stats = {}
    if os.path.exists(stats_file):
        with open(stats_file, "r", encoding="utf-8") as f:
            stats = json.load(f)

    version = suggestion["version"]
    changes = suggestion.get("changes", [])
    total_records = suggestion.get("based_on_data_records", 0)

    # AI初审
    ai_review = _ai_review_learning(stats, suggestion)

    lines = [
        f"📊 **信号灯自学习报告**",
        f"🕐 {datetime.now().strftime('%Y-%m-%d')} · {version} 权重建议",
        "",
        "---",
        "**📈 数据统计**",
        f"- 历史记录：{total_records} 条",
        f"- 当前周期：第 {state.get('current_cycle', 1)} 轮学习期",
        "",
        "**🔍 信号有效性（样本≥20）**",
    ]

    # 添加有效信号
    signal_types = stats.get("signal_types", {})
    effective = {k: v for k, v in signal_types.items() if v.get("effective")}
    if effective:
        for name, data in effective.items():
            rate = data.get("win_rate_5d", 0)
            emoji = "🟢" if rate > 0.6 else "🟡" if rate > 0.4 else "🔴"
            lines.append(
                f"{emoji} {name}: 强势信号占比 {rate:.0%}（{data.get('count',0)}次）"
            )
    else:
        lines.append("  样本数据不足，无有效信号统计")

    lines.extend(["", "**⚙️ 权重调整建议**", f"- 版本：{version}"])

    if changes:
        for c in changes:
            arrow = "📈" if c["new"] > c["old"] else "📉"
            lines.append(
                f"{arrow} {c['signal']}: {c['old']} → {c['new']}（{c['reason']}）"
            )
    else:
        lines.append("  各信号表现无显著差异，建议保持当前权重")

    if ai_review:
        lines.extend(["", ai_review])

    lines.extend([
        "",
        "---",
        f"**⏳ 下一步**：请确认是否进入验证期",
        f"- ✅ 【确认】进入验证期（影子追踪1个月）",
        f"- ❌ 【拒绝】保持当前权重{current.get('version', 'W1')}",
        "",
        "_回复「确认+版本号」或「拒绝」_",
    ])

    return "\n".join(lines)


def build_verification_report() -> str:
    """
    生成验证期报告（Month 2 末）
    包含：双线对比 + AI复审 + 采纳/放弃建议
    """
    from evolution.shadow_tracker import get_shadow_summary, get_real_summary
    from evolution.weight_manager import _load_cycle_state

    state = _load_cycle_state()
    shadow = get_shadow_summary()
    real = get_real_summary()
    version = state.get("current_weight_version", "W1")

    # AI复审
    ai_review = _ai_review_verification(shadow, real, state)

    lines = [
        f"📊 **信号灯验证期报告**",
        f"🕐 {datetime.now().strftime('%Y-%m-%d')} · 验证 {version} 结果",
        "",
        "---",
        f"**📈 验证结果对比**",
        f"- 验证期：{state.get('verification_start', '?')} ~ {datetime.now().strftime('%Y-%m-%d')}",
        "",
        f"**📊 影子交易（新{version}）**",
        f"- 影子交易次数：{shadow.get('total_shadow_trades', 0)}",
        f"- 已补充收益：{shadow.get('filled', 0)}条",
        f"- 5日平均涨跌：{shadow.get('avg_change_5d', 0):+.2f}%",
        f"- 5日胜率：{shadow.get('win_rate_5d', 0):.0%}",
        f"- 10日平均涨跌：{shadow.get('avg_change_10d', 0):+.2f}%",
        "",
        f"**📊 实盘交易（旧权重）**",
        f"- 实际交易次数：{real.get('total_real_trades', '待统计')}",
    ]

    if ai_review:
        lines.extend(["", ai_review])

    # 简单判断
    w2_return = shadow.get("avg_change_5d", 0)
    if w2_return > 1.0:
        verdict = "✅ 建议采纳新权重"
    elif w2_return < -0.5:
        verdict = "❌ 建议放弃新权重"
    else:
        verdict = "⚠️ 建议继续观察1个月"

    lines.extend([
        "",
        "---",
        f"**{verdict}**",
        "",
        f"- ✅ 【采纳】正式启用{version}，开始新一轮学习",
        f"- ❌ 【放弃】保持当前权重，开始新一轮学习",
        "",
        "_回复「采纳」或「放弃」_",
    ])

    return "\n".join(lines)


def send_report(report_text: str) -> bool:
    """推送报告到飞书"""
    try:
        from notification.feishu import get_feishu_notifier
        feishu = get_feishu_notifier()
        if feishu.enabled:
            feishu.send(report_text, msg_type="markdown")
            logger.info("[Evolution/Report] 月度报告已发送")
            return True
    except Exception as e:
        logger.error(f"[Evolution/Report] 发送失败: {e}")
    return False
