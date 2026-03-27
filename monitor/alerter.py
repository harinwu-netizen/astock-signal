# -*- coding: utf-8 -*-
"""
预警器
监控持仓异常情况并发送预警
"""

import logging
from datetime import datetime
from typing import List, Optional
from config import get_config
from models.position import Position, PositionStore
from models.signal import MarketStatus
from notification.feishu import get_feishu_notifier

logger = logging.getLogger(__name__)


class Alert:
    """预警消息"""

    def __init__(self, level: str, title: str, content: str, code: str = ""):
        self.level = level      # INFO / WARNING / DANGER
        self.title = title
        self.content = content
        self.code = code
        self.timestamp = datetime.now()


class Alerter:
    """
    预警器

    监控以下异常:
    1. 止损预警（亏损接近8%，距止损2%空间）
    2. 止盈预警（达到目标价位）
    3. 信号转弱（买点减少/卖点增加）
    4. 大盘异动（涨跌幅>1%）
    5. 持仓超时（持有超过N日）
    """

    def __init__(self):
        self.config = get_config()
        self.notifier = get_feishu_notifier()
        self._last_alerts = {}  # 避免重复预警

    def check_positions(self, signals: list) -> List[Alert]:
        """
        检查所有持仓，生成预警

        Args:
            signals: RealTimeSignal列表

        Returns:
            Alert列表
        """
        alerts = []
        position_store = PositionStore()
        positions = position_store.get_open_positions()

        signal_map = {s.code: s for s in signals}

        for position in positions:
            s = signal_map.get(position.code)
            if not s:
                continue

            # 1. 止损预警
            alert = self._check_stop_loss_alert(position, s)
            if alert:
                alerts.append(alert)

            # 2. 止盈预警
            alert = self._check_take_profit_alert(position, s)
            if alert:
                alerts.append(alert)

            # 3. 信号转弱预警
            alert = self._check_signal_weakening(position, s)
            if alert:
                alerts.append(alert)

        return alerts

    def _check_stop_loss_alert(self, position: Position, signal) -> Optional[Alert]:
        """止损预警：亏损>8%且距止损价<2%"""
        if position.stop_loss <= 0 or signal.price <= 0:
            return None

        loss_pct = (position.buy_price - signal.price) / position.buy_price * 100
        distance_to_stop = (signal.price - position.stop_loss) / signal.price * 100

        # 亏损超8%且距止损不到2%
        if loss_pct > 8 and distance_to_stop < 2:
            return Alert(
                level="DANGER",
                title=f"🚨 {position.name} 止损预警",
                content=(
                    f"持仓亏损 {loss_pct:.1f}%，距止损价仅 {distance_to_stop:.1f}% 空间\n"
                    f"买入价: ¥{position.buy_price:.2f}\n"
                    f"当前价: ¥{signal.price:.2f}\n"
                    f"止损价: ¥{position.stop_loss:.2f}\n"
                    f"建议: 考虑主动止损"
                ),
                code=position.code,
            )

        # 预警：亏损超5%
        if loss_pct > 5:
            return Alert(
                level="WARNING",
                title=f"⚠️ {position.name} 亏损关注",
                content=(
                    f"持仓亏损 {loss_pct:.1f}%，注意风险\n"
                    f"买入价: ¥{position.buy_price:.2f}\n"
                    f"当前价: ¥{signal.price:.2f}\n"
                    f"止损价: ¥{position.stop_loss:.2f}"
                ),
                code=position.code,
            )

        return None

    def _check_take_profit_alert(self, position: Position, signal) -> Optional[Alert]:
        """止盈预警：价格达到目标位"""
        if position.take_profit <= 0 or signal.price <= 0:
            return None

        profit_pct = (signal.price - position.buy_price) / position.buy_price * 100
        target_pct = (position.take_profit - position.buy_price) / position.buy_price * 100

        # 达到目标价的80%以上
        if profit_pct >= target_pct * 0.8:
            return Alert(
                level="INFO",
                title=f"🎯 {position.name} 接近目标",
                content=(
                    f"持仓盈利 {profit_pct:.1f}%，已达目标 {target_pct:.1f}% 的 {profit_pct/target_pct*100:.0f}%\n"
                    f"买入价: ¥{position.buy_price:.2f}\n"
                    f"当前价: ¥{signal.price:.2f}\n"
                    f"目标价: ¥{position.take_profit:.2f}\n"
                    f"建议: 可分批止盈"
                ),
                code=position.code,
            )

        return None

    def _check_signal_weakening(self, position: Position, signal) -> Optional[Alert]:
        """信号转弱预警"""
        # 买点信号大幅减少
        if position.latest_buy_signals > 0 and signal.buy_count < position.latest_buy_signals - 2:
            return Alert(
                level="WARNING",
                title=f"📉 {position.name} 信号转弱",
                content=(
                    f"买点信号从 {position.latest_buy_signals} 降至 {signal.buy_count}\n"
                    f"当前决策: {signal.decision.value}\n"
                    f"建议: 关注是否需要减仓"
                ),
                code=position.code,
            )

        # 卖点信号大幅增加
        if signal.sell_count >= 3 and signal.sell_count > position.latest_sell_signals:
            return Alert(
                level="WARNING",
                title=f"🔴 {position.name} 出现卖出信号",
                content=(
                    f"当前卖点信号: {signal.sell_count}/6\n"
                    f"建议关注: {', '.join(signal.sell_signals_detail[:3])}\n"
                    f"建议: 考虑减仓"
                ),
                code=position.code,
            )

        return None

    def check_market(self, market_status: MarketStatus, market_change: float) -> Optional[Alert]:
        """大盘异动预警"""
        if abs(market_change) > 1.5:
            if market_change > 0:
                return Alert(
                    level="INFO",
                    title="📈 大盘大涨",
                    content=f"上证指数涨幅 {market_change:+.2f}%，市场强势",
                )
            else:
                return Alert(
                    level="DANGER" if market_change < -2 else "WARNING",
                    title="📉 大盘大跌",
                    content=f"上证指数跌幅 {market_change:+.2f}%，注意风险",
                )
        return None

    def send_alerts(self, alerts: List[Alert]):
        """发送预警（去重+飞书推送）"""
        if not alerts:
            return

        for alert in alerts:
            # 去重检查
            key = f"{alert.code}:{alert.level}:{alert.title}"
            if key in self._last_alerts:
                last_time = self._last_alerts[key]
                if (alert.timestamp - last_time).seconds < 300:
                    continue  # 5分钟内不重复预警

            self._last_alerts[key] = alert.timestamp

            # 发送飞书
            if self.notifier.enabled:
                self.notifier.send(f"**{alert.title}**\n\n{alert.content}", msg_type="markdown")

            # 打印日志
            if alert.level == "DANGER":
                logger.warning(f"{alert.title}: {alert.content}")
            else:
                logger.info(f"{alert.title}: {alert.content}")
