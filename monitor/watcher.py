# -*- coding: utf-8 -*-
"""
监控器
定时监控股票池，支持watch模式和后台运行
"""

import logging
from typing import List, Optional
import time
import signal
import sys
from datetime import datetime
from typing import Optional
from config import get_config
from models.watchlist import WatchlistStore
from models.position import PositionStore
from models.signal import MarketStatus, Decision
from monitor.scanner import Scanner
from trading.executor import get_executor
from trading.risk_control import RiskController
from notification.feishu import get_feishu_notifier

logger = logging.getLogger(__name__)


class Watcher:
    """
    监控器

    工作模式:
    1. watch模式: 14:30-15:00每5分钟扫描，符合条件触发交易
    2. continuous模式: 持续扫描（测试用）
    """

    def __init__(self):
        self.config = get_config()
        self.scanner = Scanner()
        self.executor = get_executor()
        self.risk_controller = RiskController()
        self.notifier = get_feishu_notifier()
        self._stop_requested = False

    def start(self, continuous: bool = False):
        """
        启动监控

        Args:
            continuous: 是否持续监控（True=测试模式，False=14:30-15:00窗口）
        """
        logger.info(f"🚀 监控器启动 (continuous={continuous})")

        # 注册信号处理（优雅退出）
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        if continuous:
            self._run_continuous()
        else:
            self._run_watch_mode()

    def stop(self):
        """请求停止监控"""
        self._stop_requested = True
        logger.info("监控器收到停止信号...")

    def _handle_signal(self, signum, frame):
        """处理退出信号"""
        logger.info(f"收到信号 {signum}，准备退出...")
        self.stop()

    def _run_watch_mode(self):
        """watch模式: 等待到开仓窗口，然后开始扫描"""
        config = self.config

        while not self._stop_requested:
            now = datetime.now()
            now_time = now.time()

            open_start = datetime.strptime(config.open_window_start, "%H:%M").time()
            open_end = datetime.strptime(config.open_window_end, "%H:%M").time()

            # 在窗口内？
            if open_start <= now_time <= open_end:
                logger.info(f"🟢 进入开仓窗口: {now.strftime('%H:%M:%S')}")
                self._run_scan_cycle()
                # 每5分钟扫描一次
                time.sleep(config.watch_interval)
            elif now_time > open_end:
                # 窗口已过，退出
                logger.info(f"⏰ 开仓窗口已过({open_end})，监控结束")
                break
            else:
                # 等待
                wait_secs = 60
                logger.debug(f"等待开仓窗口，还有 {wait_secs}秒")
                time.sleep(wait_secs)

    def _run_continuous(self):
        """持续监控模式（测试用）"""
        logger.info("🔄 持续监控模式（Ctrl+C 退出）")
        while not self._stop_requested:
            self._run_scan_cycle()
            time.sleep(self.config.watch_interval)

    def _run_scan_cycle(self):
        """执行一次完整的扫描周期"""
        logger.info("=" * 60)
        logger.info(f"🔍 扫描周期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)

        # 1. 加载股票池
        store = WatchlistStore()
        watchlist = store.load()

        # 2. 扫描
        signals = self.scanner.scan_watchlist(watchlist)
        if not signals:
            logger.info("无有效信号")
            return

        # 3. 处理可操作信号
        actions = self.scanner.get_actionable_signals(signals)

        # 4. 处理止损信号（优先）
        if actions["stop_loss"]:
            self._handle_stop_loss_signals(actions["stop_loss"])

        # 5. 处理卖出信号
        if actions["sell"]:
            self._handle_sell_signals(actions["sell"])

        # 6. 处理买入信号
        if actions["buy"]:
            self._handle_buy_signals(actions["buy"])

        # 7. 更新持仓信号
        self._update_positions(signals)

        # 8. 发送通知
        if self.notifier.enabled:
            self.notifier.send_signal_report(signals)

    def _handle_stop_loss_signals(self, signals):
        """处理止损信号"""
        position_store = PositionStore()
        executor = self.executor

        for signal in signals:
            position = position_store.find_position(signal.code)
            if not position:
                continue

            # 检查是否真的触发了止损
            if signal.price <= position.stop_loss:
                logger.warning(f"🚨 触发止损: {signal.name} @{signal.price:.2f} <= 止损价{position.stop_loss:.2f}")
                result = executor.execute_stop_loss(position)
                if self.notifier.enabled:
                    self.notifier.send_trade_notification(result.__dict__)

    def _handle_sell_signals(self, signals):
        """处理卖出信号"""
        if self.config.notify_only:
            logger.info(f"📋 卖出信号（只推不交易模式）: {[s.name for s in signals]}")
            return

        if not self.config.auto_trade:
            return

        position_store = PositionStore()
        executor = self.executor

        for signal in signals:
            position = position_store.find_position(signal.code)
            if not position:
                continue

            # 计算卖出比例
            sell_ratio = self.risk_controller.get_sell_ratio(signal.sell_count, 0.5)
            if sell_ratio <= 0:
                continue

            quantity = int(position.quantity * sell_ratio)
            if quantity < 1:
                quantity = position.quantity  # 至少卖1手

            logger.info(f"🔴 卖出信号: {signal.name} {signal.sell_count}个信号，卖{quantity}手")
            result = executor.execute_sell(position, quantity, reason="信号卖出", signal=signal)

            if self.notifier.enabled:
                self.notifier.send_trade_notification(result.__dict__)

    def _handle_buy_signals(self, signals):
        """处理买入信号"""
        if self.config.notify_only:
            logger.info(f"📋 买入信号（只推不交易模式）: {[s.name for s in signals]}")
            return

        if not self.config.auto_trade:
            return

        # 检查是否可以开仓
        can_open, reason = self.risk_controller.can_open_position("")
        if not can_open:
            logger.info(f"⚠️ 无法开仓: {reason}")
            return

        executor = self.executor

        for signal in signals:
            # 检查是否已有持仓
            position_store = PositionStore()
            if position_store.find_position(signal.code):
                continue

            # 检查同日交易限制
            from models.trade import TradeStore
            trade_store = TradeStore()
            if trade_store.has_traded_today(signal.code):
                logger.debug(f"同日已交易，跳过: {signal.code}")
                continue

            # 计算仓位
            position_ratio = signal.position_ratio
            if position_ratio <= 0:
                position_ratio = 0.3  # 默认30%

            amount = self.config.total_capital * position_ratio
            amount = int(amount // 100) * 100  # 取整到百元

            if amount < 1000:
                continue

            quantity = amount // (signal.price * 100)
            if quantity < 1:
                continue

            # 执行买入
            logger.info(f"🟢 买入信号: {signal.name} {signal.buy_count}个信号，买{quantity}手 @{signal.price:.2f}")
            result = executor.execute_buy(signal, quantity, atr=signal.atr)

            if self.notifier.enabled:
                self.notifier.send_trade_notification(result.__dict__)

    def _update_positions(self, signals: list):
        """更新持仓的信号状态"""
        position_store = PositionStore()
        positions = position_store.get_open_positions()

        signal_map = {s.code: s for s in signals}

        for position in positions:
            if position.code in signal_map:
                s = signal_map[position.code]
                position.latest_buy_signals = s.buy_count
                position.latest_sell_signals = s.sell_count
                position.update_current(s.price)

        position_store.save(positions)


def start_watcher(continuous: bool = False):
    """启动监控器的便捷函数"""
    watcher = Watcher()
    watcher.start(continuous=continuous)
