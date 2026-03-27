# -*- coding: utf-8 -*-
"""
飞书通知模块
"""

import logging
import json
import requests
from typing import Optional
from config import get_config

logger = logging.getLogger(__name__)


class FeishuNotifier:
    """飞书 Webhook 推送"""

    def __init__(self, webhook_url: str = ""):
        config = get_config()
        self.webhook_url = webhook_url or config.feishu_webhook_url
        self.enabled = bool(self.webhook_url)

    def send(self, content: str, msg_type: str = "text") -> bool:
        """
        发送飞书消息

        Args:
            content: 消息内容
            msg_type: text 或 interactive
        """
        if not self.enabled:
            logger.debug("飞书通知未配置，跳过")
            return False

        try:
            headers = {"Content-Type": "application/json"}

            if msg_type == "text":
                payload = {
                    "msg_type": "text",
                    "content": {"text": content}
                }
            else:
                payload = {
                    "msg_type": "markdown",
                    "content": content
                }

            resp = requests.post(self.webhook_url, json=payload, headers=headers, timeout=10)
            resp.raise_for_status()

            result = resp.json()
            if result.get("code") == 0 or result.get("StatusCode") == 0:
                logger.info("飞书消息发送成功")
                return True
            else:
                logger.error(f"飞书消息发送失败: {result}")
                return False

        except Exception as e:
            logger.error(f"飞书通知发送异常: {e}")
            return False

    def send_signal_report(self, signals: list) -> bool:
        """发送信号扫描报告"""
        if not signals:
            return False

        lines = [
            "📊 **A股信号灯 · 信号扫描报告**",
            "",
            f"🕐 {self._now()}",
            "",
            "---",
        ]

        for s in signals:
            emoji = s.get_decision_emoji()
            lines.append(f"{emoji} **{s.name} ({s.code})**")
            lines.append(f"   价格: ¥{s.price:.2f} ({s.change_pct:+.2f}%) | 信号: {s.buy_count}/10")
            if s.buy_signals_detail:
                detail = "、".join([f"✅{x}" for x in s.buy_signals_detail[:3]])
                lines.append(f"   买点: {detail}")
            if s.sell_signals_detail:
                detail = "、".join([f"⚠️{x}" for x in s.sell_signals_detail[:3]])
                lines.append(f"   卖点: {detail}")
            decision_desc = {
                "BUY": "🟢 买入",
                "HOLD": "🟡 持有",
                "SELL": "🔴 卖出",
                "WATCH": "⚪ 观望",
                "STOP_LOSS": "🚨 止损",
            }.get(s.decision.value, s.decision.value)
            lines.append(f"   决策: {decision_desc}")
            lines.append("")

        content = "\n".join(lines)
        return self.send(content, msg_type="markdown")

    def send_position_report(self, positions: list, portfolio: dict) -> bool:
        """发送持仓日报"""
        lines = [
            "📋 **持仓日报**",
            "",
            f"🕐 {self._now()}",
            "",
            f"总市值: ¥{portfolio.get('total_value', 0):,.0f} | "
            f"盈亏: {portfolio.get('total_pnl', 0):+,.0f} ({portfolio.get('total_pnl_pct', 0):+.2f}%)",
            "",
            "---",
        ]

        for p in positions:
            if p.status != "open":
                continue
            pnl_emoji = "🟢" if p.pnl_pct > 0 else "🔴"
            lines.append(f"{pnl_emoji} **{p.name} ({p.code})**")
            lines.append(f"   成本: ¥{p.buy_price:.2f} | 现价: ¥{p.current_price:.2f}")
            lines.append(f"   盈亏: {p.unrealized_pnl:+,.0f} ({p.pnl_pct:+.2f}%)")
            lines.append(f"   信号: 买{p.latest_buy_signals}/卖{p.latest_sell_signals} | "
                        f"止损: ¥{p.stop_loss:.2f}")
            lines.append("")

        content = "\n".join(lines)
        return self.send(content, msg_type="markdown")

    def send_trade_notification(self, trade: dict) -> bool:
        """发送交易通知"""
        action_emoji = {"BUY": "🟢买入", "SELL": "🔴卖出", "STOP_LOSS": "🚨止损",
                       "TAKE_PROFIT": "🎯止盈"}.get(trade.get("action", ""), "📋操作")

        lines = [
            f"🤖 **自动交易 {action_emoji}**",
            "",
            f"**{trade.get('name', '')} ({trade.get('code', '')})**",
            f"价格: ¥{trade.get('price', 0):.2f}",
            f"数量: {trade.get('quantity', 0)}手",
            f"金额: ¥{trade.get('amount', 0):,.0f}",
            "",
            f"信号: 买{trade.get('buy_signals', 0)}/卖{trade.get('sell_signals', 0)}",
            f"原因: {trade.get('reason', '')}",
        ]

        content = "\n".join(lines)
        return self.send(content, msg_type="markdown")

    def _now(self) -> str:
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M")


# 全局飞书通知器
_feishu_notifier: FeishuNotifier = None


def get_feishu_notifier() -> FeishuNotifier:
    global _feishu_notifier
    if _feishu_notifier is None:
        _feishu_notifier = FeishuNotifier()
    return _feishu_notifier
