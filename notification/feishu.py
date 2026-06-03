# -*- coding: utf-8 -*-
"""
飞书通知模块 v4 (C方案改造)
发送方式：单一路径
1. 写 outbox 文件 → 由 小海(OpenClaw) 在 heartbeat 时读取转发到飞书

设计原则：
- 单一发送路径，避免多通道重复/漏发
- 不依赖 subprocess CLI（已废弃）
- 不依赖 send_pending.py 轮询（已废弃）
- 失败时记录到 send_health.json，由下一轮扫描触发告警
"""

import logging
import json
import os
import uuid
from datetime import datetime
from config import get_config

logger = logging.getLogger(__name__)

OUTBOX_DIR = "data/outbox"
HEALTH_FILE = "data/send_health.json"
TARGET_USER_ID = "ou_ee2947ff311d4978679c2a2d4433f62a"


class FeishuNotifier:
    """飞书通知推送 (v4 - outbox 单一路径)"""

    def __init__(self):
        self.enabled = True
        self.user_id = TARGET_USER_ID
        os.makedirs(OUTBOX_DIR, exist_ok=True)

    def send(self, content: str, msg_type: str = "markdown") -> bool:
        """
        发送飞书消息 - 单一路径：写入 outbox

        Returns: True 表示成功写入 outbox（不代表已送达）
        """
        return self._write_to_outbox(content, msg_type, kind="text")

    def _write_to_outbox(self, content: str, msg_type: str, kind: str = "text") -> bool:
        """
        写入 outbox 文件，由 OpenClaw heartbeat 读取转发

        文件命名: outbox/{timestamp}_{uuid}.json
        """
        try:
            os.makedirs(OUTBOX_DIR, exist_ok=True)
            msg_id = uuid.uuid4().hex[:8]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{msg_id}.json"
            filepath = os.path.join(OUTBOX_DIR, filename)

            payload = {
                "id": msg_id,
                "content": content,
                "msg_type": msg_type,
                "target": self.user_id,
                "kind": kind,
                "created_at": datetime.now().isoformat(),
                "status": "pending",
            }

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            logger.info(f"[FeishuNotifier] 消息已写入 outbox: {filename} ({len(content)} chars)")
            self._record_health("outbox_written", filename, len(content))
            return True

        except Exception as e:
            logger.error(f"[FeishuNotifier] 写入 outbox 失败: {e}")
            self._record_health("outbox_failed", "", 0, str(e))
            self._write_alert_flag(str(e))
            return False

    def _write_alert_flag(self, error_msg: str):
        """写入告警标志文件,小海(OpenClaw)检测后会立即推送告警"""
        try:
            alert_path = "data/alert_needed.flag"
            with open(alert_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "type": "feishu_send_failed",
                    "error": error_msg,
                    "created_at": datetime.now().isoformat(),
                }, ensure_ascii=False))
        except Exception as e:
            logger.error(f"[FeishuNotifier] 写入告警标志失败: {e}")

    def _record_health(self, status: str, msg_file: str = "", content_len: int = 0, error: str = ""):
        """记录发送健康状态"""
        try:
            health = {"history": []}
            if os.path.exists(HEALTH_FILE):
                with open(HEALTH_FILE, "r", encoding="utf-8") as f:
                    health = json.load(f)

            # 只保留最近 50 条
            health["history"] = health.get("history", [])
            health["history"].append({
                "timestamp": datetime.now().isoformat(),
                "status": status,
                "msg_file": msg_file,
                "content_len": content_len,
                "error": error,
            })
            health["history"] = health["history"][-50:]

            # 统计
            health["total_outbox_written"] = sum(1 for h in health["history"] if h["status"] == "outbox_written")
            health["total_outbox_failed"] = sum(1 for h in health["history"] if h["status"] == "outbox_failed")
            health["last_status"] = status
            health["last_update"] = datetime.now().isoformat()

            with open(HEALTH_FILE, "w", encoding="utf-8") as f:
                json.dump(health, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"记录健康状态失败: {e}")

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
        return self._write_to_outbox(content, "markdown", kind="signal_report")

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
        return self._write_to_outbox(content, "markdown", kind="position_report")

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
        return self._write_to_outbox(content, "markdown", kind="trade_notification")

    def send_alert(self, alert_msg: str) -> bool:
        """发送告警消息（高优先级）"""
        content = f"🚨 **信号灯告警**\n\n{alert_msg}\n\n🕐 {self._now()}"
        return self._write_to_outbox(content, "markdown", kind="alert")

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M")


# 全局飞书通知器
_feishu_notifier: FeishuNotifier = None


def get_feishu_notifier() -> FeishuNotifier:
    global _feishu_notifier
    if _feishu_notifier is None:
        _feishu_notifier = FeishuNotifier()
    return _feishu_notifier
