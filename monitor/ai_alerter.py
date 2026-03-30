# -*- coding: utf-8 -*-
"""
AI 增强预警系统
在关键事件触发时调用大模型进行深度分析，并通过飞书/企微推送

触发条件：
1. 持仓亏损 > 8%  → 紧急止损预警 + AI分析
2. 信号多转空      → 持仓个股信号恶化预警 + AI分析
3. 大盘跳水        → 多指数联合判断，跌幅超阈值时推送
4. 每日复盘        → 收盘后汇总持仓状态 + AI建议
"""

import logging
import threading
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional

from config import get_config
from models.position import Position, PositionStore
from models.signal import RealTimeSignal, MarketStatus
from strategy.market_filter import get_market_filter
from notification.feishu import get_feishu_notifier
from notification.llm_analyzer import get_llm_analyzer

logger = logging.getLogger(__name__)

# ============================================================================
# 预警阈值（可从 .env 覆盖）
# ============================================================================

AI_LOSS_THRESHOLD = 8.0       # 亏损超此值触发紧急预警（%）
AI_ALERT_ENABLED = True         # AI预警总开关
DEDUP_INTERVAL = 300           # 同类预警去重间隔（秒）


# ============================================================================
# 预警数据结构
# ============================================================================

@dataclass
class AIAlert:
    """AI预警消息"""
    level: str           # INFO / WARNING / DANGER
    title: str
    body: str           # 基础信息（不含AI分析）
    ai_analysis: str = ""  # AI深度分析
    positions: List[str] = field(default_factory=list)  # 相关股票代码
    ts: datetime = field(default_factory=datetime.now)


# ============================================================================
# 预警器
# ============================================================================

class AIAlerter:
    """
    AI增强预警器

    用法：
        alerter = AIAlerter()
        alerter.check_and_alert(signals=[...])   # 每日扫描时调用
        alerter.send_market_crash_alert()        # 大盘异动时调用
        alerter.send_daily_summary()              # 收盘复盘
    """

    def __init__(self):
        self.config = get_config()
        self.feishu = get_feishu_notifier()
        self.llm = get_llm_analyzer()
        self.market_filter = get_market_filter()
        self._dedup_cache: dict = {}  # key → last_alert_time

    # ------------------------------------------------------------------------
    # 核心检查入口
    # ------------------------------------------------------------------------

    def check_and_alert(self, signals: List[RealTimeSignal]) -> List[AIAlert]:
        """
        检查所有持仓并发送预警

        Args:
            signals: 当前所有股票的 RealTimeSignal 列表

        Returns:
            产生的预警列表
        """
        if not AI_ALERT_ENABLED:
            return []

        position_store = PositionStore()
        positions = position_store.get_open_positions()
        signal_map = {s.code: s for s in signals}

        alerts: List[AIAlert] = []

        for pos in positions:
            sig = signal_map.get(pos.code)
            if not sig:
                continue

            # 1. 亏损超限检查
            alert = self._check_loss_alert(pos, sig)
            if alert:
                alerts.append(alert)

            # 2. 信号多转空检查
            alert = self._check_signal_reverse(pos, sig)
            if alert:
                alerts.append(alert)

        # 发送所有预警
        for alert in alerts:
            self._send_alert(alert)

        return alerts

    def send_market_crash_alert(self) -> Optional[AIAlert]:
        """
        检查大盘是否触发暴跌预警并发送

        Returns:
            产生的预警，无则返回 None
        """
        if not AI_ALERT_ENABLED:
            return None

        status, worst_change = self.market_filter.get_market_status()
        indices = self.market_filter.get_multi_index_status()

        crash_threshold = self.config.market_crash_threshold  # 默认 -2.0

        if worst_change < crash_threshold:
            # 触发大盘暴跌预警
            index_details = []
            for code, data in indices.items():
                index_details.append(
                    f"- {data['name']}: {data['price']:.2f} ({data['change_pct']:+.2f}%)"
                )

            body = (
                f"⚠️ **大盘暴跌预警**\n\n"
                f"多指数最大跌幅: **{worst_change:+.2f}%**\n"
                f"触发阈值: {crash_threshold}%\n\n"
                "指数详情:\n" + "\n".join(index_details) + "\n\n"
                f"建议：考虑减仓或止损，耐心等待市场企稳。"
            )

            alert = AIAlert(
                level="DANGER",
                title=f"🚨 大盘暴跌预警（{worst_change:+.2f}%）",
                body=body,
                positions=["大盘"],
            )

            # AI 深度分析（异步）
            self._async_ai_analysis(alert, prompt=self._build_market_prompt(indices, worst_change))
            self._send_alert(alert)
            return alert

        return None

    def send_daily_summary(self, signals: List[RealTimeSignal]) -> Optional[AIAlert]:
        """
        每日收盘复盘推送

        Args:
            signals: 当日所有监控股票的信号列表
        """
        if not AI_ALERT_ENABLED:
            return None

        if not self.feishu.enabled:
            return None

        position_store = PositionStore()
        positions = position_store.get_open_positions()
        signal_map = {s.code: s for s in signals}

        if not positions:
            return None

        # 构建持仓复盘
        position_lines = []
        for pos in positions:
            sig = signal_map.get(pos.code)
            if not sig:
                continue

            cost = pos.buy_price
            cur = sig.price
            pnl_pct = (cur - cost) / cost * 100
            emoji = "🟢" if pnl_pct > 0 else "🔴"

            position_lines.append(
                f"{emoji} **{pos.name}**({pos.code})\n"
                f"   成本 ¥{cost:.2f} → 现价 ¥{cur:.2f} "
                f"({pnl_pct:+.1f}%)\n"
                f"   买信号{sig.buy_count}/10 | 卖信号{sig.sell_count}/6\n"
                f"   止损 ¥{pos.stop_loss:.2f} | ATR ¥{pos.atr:.4f}"
            )

        body = (
            f"📋 **每日持仓复盘** `{datetime.now().strftime('%Y-%m-%d')}`\n\n"
            + "\n\n".join(position_lines)
            + "\n\n_数据仅供参考，不构成投资建议_"
        )

        alert = AIAlert(
            level="INFO",
            title="📋 每日持仓复盘",
            body=body,
            positions=[p.code for p in positions],
        )

        # AI 分析（异步）
        self._async_ai_analysis(
            alert,
            prompt=self._build_daily_summary_prompt(positions, signal_map)
        )
        self._send_alert(alert)
        return alert

    # ------------------------------------------------------------------------
    # 各类预警检查
    # ------------------------------------------------------------------------

    def _check_loss_alert(self, position: Position, signal: RealTimeSignal) -> Optional[AIAlert]:
        """持仓亏损超限预警"""
        cost = position.buy_price
        cur = signal.price
        if cost <= 0 or cur <= 0:
            return None

        loss_pct = (cost - cur) / cost * 100

        if loss_pct <= AI_LOSS_THRESHOLD:
            return None

        # 去重
        key = f"loss:{position.code}"
        if not self._should_alert(key):
            return None

        body = (
            f"🚨 **{position.name}（{position.code}）亏损预警**\n\n"
            f"持仓亏损: **{loss_pct:.1f}%**（阈值: {AI_LOSS_THRESHOLD}%)\n"
            f"买入价: ¥{cost:.2f}\n"
            f"当前价: ¥{cur:.2f}\n"
            f"买入日期: {position.buy_date}\n"
            f"持仓天数: {(datetime.now() - datetime.strptime(position.buy_date, '%Y-%m-%d')).days}天\n"
            f"ATR止损: ¥{position.stop_loss:.2f}（当前价距止损 {(cur - position.stop_loss) / cur * 100:.1f}%)\n\n"
            f"建议：立即关注，考虑止损。"
        )

        alert = AIAlert(
            level="DANGER",
            title=f"🚨 {position.name} 亏损{loss_pct:.1f}%！",
            body=body,
            positions=[position.code],
        )

        self._async_ai_analysis(
            alert,
            prompt=self._build_loss_prompt(position, signal)
        )

        return alert

    def _check_signal_reverse(self, position: Position, signal: RealTimeSignal) -> Optional[AIAlert]:
        """信号多转空预警"""
        prev_buy = position.latest_buy_signals
        prev_sell = position.latest_sell_signals
        curr_buy = signal.buy_count
        curr_sell = signal.sell_count

        # 信号明显恶化：买入信号减少超过2，或卖出信号增加超过2
        buy_deteriorated = (prev_buy - curr_buy) >= 2
        sell_increased = (curr_sell - prev_sell) >= 2

        if not (buy_deteriorated or sell_increased):
            return None

        # 去重
        key = f"reverse:{position.code}"
        if not self._should_alert(key):
            return None

        body = (
            f"📉 **{position.name}（{position.code}）信号恶化**\n\n"
            f"买信号: {prev_buy} → {curr_buy} {'⬇️' if buy_deteriorated else '➡️'}\n"
            f"卖信号: {prev_sell} → {curr_sell} {'⬆️' if sell_increased else '➡️'}\n"
            f"当前决策: {signal.decision.value}\n"
            f"现价: ¥{signal.price:.2f}\n"
            f"ATR止损: ¥{position.stop_loss:.2f}\n\n"
            f"建议：密切关注，及时止损。"
        )

        alert = AIAlert(
            level="WARNING",
            title=f"📉 {position.name} 信号转弱",
            body=body,
            positions=[position.code],
        )

        self._async_ai_analysis(
            alert,
            prompt=self._build_signal_reverse_prompt(position, signal)
        )

        return alert

    # ------------------------------------------------------------------------
    # AI 分析（异步，不阻塞）
    # ------------------------------------------------------------------------

    def _async_ai_analysis(self, alert: AIAlert, prompt: str):
        """异步触发 AI 分析，结果追加到预警"""
        if not self.llm.is_available:
            return

        def _run():
            try:
                analysis = self.llm.analyze_stock(
                    signal={"code": alert.positions[0] if alert.positions else "", "name": alert.positions[0], "price": 0},
                    market=None,
                )
                if analysis:
                    alert.ai_analysis = analysis
                    # AI分析完成后，重新发送带AI分析的完整预警
                    self._send_alert_with_ai(alert)
            except Exception as e:
                logger.debug(f"[AIAlerter] AI分析失败: {e}")

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    def _send_alert_with_ai(self, alert: AIAlert):
        """发送带 AI 分析的完整预警"""
        if not self.feishu.enabled:
            return

        full_body = alert.body
        if alert.ai_analysis:
            full_body += f"\n\n🤖 **AI分析:**\n{alert.ai_analysis}"

        full_body += "\n\n_仅供参考，不构成投资建议_"

        try:
            self.feishu.send(full_body, msg_type="markdown")
        except Exception as e:
            logger.error(f"[AIAlerter] 发送失败: {e}")

    # ------------------------------------------------------------------------
    # 发送预警
    # ------------------------------------------------------------------------

    def _send_alert(self, alert: AIAlert):
        """发送预警（不含AI分析部分）"""
        if not self.feishu.enabled:
            logger.info(f"[AIAlerter] 飞书未配置，仅记录: {alert.title}")
            return

        body = alert.body + "\n\n_仅供参考，不构成投资建议_"
        try:
            self.feishu.send(body, msg_type="markdown")
            logger.info(f"[AIAlerter] 预警已发送: {alert.title}")
        except Exception as e:
            logger.error(f"[AIAlerter] 发送失败: {e}")

    def _should_alert(self, key: str) -> bool:
        """去重检查"""
        now = datetime.now().timestamp()
        last = self._dedup_cache.get(key, 0)
        if now - last < DEDUP_INTERVAL:
            return False
        self._dedup_cache[key] = now
        return True

    # ------------------------------------------------------------------------
    # AI Prompt 构建
    # ------------------------------------------------------------------------

    def _build_loss_prompt(self, position: Position, signal: RealTimeSignal) -> str:
        return (
            f"持仓亏损严重，请紧急分析。\n"
            f"股票: {position.name}({position.code})\n"
            f"买入价: ¥{position.buy_price:.2f}, 当前价: ¥{signal.price:.2f}\n"
            f"亏损幅度: {(position.buy_price - signal.price) / position.buy_price * 100:.1f}%\n"
            f"ATR止损位: ¥{position.stop_loss:.2f}\n"
            f"买入信号{position.buy_signals}/10, 当前买信号{signal.buy_count}/10\n"
            f"请判断：1) 是否应该止损 2) 后市如何应对"
        )

    def _build_signal_reverse_prompt(self, position: Position, signal: RealTimeSignal) -> str:
        return (
            f"持仓信号恶化，请分析。\n"
            f"股票: {position.name}({position.code})\n"
            f"当前价: ¥{signal.price:.2f}\n"
            f"买信号: {position.latest_buy_signals}→{signal.buy_count}, "
            f"卖信号: {position.latest_sell_signals}→{signal.sell_count}\n"
            f"ATR止损: ¥{position.stop_loss:.2f}\n"
            f"请判断：1) 信号恶化是否意味着趋势反转 2) 如何操作"
        )

    def _build_market_prompt(self, indices: dict, worst_change: float) -> str:
        lines = []
        for code, data in indices.items():
            lines.append(f"{data['name']}: {data['price']:.2f}({data['change_pct']:+.2f}%)")
        return (
            f"大盘出现异常波动，请分析。\n"
            f"各指数涨跌:\n" + "\n".join(lines) + "\n"
            f"最大跌幅: {worst_change:+.2f}%\n"
            f"请判断：1) 当前市场环境 2) 持仓如何应对"
        )

    def _build_daily_summary_prompt(self, positions: List[Position], signal_map: dict) -> str:
        lines = []
        for pos in positions:
            sig = signal_map.get(pos.code)
            if not sig:
                continue
            pnl = (sig.price - pos.buy_price) / pos.buy_price * 100
            lines.append(
                f"{pos.name}: 成本¥{pos.buy_price:.2f}→现价¥{sig.price:.2f}({pnl:+.1f}%), "
                f"买{sig.buy_count}/卖{sig.sell_count}"
            )
        return "请对以下持仓做每日复盘分析：\n" + "\n".join(lines)


# ============================================================================
# 全局单例
# ============================================================================

_alerter: AIAlerter = None


def get_ai_alerter() -> AIAlerter:
    global _alerter
    if _alerter is None:
        _alerter = AIAlerter()
    return _alerter
