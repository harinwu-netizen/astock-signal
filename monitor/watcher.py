# -*- coding: utf-8 -*-
"""
监控器
定时监控股票池，支持watch模式和后台运行
v4.2: 连续止损锁仓机制 + 弱势连续亏损强化买点
"""

import json
import logging
import os
from pathlib import Path
import time
import signal
import sys
from datetime import datetime, timedelta
from typing import List, Optional
from config import get_config
from models.watchlist import WatchlistStore
from models.position import PositionStore
from models.signal import MarketStatus, Decision
from monitor.scanner import Scanner
from trading.executor import get_executor
from trading.risk_control import RiskController
from notification.feishu import get_feishu_notifier

# 连续止损状态文件
LOSS_STREAK_FILE = Path(__file__).parent.parent / "data" / "loss_streak.json"

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

        # 启动时先判断是否为交易日
        now = datetime.now()
        if now.weekday() >= 5:
            logger.info(f"⛔ 今日({now.strftime('%Y-%m-%d')})为周末，非交易日，监控不启动")
            return

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
        # 0. 判断是否为交易日（A股周末/节假日直接跳过）
        today = datetime.now()
        if today.weekday() >= 5:  # 周六=5, 周日=6
            logger.info(f"⛔ 今日({today.strftime('%Y-%m-%d')})为周末，非交易日，跳过扫描")
            return

        logger.info("=" * 60)
        logger.info(f"🔍 扫描周期: {today.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)

        # 1. 检测市场状态（每周期只检测一次）
        market_regime = self._detect_market_regime()

        # 2. 加载股票池
        store = WatchlistStore()
        watchlist = store.load()

        # 3. 扫描（传入市场状态）
        signals = self.scanner.scan_watchlist(watchlist, market_regime=market_regime)
        if not signals:
            logger.info("无有效信号")
            return

        # 4. 处理可操作信号
        actions = self.scanner.get_actionable_signals(signals)

        # 5. 处理止损信号（优先）— 同时更新连续亏损状态
        if actions["stop_loss"]:
            self._handle_stop_loss_signals(actions["stop_loss"])

        # 6. 处理卖出信号
        if actions["sell"]:
            self._handle_sell_signals(actions["sell"])

        # 7. 处理买入信号（传入市场状态和配置）
        if actions["buy"]:
            self._handle_buy_signals(actions["buy"], market_regime)

        # 8. 更新持仓信号
        self._update_positions(signals)

        # 9. 发送通知
        if self.notifier.enabled:
            self.notifier.send_signal_report(signals)

    def _detect_market_regime(self):
        """
        检测市场状态（强市/震荡/弱市）
        返回: (regime_str, MarketRegimeResult)
        """
        try:
            from indicators.market_regime import detect_market_regime, MarketRegime, get_market_regime_str
            regime_result = detect_market_regime()
            regime_str = get_market_regime_str(regime_result.regime)
            logger.info(f"📊 市场状态: {regime_str} — {regime_result.reason}")
            return regime_result
        except Exception as e:
            logger.warning(f"市场状态检测失败，默认震荡: {e}")
            return None

    # ==================================================================== #
    #  v4.2 连续止损锁仓状态管理
    # ==================================================================== #

    def _load_loss_streak(self) -> dict:
        """加载连续止损状态"""
        if not LOSS_STREAK_FILE.exists():
            return {}
        try:
            with open(LOSS_STREAK_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_loss_streak(self, state: dict):
        """保存连续止损状态"""
        LOSS_STREAK_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(LOSS_STREAK_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存连续止损状态失败: {e}")

    def _update_loss_streak_on_sell(self, code: str, pnl: float) -> tuple:
        """
        卖出后更新连续亏损计数
        Returns: (loss_streak, lock_until_date_str or None)
        """
        state = self._load_loss_streak()
        today = datetime.now().strftime("%Y-%m-%d")

        if code not in state:
            state[code] = {"streak": 0, "lock_until": ""}

        if pnl < 0:
            state[code]["streak"] += 1
            if state[code]["streak"] >= self.config.consecutive_stop_loss_lock:
                lock_until = (datetime.now() + timedelta(
                    days=self.config.consecutive_stop_loss_lock_days
                )).strftime("%Y-%m-%d")
                state[code]["lock_until"] = lock_until
                logger.warning(
                    f"🔒 [{code}] 连续{state[code]['streak']}笔亏损，"
                    f"锁仓至{lock_until}（{self.config.consecutive_stop_loss_lock_days}个交易日）"
                )
            self._save_loss_streak(state)
            return state[code]["streak"], state[code]["lock_until"]
        else:
            state[code] = {"streak": 0, "lock_until": ""}
            self._save_loss_streak(state)
            return 0, ""

    def _is_locked(self, code: str) -> bool:
        """检查股票是否处于锁仓期"""
        state = self._load_loss_streak()
        if code not in state or not state[code].get("lock_until"):
            return False
        today = datetime.now().strftime("%Y-%m-%d")
        if today <= state[code]["lock_until"]:
            return True
        # 锁仓期已过，清除状态
        if state[code]["streak"] > 0:
            state[code] = {"streak": 0, "lock_until": ""}
            self._save_loss_streak(state)
        return False

    def _handle_stop_loss_signals(self, signals):
        """处理止损信号（v4.1 弱强市自适应优化）"""
        position_store = PositionStore()
        executor = self.executor
        cfg = self.config

        for signal in signals:
            position = position_store.find_position(signal.code)
            if not position:
                continue

            from datetime import datetime
            hold_days = (datetime.now().date() - datetime.strptime(position.buy_date, "%Y-%m-%d").date()).days
            pnl_pct = (signal.price - position.buy_price) / position.buy_price * 100

            sell_reason = None

            # ---- 条件1：ATR追踪止损（持仓记录里的atr_multiplier）----
            if signal.price <= position.stop_loss:
                sell_reason = f"ATR追踪止损({signal.price:.2f}≤{position.stop_loss:.2f})"

            # ---- 条件2：持仓超期（震荡10天/强市30天；弱市已取消此限制）----
            elif position.market_regime != "WEAK" and hold_days >= position.max_hold_days:
                sell_reason = f"持仓超期({hold_days}天≥{position.max_hold_days}天)"

            # ---- 条件3：亏损超限（弱市-3%/震荡-10%/强市-15%）----
            elif pnl_pct <= -position.stop_loss_pct:
                sell_reason = f"亏损超限({pnl_pct:.1f}%≤-{position.stop_loss_pct}%)"

            # ---- 条件4：弱市RSI反弹到位（>65）----
            elif position.market_regime == "WEAK" and signal.rsi_6 > cfg.weak_rsi_sell_threshold:
                sell_reason = f"弱市RSI反弹到位({signal.rsi_6:.1f}>{cfg.weak_rsi_sell_threshold:.0f})"

            if sell_reason:
                logger.warning(f"🚨 触发风控: {position.name} @{signal.price:.2f} — {sell_reason}")
                result = executor.execute_stop_loss(position)
                if result.success and result.pnl is not None:
                    self._update_loss_streak_on_sell(position.code, result.pnl)
                if self.notifier.enabled:
                    self.notifier.send_trade_notification(result.__dict__)
                continue

            # ---- 止盈：弱市=MA20 / 震荡=固定% / 强市=固定% ----
            take_profit_triggered = False
            if position.market_regime == "WEAK":
                # 弱市止盈：反弹到MA20（v4.1新增）
                if position.ma20_take_profit > 0 and signal.price >= position.ma20_take_profit:
                    take_profit_triggered = True
                    sell_reason = f"弱市MA20止盈({signal.price:.2f}≥MA20{position.ma20_take_profit:.2f})"
            elif position.market_regime == "STRONG" and pnl_pct >= cfg.strong_take_profit_pct:
                take_profit_triggered = True
                sell_reason = f"强市止盈({pnl_pct:.1f}%≥{cfg.strong_take_profit_pct}%)"
            elif position.market_regime == "CONSOLIDATE" and pnl_pct >= cfg.consolidate_take_profit_pct:
                take_profit_triggered = True
                sell_reason = f"震荡止盈({pnl_pct:.1f}%≥{cfg.consolidate_take_profit_pct}%)"

            if take_profit_triggered:
                logger.warning(f"🎯 触发止盈: {position.name} @{signal.price:.2f} — {sell_reason}")
                result = executor.execute_sell(position, position.quantity, reason="止盈", signal=signal)
                if result.success and result.pnl is not None:
                    self._update_loss_streak_on_sell(position.code, result.pnl)
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

    def _handle_buy_signals(self, signals, market_regime_result=None):
        """处理买入信号（v4.0 弱强市自适应）"""
        if self.config.notify_only:
            logger.info(f"📋 买入信号（只推不交易模式）: {[s.name for s in signals]}")
            return

        if not self.config.auto_trade:
            return

        # 根据市场状态获取对应参数
        cfg = self.config
        if market_regime_result is not None:
            regime = market_regime_result.regime.value  # "弱市"/"震荡"/"强市"
        else:
            regime = "CONSOLIDATE"

        # 获取市场状态枚举（兼容 scanner）
        from models.signal import MarketStatus
        regime_enum_map = {
            "弱市": MarketStatus.WEAK,
            "震荡": MarketStatus.CONSOLIDATE,
            "强市": MarketStatus.STRONG,
        }
        regime_enum = regime_enum_map.get(regime, MarketStatus.CONSOLIDATE)

        # 根据市场状态加载对应参数
        if regime_enum == MarketStatus.WEAK:
            atr_multiplier = cfg.weak_atr_multiplier
            stop_loss_pct = cfg.weak_stop_loss_pct
            take_profit_pct = cfg.weak_take_profit_pct
            max_position_pct = cfg.weak_single_position_pct
            total_position_pct = cfg.weak_total_position_pct
            open_window_start = cfg.weak_open_window_start
        elif regime_enum == MarketStatus.STRONG:
            atr_multiplier = cfg.strong_atr_multiplier
            stop_loss_pct = cfg.strong_stop_loss_pct
            take_profit_pct = cfg.strong_take_profit_pct
            max_position_pct = cfg.strong_single_position_pct
            total_position_pct = cfg.strong_total_position_pct
            open_window_start = cfg.open_window_start
        else:
            atr_multiplier = cfg.consolidate_atr_multiplier
            stop_loss_pct = cfg.consolidate_stop_loss_pct
            take_profit_pct = cfg.consolidate_take_profit_pct
            max_position_pct = cfg.consolidate_single_position_pct
            total_position_pct = cfg.consolidate_total_position_pct
            open_window_start = cfg.open_window_start

        logger.info(f"📊 [{regime}] ATR倍数={atr_multiplier}x, 止损={stop_loss_pct}%, 止盈={take_profit_pct}%, 仓位上限={max_position_pct}%")

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

            # ---- v4.2 ③：锁仓期检查 ----
            if self._is_locked(signal.code):
                logger.info(f"🔒 [{signal.code}] 处于锁仓期，跳过")
                continue

            # ---- v4.2 ④：弱市连续亏损后强化买点 ----
            loss_streak_state = self._load_loss_streak()
            streak = loss_streak_state.get(signal.code, {}).get("streak", 0)
            if regime_enum == MarketStatus.WEAK and streak >= cfg.weak_consecutive_loss_count:
                required = 2 + cfg.weak_consecutive_loss_extra_signals
                if signal.rebound_count < required:
                    logger.info(f"⚠️ [{signal.code}] 弱市连续{streak}亏，强化信号要求: {signal.rebound_count}<{required}，跳过")
                    continue

            # 计算仓位（不能超过市场状态对应的上限）
            position_ratio = signal.position_ratio
            if position_ratio <= 0:
                position_ratio = max_position_pct / 100

            # 总仓位检查
            open_positions = position_store.get_open_positions()
            current_total_pct = sum(p.cost for p in open_positions) / cfg.total_capital * 100
            remaining_pct = total_position_pct - current_total_pct
            if remaining_pct <= 0:
                logger.info(f"⚠️ 总仓位已达上限({total_position_pct}%)，暂停开仓")
                continue

            position_ratio = min(position_ratio, remaining_pct / 100)

            amount = cfg.total_capital * position_ratio
            amount = int(amount // 100) * 100  # 取整到百元

            if amount < 1000:
                continue

            quantity = amount // (signal.price * 100)
            if quantity < 1:
                continue

            # 执行买入（传递市场状态参数）
            regime_str_for_pos = regime_enum.name  # WEAK/CONSOLIDATE/STRONG
            logger.info(
                f"🟢 买入信号: {signal.name} "
                f"反弹{int(signal.rebound_count)}/趋势{int(signal.trend_count)}/经典{int(signal.buy_count)} "
                f"→ 仓位{signal.position_ratio:.0%}，买{quantity}手 @{signal.price:.2f}"
            )
            result = executor.execute_buy(
                signal, quantity,
                atr=signal.atr,
                market_regime=regime_str_for_pos,
                atr_multiplier=atr_multiplier,
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
            )

            if self.notifier.enabled:
                self.notifier.send_trade_notification(result.__dict__)

    def _update_positions(self, signals: list):
        """更新持仓的信号状态（v4.0，含每日ATR追踪止损重建）"""
        position_store = PositionStore()
        positions = position_store.get_open_positions()

        signal_map = {s.code: s for s in signals}

        for position in positions:
            if position.code in signal_map:
                s = signal_map[position.code]

                # 更新基础信号数
                position.latest_buy_signals = s.buy_count
                position.latest_sell_signals = s.sell_count
                position.latest_rebound_signals = getattr(s, 'rebound_count', 0)
                position.latest_trend_signals = getattr(s, 'trend_count', 0)

                # 每日重建ATR追踪止损（用持仓开仓时记录的atr_multiplier）
                if s.atr > 0 and position.atr_multiplier > 0:
                    new_stop = s.price - position.atr_multiplier * s.atr
                    position.trailing_stop = max(position.trailing_stop, new_stop)
                    position.atr = s.atr

                position.update_current(s.price)

        position_store.save(positions)


def start_watcher(continuous: bool = False):
    """启动监控器的便捷函数"""
    watcher = Watcher()
    watcher.start(continuous=continuous)
