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
from datetime import datetime, timedelta, time as dtime
from typing import List, Optional

# v6.15.0 (2026-06-15): is_trading_day() 重构
# 旧实现: 依赖 txstock.get_history('sh000001', days=5) 检查 last_date == today
# 问题: 腾讯财经日 K 数据开盘后 9:30 前后才更新, 9:15-9:29 期间 last_date 还是昨日
#   → 被误判为"非交易日"跳过扫描, 6/3 9:15/9:20/9:25 三次 scan 被跳过
#   → 6/15 (周一) 9:15 启动后预计重复同样问题
# 新实现: 复用 run_watch_daemon.sh 的 weekday+节假日表判断, 不依赖 txstock 数据
#   守护脚本用同一套逻辑启动 watch, Python 端必须同步, 否则会出现
#   "守护脚本启动 watch → watch 立即判定非交易日退出" 的循环
from main import is_trading_day  # noqa: E402, F401
from models.watchlist import WatchlistStore
from config import get_config
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
        if not is_trading_day():
            logger.info(f"⛔ 今日({now.strftime('%Y-%m-%d')})为周末，非交易日，监控不启动")
            return

        while not self._stop_requested:
            now = datetime.now()
            now_time = now.time()

            # v6.5: 两段窗口(跳午休),使用 main.is_in_open_window()
            from main import is_in_open_window
            in_window = is_in_open_window(now_time)

            # 在窗口内？
            if in_window:
                logger.info(f"🟢 进入开仓窗口: {now.strftime('%H:%M:%S')}")
                self._run_scan_cycle()
                # 每5分钟扫描一次
                time.sleep(config.watch_interval)
            elif now_time >= dtime(15, 0):
                # 窗口已过(15:00),退出
                logger.info(f"⏰ 开仓窗口已过(15:00),监控结束")
                break
            else:
                # 等待(包含 09:15-10:00 / 11:30-13:00 午休)
                wait_secs = 60
                logger.debug(f"等待开仓窗口，还有 {wait_secs}秒 (10:00-11:30 / 13:00-15:00)")
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
        if not is_trading_day():
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

        # 5.5 v6.13 (2026-06-14): 独立止损检查
        # 旧问题：止损信号依赖 actionable_signals['stop_loss']，但
        #   资金流失败时所有 signal 都被降级 WATCH → stop_loss 列表为空
        #   → 持仓浮亏超 -2% (弱市止损线) 但不会触发
        # 新逻辑：直接遍历持仓，用对应股票的实时信号检查止损
        self._handle_stop_loss_for_all_positions(signals)

        # 6. 处理卖出信号
        if actions["sell"]:
            self._handle_sell_signals(actions["sell"])

        # 7. 处理买入信号（传入市场状态和配置）
        if actions["buy"]:
            self._handle_buy_signals(actions["buy"], market_regime)

        # 8. 更新持仓信号
        self._update_positions(signals)

        # 9. 发送通知（v6.5: 仅在开仓窗口内发送,避免休市/午休期间骚扰）
        from datetime import datetime as _dt
        from main import is_in_open_window as _is_in_open_window
        now_time = _dt.now().time()
        in_window = _is_in_open_window(now_time)
        if self.notifier.enabled and in_window:
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

    def _write_silenced_signal_alert(self, signal, reason: str, extra: dict = None):
        """v6.13 (2026-06-14): 写"信号被静默吃掉"告警到 outbox

        适用场景：
          - amount < 1000 静默跳过 (仓位被占用)
          - quantity < 1 静默跳过
          - 资金流失败 BUY 降级 WATCH
        """
        try:
            from datetime import datetime
            import json
            from pathlib import Path

            # 写到 data/alert_silenced_signals/ 目录
            alert_dir = Path(self.config.data_dir) / "alert_silenced_signals"
            alert_dir.mkdir(parents=True, exist_ok=True)

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            alert = {
                "timestamp": datetime.now().isoformat(),
                "code": signal.code,
                "name": signal.name,
                "price": signal.price,
                "decision": signal.decision.value if hasattr(signal.decision, 'value') else str(signal.decision),
                "buy_count": signal.buy_count,
                "reason_silenced": reason,
                "extra": extra or {},
            }
            alert_file = alert_dir / f"{ts}_{signal.code}_{reason}.json"
            with open(alert_file, "w", encoding="utf-8") as f:
                json.dump(alert, f, ensure_ascii=False, indent=2)

            # 也发到飞书 outbox (但不走 _run_scan_cycle 同一轮，避免循环)
            notifier = self.notifier
            if notifier.enabled:
                content = (
                    f"⚠️ *信号被静默吃掉* `{signal.code} {signal.name}`\n"
                    f"原因: `{reason}`\n"
                    f"价格: ¥{signal.price:.2f}  决策: {alert['decision']}  buy_count: {signal.buy_count}\n"
                    f"extra: `{json.dumps(extra or {}, ensure_ascii=False)}`"
                )
                notifier._write_to_outbox(content, "markdown", kind="silenced_signal_alert")
        except Exception as e:
            logger.error(f"写信号静默告警失败: {e}")

    # v6.15 (2026-06-15) 止损决策统一 helper
    # 修复: 1) position.max_hold_days 不存在 → 按 regime 选 cfg.{strong,consolidate}_max_hold_days
    #       2) != "WEAK" 字符串 vs 枚举比较永真 → 改用 is not MarketStatus.WEAK
    #       3) 抽公共逻辑,消除 _handle_stop_loss_signals / _handle_stop_loss_for_all_positions 重复
    def _decide_sell_reason(self, position, sig, cfg) -> Optional[str]:
        """统一止损决策: 返回卖出原因字符串, 或 None(不需要卖)

        优先级 (与 v6.14 行为完全一致):
        1) ATR 追踪止损
        2) 持仓超期(弱市禁用)
        3) 亏损超限
        4) 弱市 RSI 反弹到位
        """
        from datetime import datetime
        hold_days = (datetime.now().date() - datetime.strptime(position.buy_date, "%Y-%m-%d").date()).days
        pnl_pct = (sig.price - position.buy_price) / position.buy_price * 100
        regime = position.market_regime

        # 1) ATR 追踪止损
        if sig.price <= position.stop_loss:
            return f"ATR追踪止损({sig.price:.2f}≤{position.stop_loss:.2f})"

        # 2) 持仓超期(弱市禁用;其他市况按对应 max_hold_days)
        if regime is not MarketStatus.WEAK:
            if regime is MarketStatus.STRONG:
                max_hold = cfg.strong_max_hold_days
            else:  # CONSOLIDATE 或其他 → 落到震荡
                max_hold = cfg.consolidate_max_hold_days
            if hold_days >= max_hold:
                return f"持仓超期({hold_days}天≥{max_hold}天)"

        # 3) 亏损超限
        if pnl_pct <= -position.stop_loss_pct:
            return f"亏损超限({pnl_pct:.1f}%≤-{position.stop_loss_pct}%)"

        # 4) 弱市 RSI 反弹到位
        if regime is MarketStatus.WEAK and sig.rsi_6 > cfg.weak_rsi_sell_threshold:
            return f"弱市RSI反弹到位({sig.rsi_6:.1f}>{cfg.weak_rsi_sell_threshold:.0f})"

        return None

    def _handle_stop_loss_for_all_positions(self, signals):
        """v6.13 (2026-06-14): 独立遍历持仓检查止损

        不依赖 actionable_signals['stop_loss']，确保资金流失败/信号降级时
        止损检查不被静默跳过。
        v6.15 (2026-06-15): 委托给 _decide_sell_reason 统一决策
        """
        from datetime import datetime
        position_store = PositionStore()
        executor = self.executor
        cfg = self.config

        # v6.13.1 (2026-06-14): 增加交易时间窗口保护
        # 凌晨/午休/盘后不应该执行实际交易
        now_time = datetime.now().time()
        from main import is_in_open_window
        if not is_in_open_window(now_time):
            logger.debug(
                f"_handle_stop_loss_for_all_positions: 非交易时间窗口({now_time.strftime('%H:%M')}),"
                f" 止损检查跳过（仅记录状态，不实际下单）"
            )
            # 只 log，不实际下单 — 避免凌晨 1 点被止损
            # 实际下单推到下一轮交易时间内执行
            for position in position_store.get_open_positions():
                sig = next((s for s in signals if s.code == position.code), None)
                if not sig:
                    continue
                pnl_pct = (sig.price - position.buy_price) / position.buy_price * 100
                if sig.price <= position.stop_loss or pnl_pct <= -position.stop_loss_pct:
                    logger.warning(
                        f"⚠️ [{position.name}] 非交易时间检测到止损条件:"
                        f" 价格¥{sig.price:.2f} 止损¥{position.stop_loss:.2f}"
                        f" 浮亏{pnl_pct:.1f}%, 等待交易时间窗口执行"
                    )
            return

        # 按 code 索引当前 signals
        signal_map = {s.code: s for s in signals}

        for position in position_store.get_open_positions():
            sig = signal_map.get(position.code)
            if not sig:
                # 该持仓股票不在股票池中 → 跳过
                # (理论上不会发生，但加保护)
                continue

            reason = self._decide_sell_reason(position, sig, cfg)
            if reason:
                logger.warning(f"🚨 [v6.15 独立止损] {position.name} @{sig.price:.2f} — {reason}")
                result = executor.execute_stop_loss(position)
                # v6.14 P2-7 修复: TradeResult.pnl 现已由 executor 填充, 不再 AttributeError
                if result.success and result.pnl is not None:
                    self._update_loss_streak_on_sell(position.code, result.pnl)
                if self.notifier.enabled:
                    self.notifier.send_trade_notification(result.__dict__)

    def _handle_stop_loss_signals(self, signals):
        """处理止损信号（v4.1 弱强市自适应优化）
        v6.15 (2026-06-15): 委托给 _decide_sell_reason 统一决策
        """
        position_store = PositionStore()
        executor = self.executor
        cfg = self.config

        for signal in signals:
            position = position_store.find_position(signal.code)
            if not position:
                continue

            reason = self._decide_sell_reason(position, signal, cfg)
            if reason:
                logger.warning(f"🚨 触发风控: {position.name} @{signal.price:.2f} — {reason}")
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
            logger.debug("_handle_buy_signals 跳过：auto_trade=False")
            return

        # v6.13 (2026-06-14): 让"本周期有 N 个 BUY 决策"可观测
        # 之前 _handle_sell_signals 有 log, _handle_buy_signals 没有
        # 导致 amount<1000 静默 continue 等 bug 完全不可见
        logger.info(
            f"📋 _handle_buy_signals 进入: 本周期 {len(signals)} 个 BUY 决策 → "
            f"{[s.name for s in signals]}"
        )

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
                # v6.13 (2026-06-14): 静默 continue 会让"信号被吃掉"完全不可见
                # 仓位已接近上限时（弱市满仓+新 BUY 信号）会走到这里
                # 改为可观测 log + 写 outbox 告警
                logger.warning(
                    f"⚠️ [{signal.name}] 剩余可开仓 ¥{amount} < ¥1000 最低买入额，"
                    f"跳过 (position_ratio={position_ratio:.4f}, "
                    f"持仓占用 {current_total_pct:.2f}%, 上限 {total_position_pct}%)"
                )
                self._write_silenced_signal_alert(signal, reason="amount_too_small",
                                                   extra={"amount": amount, "position_ratio": position_ratio})
                continue

            quantity = amount // (signal.price * 100)
            if quantity < 1:
                # v6.13: 同样加 log
                logger.warning(
                    f"⚠️ [{signal.name}] amount=¥{amount} 但 {signal.price:.2f}×100 后 quantity<1，跳过"
                )
                self._write_silenced_signal_alert(signal, reason="quantity_too_small",
                                                   extra={"amount": amount, "price": signal.price})
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
