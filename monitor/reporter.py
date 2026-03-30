# -*- coding: utf-8 -*-
"""
交易复盘报告
每日/每周/每月生成交易复盘报告并推送

复盘内容：
1. 今日交易汇总（买卖点、盈亏）
2. 信号有效性统计（本次信号后续走势）
3. 失败交易归因分析
4. 明日关注股票
5. AI 综合建议
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from config import get_config
from models.trade import TradeStore, TradeRecord
from models.position import PositionStore
from notification.feishu import get_feishu_notifier
from notification.llm_analyzer import get_llm_analyzer
from data_provider.data_selector import get_selector

logger = logging.getLogger(__name__)


# ============================================================================
# 复盘报告生成器
# ============================================================================

class Reporter:
    """
    交易复盘报告生成器

    用法：
        reporter = Reporter()
        reporter.send_daily_report()    # 每日收盘后
        reporter.send_weekly_report()  # 每周
        reporter.send_monthly_report()  # 每月
    """

    def __init__(self):
        self.config = get_config()
        self.feishu = get_feishu_notifier()
        self.llm = get_llm_analyzer()
        self.selector = get_selector()

    # ------------------------------------------------------------------------
    # 每日复盘
    # ------------------------------------------------------------------------

    def send_daily_report(self, trade_date: str = None) -> bool:
        """
        发送每日复盘报告

        Args:
            trade_date: 复盘日期，默认今日

        Returns:
            是否发送成功
        """
        if not self.feishu.enabled:
            logger.debug("飞书未配置，跳过复盘")
            return False

        date = trade_date or datetime.now().strftime("%Y-%m-%d")
        trade_store = TradeStore()
        position_store = PositionStore()

        # 获取当日交易
        all_trades = trade_store.load()
        today_trades = [t for t in all_trades if t.trade_date == date]
        today_buys = [t for t in today_trades if t.action == "BUY"]
        today_sells = [t for t in today_trades if t.action not in ("BUY",)]

        # 获取当前持仓
        positions = position_store.get_open_positions()

        # 获取当日大盘状态
        from strategy.market_filter import get_market_filter
        mf = get_market_filter()
        status, worst_change = mf.get_market_status()
        indices = mf.get_multi_index_status()

        index_lines = []
        for code, data in indices.items():
            emoji = "🟢" if data["change_pct"] > 0 else "🔴" if data["change_pct"] < 0 else "🟡"
            index_lines.append(f"{emoji} {data['name']}: {data['change_pct']:+.2f}%")

        # ---- 构建报告内容 ----
        lines = [
            f"📊 **每日交易复盘** `{date}`",
            "",
            "---",
            "**📈 大盘状态**",
            f"综合判断: {status.value}",
            *index_lines,
            "",
        ]

        # ---- 今日交易 ----
        lines.append("---")
        lines.append("**📋 今日交易**")

        if not today_trades:
            lines.append("今日无交易")
        else:
            for t in today_trades:
                emoji = "🟢" if t.action == "BUY" else "🔴" if t.action in ("SELL", "STOP_LOSS") else "🎯"
                pnl_str = f"盈亏: ¥{t.pnl:+,.0f}" if t.pnl else ""
                lines.append(
                    f"{emoji} **{t.name}** `{t.action}`\n"
                    f"   价: ¥{t.price:.2f} × {t.quantity}手 | {pnl_str}\n"
                    f"   买信号{t.buy_signals} | 卖信号{t.sell_signals}\n"
                    f"   原因: {t.reason or '信号触发'}"
                )
        lines.append("")

        # ---- 持仓状态 ----
        lines.append("---")
        lines.append(f"**💼 当前持仓**（{len(positions)}只）")

        if not positions:
            lines.append("暂无持仓")
        else:
            for p in positions:
                pnl_emoji = "🟢" if p.pnl_pct > 0 else "🔴"
                hold_days = (datetime.now() - datetime.strptime(p.buy_date, "%Y-%m-%d")).days
                lines.append(
                    f"{pnl_emoji} **{p.name}**({p.code})\n"
                    f"   成本 ¥{p.buy_price:.2f} → 现价 ¥{p.current_price:.2f} "
                    f"({p.pnl_pct:+.2f}% / {hold_days}天)\n"
                    f"   买{p.latest_buy_signals}/卖{p.latest_sell_signals} | "
                    f"止损¥{p.stop_loss:.2f}"
                )
        lines.append("")

        # ---- 信号有效性统计 ----
        signal_stats = self._calc_signal_effectiveness(all_trades, lookback_days=5)
        if signal_stats:
            lines.append("---")
            lines.append("**📈 近5日信号有效性**")
            for name, stat in signal_stats.items():
                win_rate = stat["win_rate"]
                emoji = "🟢" if win_rate > 60 else "🟡" if win_rate > 40 else "🔴"
                lines.append(
                    f"{emoji} {name}: 胜率{win_rate:.0f}%, "
                    f"平均盈利¥{stat['avg_pnl']:+,.0f}, "
                    f"共交易{stat['count']}次"
                )
            lines.append("")

        # ---- AI 建议（异步，不等待）----
        report_text = "\n".join(lines)

        def _send_with_ai():
            try:
                if self.llm.is_available:
                    prompt = self._build_ai_prompt(date, today_trades, positions, signal_stats or {})
                    analysis = self.llm.analyze_stock({}, {}) or ""
                    if analysis:
                        lines.append("---")
                        lines.append("🤖 **AI 复盘建议**")
                        lines.append(analysis)
            except Exception as e:
                logger.debug(f"[Reporter] AI复盘生成失败: {e}")

            try:
                final_text = "\n".join(lines) + "\n\n_仅供参考，不构成投资建议_"
                self.feishu.send(final_text, msg_type="markdown")
                logger.info(f"[Reporter] 每日复盘已发送: {date}")
            except Exception as e:
                logger.error(f"[Reporter] 发送失败: {e}")

        import threading
        threading.Thread(target=_send_with_ai, daemon=True).start()
        return True

    # ------------------------------------------------------------------------
    # 每周复盘
    # ------------------------------------------------------------------------

    def send_weekly_report(self) -> bool:
        """发送每周复盘报告"""
        if not self.feishu.enabled:
            return False

        today = datetime.now()
        week_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")

        trade_store = TradeStore()
        all_trades = trade_store.load()
        week_trades = [t for t in all_trades if t.trade_date >= week_ago]

        closed_trades = [t for t in week_trades if t.pnl != 0]
        winning = [t for t in closed_trades if t.pnl > 0]
        losing = [t for t in closed_trades if t.pnl <= 0]

        total_pnl = sum(t.pnl for t in closed_trades)
        win_rate = len(winning) / len(closed_trades) * 100 if closed_trades else 0

        lines = [
            f"📊 **每周交易复盘** `{week_ago} ~ {today.strftime('%Y-%m-%d')}`",
            "",
            "---",
            f"**📈 本周概况**",
            f"总交易: {len(closed_trades)}笔（盈{len(winning)}/亏{len(losing)}）",
            f"胜率: {win_rate:.1f}%",
            f"总盈亏: ¥{total_pnl:+,.0f}",
        ]

        if closed_trades:
            lines.append("")
            lines.append("**📋 交易明细**")
            for t in closed_trades:
                emoji = "🟢" if t.pnl > 0 else "🔴"
                lines.append(
                    f"{emoji} {t.name} `{t.action}` ¥{t.price:.2f} "
                    f"qty={t.quantity} pnl=¥{t.pnl:+,.0f}"
                )

        lines.append("")
        lines.append("_仅供参考，不构成投资建议_")

        try:
            self.feishu.send("\n".join(lines), msg_type="markdown")
            logger.info("[Reporter] 每周复盘已发送")
            return True
        except Exception as e:
            logger.error(f"[Reporter] 每周复盘发送失败: {e}")
            return False

    # ------------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------------

    def _calc_signal_effectiveness(self, trades: List[TradeRecord], lookback_days: int = 5) -> dict:
        """
        计算近N日各信号的胜率

        Returns:
            {信号名: {win_rate, avg_pnl, count}}
        """
        cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        recent = [t for t in trades if t.trade_date >= cutoff and t.pnl != 0]

        if not recent:
            return {}

        # 统计买入信号的胜率
        stats = {}
        for t in recent:
            key = f"买{t.buy_signals}"
            if key not in stats:
                stats[key] = {"wins": 0, "total": 0, "pnl_sum": 0}
            stats[key]["total"] += 1
            stats[key]["pnl_sum"] += t.pnl
            if t.pnl > 0:
                stats[key]["wins"] += 1

        result = {}
        for key, s in stats.items():
            result[key] = {
                "win_rate": s["wins"] / s["total"] * 100,
                "avg_pnl": s["pnl_sum"] / s["total"],
                "count": s["total"],
            }
        return result

    def _build_ai_prompt(self, date: str, trades: list, positions: list, signal_stats: dict) -> str:
        """构建AI复盘Prompt"""
        trade_lines = []
        for t in trades:
            trade_lines.append(
                f"- {t.name}({t.action}): ¥{t.price:.2f}, "
                f"买信号{t.buy_signals}, 盈亏¥{t.pnl:+,.0f}"
            )

        pos_lines = []
        for p in positions:
            pos_lines.append(
                f"- {p.name}: 成本¥{p.buy_price:.2f}→现价¥{p.current_price:.2f}"
                f"({p.pnl_pct:+.2f}%), 买{p.latest_buy_signals}/卖{p.latest_sell_signals}"
            )

        signal_lines = []
        for name, stat in signal_stats.items():
            signal_lines.append(f"- {name}: 胜率{stat['win_rate']:.0f}%, 均盈亏¥{stat['avg_pnl']:+,.0f}")

        prompt = f"""请对以下每日交易复盘提供简短点评和建议：

日期: {date}

今日交易:
{chr(10).join(trade_lines) if trade_lines else "无交易"}

当前持仓:
{chr(10).join(pos_lines) if pos_lines else "无持仓"}

信号有效性（近5日）:
{chr(10).join(signal_lines) if signal_lines else "数据不足"}

请从以下角度点评：
1. 今日操作是否合理
2. 持仓风险评估
3. 明日操作建议
保持客观简洁，不超过100字。
"""
        return prompt


# ============================================================================
# 全局单例
# ============================================================================

_reporter: Reporter = None


def get_reporter() -> Reporter:
    global _reporter
    if _reporter is None:
        _reporter = Reporter()
    return _reporter
