# -*- coding: utf-8 -*-
"""
飞书通知模块 v2
支持两种推送方式：
1. OpenClaw Gateway API（优先，用于直接发消息给用户）
2. 群体机器人Webhook（备用）
"""

import logging
import json
import os
import requests
import subprocess
from datetime import datetime
from config import get_config

logger = logging.getLogger(__name__)

# OpenClaw Gateway 直发消息的配置文件
PENDING_MSG_FILE = "data/pending_messages.json"


class FeishuNotifier:
    """飞书通知推送"""

    def __init__(self, webhook_url: str = ""):
        config = get_config()
        self.webhook_url = webhook_url or config.feishu_webhook_url
        self.enabled = True  # 始终启用，fallback到日志
        self.user_id = "ou_ee2947ff311d4978679c2a2d4433f62a"  # 默认发送目标

    def send(self, content: str, msg_type: str = "text") -> bool:
        """
        发送飞书消息（优先用OpenClaw直发，失败用Webhook）

        Args:
            content: 消息内容
            msg_type: text 或 markdown
        """
        # 优先方案：通过OpenClaw Gateway API 发送
        if self._send_via_openclaw(content, msg_type):
            return True

        # 备用方案：Webhook
        if self.webhook_url:
            return self._send_via_webhook(content, msg_type)

        # 最终fallback：写入文件
        logger.warning("飞书未配置，写入pending文件")
        return self._save_to_file(content, msg_type)

    def _send_via_openclaw(self, content: str, msg_type: str = "text") -> bool:
        """通过OpenClaw Gateway HTTP API发送消息（直接发给用户）"""
        try:
            import requests
            resp = requests.post(
                "http://localhost:19277/api/message/send",
                json={
                    "channel": "feishu",
                    "target": self.user_id,
                    "message": content,
                },
                timeout=30,
            )
            if resp.status_code == 200:
                logger.info(f"[FeishuNotifier] Gateway API发送成功")
                return True
            else:
                logger.warning(f"[FeishuNotifier] Gateway API返回 {resp.status_code}: {resp.text[:100]}")
                return False
        except ImportError:
            logger.warning("requests库不可用，尝试subprocess")
            return self._send_via_subprocess(content, msg_type)
        except Exception as e:
            logger.warning(f"[FeishuNotifier] Gateway API发送失败: {e}")
            return False

    def _send_via_subprocess(self, content: str, msg_type: str = "text") -> bool:
        """通过subprocess调用openclaw CLI发送（备用）"""
        try:
            import subprocess
            result = subprocess.run(
                ["openclaw", "message", "send",
                 "--channel", "feishu",
                 "--target", self.user_id,
                 "--message", content],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                logger.info(f"[FeishuNotifier] CLI发送成功")
                return True
            else:
                logger.warning(f"[FeishuNotifier] CLI发送失败: {result.stderr[:100]}")
                return False
        except Exception as e:
            logger.warning(f"[FeishuNotifier] CLI发送异常: {e}")
            return False

    def _send_via_webhook(self, content: str, msg_type: str = "text") -> bool:
        """通过群体机器人Webhook发送"""
        if not self.webhook_url:
            return False

        try:
            headers = {"Content-Type": "application/json"}

            if msg_type == "markdown":
                # 飞书markdown格式
                payload = {
                    "msg_type": "markdown",
                    "content": {
                        "text": content
                    }
                }
            else:
                payload = {
                    "msg_type": "text",
                    "content": {"text": content}
                }

            resp = requests.post(self.webhook_url, json=payload, headers=headers, timeout=10)
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") == 0 or result.get("StatusCode") == 0:
                logger.info("飞书Webhook消息发送成功")
                return True
            else:
                logger.error(f"飞书Webhook发送失败: {result}")
                return False

        except Exception as e:
            logger.error(f"飞书Webhook异常: {e}")
            return False

    def _save_to_file(self, content: str, msg_type: str = "text") -> bool:
        """保存到待发消息文件"""
        try:
            os.makedirs(os.path.dirname(PENDING_MSG_FILE) or ".", exist_ok=True)
            pending = []
            if os.path.exists(PENDING_MSG_FILE):
                with open(PENDING_MSG_FILE, "r") as f:
                    pending = json.load(f)

            pending.append({
                "content": content,
                "msg_type": msg_type,
                "target": self.user_id,
                "created_at": datetime.now().isoformat(),
            })

            with open(PENDING_MSG_FILE, "w") as f:
                json.dump(pending, f, ensure_ascii=False, indent=2)

            return True
        except Exception as e:
            logger.error(f"保存待发消息失败: {e}")
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
            if hasattr(s, 'buy_signals_detail') and s.buy_signals_detail:
                detail = "、".join([f"✅{x}" for x in s.buy_signals_detail[:3]])
                lines.append(f"   买点: {detail}")
            if hasattr(s, 'sell_signals_detail') and s.sell_signals_detail:
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
        """发送交易通知（买入/卖出/止损）"""
        action_emoji = {
            "BUY": "🟢买入",
            "SELL": "🔴卖出",
            "STOP_LOSS": "🚨止损",
            "TAKE_PROFIT": "🎯止盈",
        }.get(trade.get("action", ""), "📋操作")

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
        return datetime.now().strftime("%Y-%m-%d %H:%M")


# 全局飞书通知器
_feishu_notifier: FeishuNotifier = None


def get_feishu_notifier() -> FeishuNotifier:
    global _feishu_notifier
    if _feishu_notifier is None:
        _feishu_notifier = FeishuNotifier()
    return _feishu_notifier
