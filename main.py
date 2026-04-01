#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
astock-signal 主入口
A股信号灯 - CLI工具

用法:
  python main.py pool list              # 查看股票池
  python main.py pool add 000629       # 添加股票
  python main.py pool remove 000629    # 删除股票
  python main.py pool enable 000629    # 启用监控
  python main.py pool disable 000629   # 禁用监控
  python main.py analyze 000629        # 分析单只股票
  python main.py analyze --pool        # 分析股票池所有股票
  python main.py watch                 # 启动实时监控（14:30-15:00）
  python main.py position              # 查看持仓
  python main.py report                # 收盘复盘报告
  python main.py settings              # 查看设置
  python main.py settings --notify-only=false  # 修改设置
"""

import sys
import os
import logging
import argparse
import time
import uuid
from datetime import datetime, time as dtime

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import get_config, reload_config
from models.watchlist import WatchlistStore
from models.position import PositionStore, Position, Portfolio
from models.signal import MarketStatus
from models.trade import TradeRecord, TradeStore
from data_provider.txstock import TxStock
from indicators.signal_counter import SignalCounter
from strategy.market_filter import get_market_filter, get_market_warning
from trading.pre_check import PreTradeChecker
from notification.feishu import get_feishu_notifier

# ===== 日志配置 =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


# ==================== 股票池管理 ====================

def cmd_pool_list():
    """查看股票池"""
    store = WatchlistStore()
    watchlist = store.load()
    settings = watchlist.settings

    print("\n📋 股票池")
    print("=" * 50)
    print(f"当前设置: 自动交易={'✅' if settings.auto_trade else '❌'} | "
          f"只推不交易={'✅' if settings.notify_only else '❌'} | "
          f"最大持仓: {settings.max_positions}只")
    print()

    if not watchlist.stocks:
        print("  股票池为空，请使用 pool add <code> 添加股票")
        return

    print(f"{'状态':<6} {'代码':<10} {'名称':<12} {'添加日期'}")
    print("-" * 50)
    for s in watchlist.stocks:
        status = "🟢" if s.enabled else "⚪"
        print(f"{status:<6} {s.code:<10} {s.name:<12} {s.added_at}")

    print(f"\n共 {len(watchlist.stocks)} 只股票，"
          f"{len(watchlist.get_enabled_stocks())} 只启用监控")


def cmd_pool_add(code: str, name: str = ""):
    """添加股票到池中"""
    store = WatchlistStore()
    watchlist = store.load()

    # 自动获取名称
    if not name:
        tx = TxStock()
        name = tx.get_name(code)
        logger.info(f"自动获取股票名称: {name}")

    # 添加
    if watchlist.find_by_code(code):
        print(f"⚠️  {code} 已在股票池中")
        return

    watchlist.add_stock(code, name or code)
    if store.save(watchlist):
        print(f"✅ 已添加 {name} ({code}) 到股票池")
    else:
        print(f"❌ 添加失败")


def cmd_pool_remove(code: str):
    """从池中删除股票"""
    store = WatchlistStore()
    watchlist = store.load()

    if not watchlist.find_by_code(code):
        print(f"⚠️  {code} 不在股票池中")
        return

    watchlist.remove_stock(code)
    if store.save(watchlist):
        print(f"✅ 已从股票池删除 {code}")
    else:
        print(f"❌ 删除失败")


def cmd_pool_enable(code: str, enable: bool = True):
    """启用/禁用股票监控"""
    store = WatchlistStore()
    watchlist = store.load()

    if not watchlist.find_by_code(code):
        print(f"⚠️  {code} 不在股票池中")
        return

    if enable:
        watchlist.enable_stock(code)
        print(f"✅ 已启用 {code} 的监控")
    else:
        watchlist.disable_stock(code)
        print(f"✅ 已禁用 {code} 的监控")

    store.save(watchlist)


def cmd_pool_settings(args):
    """查看/修改设置"""
    store = WatchlistStore()
    watchlist = store.load()
    settings = watchlist.settings

    if args.auto_trade is not None:
        settings.auto_trade = args.auto_trade.lower() == "true"
        print(f"✅ auto_trade = {settings.auto_trade}")

    if args.notify_only is not None:
        settings.notify_only = args.notify_only.lower() == "true"
        print(f"✅ notify_only = {settings.notify_only}")

    if args.max_positions is not None:
        settings.max_positions = int(args.max_positions)
        print(f"✅ max_positions = {settings.max_positions}")

    if args.total_capital is not None:
        settings.total_capital = float(args.total_capital)
        print(f"✅ total_capital = {settings.total_capital}")

    # 显示当前设置
    print("\n📋 当前设置")
    print("=" * 50)
    print(f"  auto_trade:    {settings.auto_trade}")
    print(f"  notify_only:   {settings.notify_only}")
    print(f"  max_positions: {settings.max_positions}")
    print(f"  total_capital: ¥{settings.total_capital:,.0f}")

    store.save(watchlist)


# ==================== 分析 ====================

def cmd_analyze(code: str = "", pool: bool = False):
    """分析单只股票或股票池"""
    tx = TxStock()
    counter = SignalCounter()
    market_filter = get_market_filter()
    market_status, market_change = market_filter.get_market_status()

    codes = []
    if pool:
        store = WatchlistStore()
        watchlist = store.load()
        codes = [s.code for s in watchlist.get_enabled_stocks()]
        if not codes:
            print("⚠️  股票池为空或无启用的股票")
            return
        print(f"\n📊 开始分析股票池 ({len(codes)} 只)")
        print(f"大盘状态: {market_status.value} ({market_change:+.2f}%)")
        print("=" * 60)
    elif code:
        codes = [code]
    else:
        print("⚠️  请指定股票代码或使用 --pool")
        return

    # 获取大盘状态
    results = []
    for c in codes:
        print(f"\n🔍 分析 {c}...", end=" ", flush=True)

        # 获取数据
        history = tx.get_history(c, days=60)
        realtime = tx.get_realtime(c)

        if not realtime:
            print(f"❌ 获取数据失败")
            continue

        name = realtime.get("name", c)
        realtime["market_change_pct"] = market_change

        # 计算信号
        signal = counter.count_signals(history, realtime, market_status)
        results.append(signal)

        # 打印结果
        print(f"✅")
        print_result(signal)

    # 汇总
    if results:
        print("\n" + "=" * 60)
        print("📊 汇总")
        buy_count = sum(1 for r in results if r.decision.value == "BUY")
        hold_count = sum(1 for r in results if r.decision.value == "HOLD")
        sell_count = sum(1 for r in results if r.decision.value == "SELL")
        watch_count = sum(1 for r in results if r.decision.value == "WATCH")
        print(f"  🟢 买入: {buy_count} 只")
        print(f"  🟡 持有: {hold_count} 只")
        print(f"  🔴 卖出: {sell_count} 只")
        print(f"  ⚪ 观望: {watch_count} 只")

        # 发送飞书通知
        notifier = get_feishu_notifier()
        if notifier.enabled:
            notifier.send_signal_report(results)

    return results


def print_result(s):
    """打印单只股票分析结果"""
    print(f"\n  📈 {s.name} ({s.code})  ¥{s.price:.2f} ({s.change_pct:+.2f}%)")
    print(f"  {'─'*50}")

    # 均线
    print(f"  均线: MA5={s.ma5:.2f} MA10={s.ma10:.2f} MA20={s.ma20:.2f}")
    trend_emoji = s.get_trend_emoji()
    print(f"  趋势: {trend_emoji} {s.trend_status.value}")

    # MACD
    macd_str = f"DIF={s.macd_dif:.4f} DEA={s.macd_dea:.4f}"
    print(f"  MACD: {macd_str}")

    # RSI
    print(f"  RSI:  {s.rsi_6:.1f}")

    # ATR
    print(f"  ATR:  {s.atr:.4f} → 止损¥{s.atr_stop_loss:.2f} 止盈¥{s.take_profit_price:.2f}")

    # 信号
    print(f"\n  🟢 买入信号: {s.buy_count}/10")
    if s.buy_signals_detail:
        for sig in s.buy_signals_detail[:5]:
            print(f"     ✅ {sig}")
    else:
        print(f"     (无)")

    print(f"  🔴 卖出信号: {s.sell_count}/6")
    if s.sell_signals_detail:
        for sig in s.sell_signals_detail[:3]:
            print(f"     ⚠️ {sig}")
    else:
        print(f"     (无)")

    # 决策
    emoji = s.get_decision_emoji()
    decision_map = {
        "BUY": "🟢 买入",
        "HOLD": "🟡 持有",
        "SELL": "🔴 卖出",
        "WATCH": "⚪ 观望",
        "STOP_LOSS": "🚨 止损",
        "TAKE_PROFIT": "🎯 止盈",
    }
    decision_str = decision_map.get(s.decision.value, s.decision.value)
    print(f"\n  💡 决策: {emoji} {decision_str}")
    if s.decision.value == "BUY" and s.position_ratio > 0:
        print(f"     建议仓位: {int(s.position_ratio * 100)}%")


# ==================== 监控 ====================

def cmd_watch(continuous: bool = False):
    """启动实时监控"""
    config = get_config()
    store = WatchlistStore()
    watchlist = store.load()

    if not watchlist.get_enabled_stocks():
        print("⚠️  股票池为空或无启用的股票，请先使用 pool add 添加")
        return

    print("=" * 60)
    print("🚀 A股信号灯 · 实时监控")
    print(f"监控股票: {[s.code for s in watchlist.get_enabled_stocks()]}")
    print(f"自动交易: {'✅ 开启' if config.auto_trade else '❌ 关闭'}")
    print(f"只推不交易: {'✅ 是' if config.notify_only else '❌ 否'}")
    print("=" * 60)

    # 检查时间
    now = datetime.now()
    now_time = now.time()

    # 开仓窗口判断
    open_start = datetime.strptime(config.open_window_start, "%H:%M").time()
    open_end = datetime.strptime(config.open_window_end, "%H:%M").time()

    in_window = open_start <= now_time <= open_end
    print(f"\n当前时间: {now.strftime('%H:%M:%S')}")
    print(f"开仓窗口: {config.open_window_start}-{config.open_window_end}")
    print(f"是否在窗口: {'✅ 是' if in_window else '❌ 否'}")
    print()

    if not continuous and not in_window:
        print("⚠️  当前不在开仓时间窗口(14:30-15:00)，watch模式将在指定时间自动执行")
        print("   如需持续测试，可使用: watch --continuous")

    # 执行扫描
    _run_watch_scan(watchlist, manual=True)


def _run_watch_scan(watchlist, manual: bool = False):
    """执行一次watch扫描"""
    config = get_config()
    tx = TxStock()
    counter = SignalCounter()
    market_filter = get_market_filter()
    market_status, market_change = market_filter.get_market_status()

    print(f"\n{'='*60}")
    print(f"🔍 扫描时间: {datetime.now().strftime('%H:%M:%S')} | 大盘: {market_status.value}({market_change:+.2f}%)")
    print(f"{'='*60}")

    signals = []
    for entry in watchlist.get_enabled_stocks():
        code = entry.code
        print(f"\n📊 {entry.name} ({code})...", end=" ", flush=True)

        history = tx.get_history(code, days=60)
        realtime = tx.get_realtime(code)

        if not history or not realtime:
            print("❌ 数据获取失败")
            continue

        realtime["market_change_pct"] = market_change
        signal = counter.count_signals(history, realtime, market_status)
        signals.append(signal)
        print_result(signal)

        # 如果开启了自动交易且有操作信号
        if config.auto_trade and not config.notify_only:
            _handle_auto_trade(signal, history, realtime, market_status)

    # 汇总
    if signals:
        _print_watch_summary(signals)

        # 发送飞书通知
        notifier = get_feishu_notifier()
        if notifier.enabled:
            notifier.send_signal_report(signals)


def _handle_auto_trade(signal, history, realtime, market_status):
    """处理自动交易"""
    from models.position import PositionStore, Position
    from indicators.atr import calc_atr, calc_atr_stop_loss
    import uuid

    config = get_config()
    position_store = PositionStore()
    checker = PreTradeChecker()

    code = signal.code
    price = signal.price
    decision = signal.decision.value

    # 查找持仓
    position = position_store.find_position(code)

    if decision == "BUY" and not position:
        # 准备买入
        # 计算仓位
        position_ratio = signal.position_ratio
        amount = config.total_capital * position_ratio
        # 向下取整到百元
        amount = int(amount // 100) * 100
        if amount < 1000:
            return  # 金额太小不交易
        quantity = amount // (price * 100)  # 手数
        if quantity < 1:
            return

        # 交易前检查
        result = checker.check("BUY", code, price, quantity, amount,
                             market_status, signal.market_change_pct)

        if result.passed:
            print(f"\n  🤖 自动交易检查通过，准备买入 {quantity} 手")
            # 创建持仓记录（模拟撮合）
            atr = signal.atr
            stop_loss = calc_atr_stop_loss(price, atr) if atr > 0 else price * 0.95

            pos = Position(
                id=str(uuid.uuid4()),
                code=code,
                name=signal.name,
                buy_date=datetime.now().strftime("%Y-%m-%d"),
                buy_price=price,
                quantity=quantity,
                cost=amount,
                stop_loss=stop_loss,
                atr=atr,
                latest_buy_signals=signal.buy_count,
                latest_sell_signals=signal.sell_count,
            )
            pos.update_current(price)

            positions = position_store.load()
            positions.append(pos)
            position_store.save(positions)

            # 记录交易
            trade = TradeRecord(
                id=str(uuid.uuid4()),
                code=code,
                name=signal.name,
                action="BUY",
                price=price,
                quantity=quantity,
                amount=amount,
                commission=amount * 0.00025,
                stamp_tax=0,
                buy_signals=signal.buy_count,
                sell_signals=signal.sell_count,
                atr=atr,
                stop_loss=stop_loss,
                position_id=pos.id,
                pre_check_passed=True,
                created_at=datetime.now().isoformat(),
                trade_date=datetime.now().strftime("%Y-%m-%d"),
            )
            TradeStore().add(trade)

            print(f"  ✅ 已建仓: {quantity}手 @{price:.2f}，止损¥{stop_loss:.2f}")

            # 发送通知
            notifier = get_feishu_notifier()
            if notifier.enabled:
                notifier.send_trade_notification(trade.__dict__ if hasattr(trade, '__dict__') else {})

        else:
            print(f"\n  ⚠️ 自动交易检查未通过: {result.summary()}")


def _print_watch_summary(signals: list):
    """打印watch汇总"""
    decisions = {}
    for s in signals:
        d = s.decision.value
        decisions[d] = decisions.get(d, 0) + 1

    print(f"\n{'='*60}")
    print("📊 扫描汇总")
    print(f"  分析股票: {len(signals)} 只")
    print(f"  🟢 买入: {decisions.get('BUY', 0)} 只")
    print(f"  🟡 持有: {decisions.get('HOLD', 0)} 只")
    print(f"  🔴 卖出: {decisions.get('SELL', 0)} 只")
    print(f"  ⚪ 观望: {decisions.get('WATCH', 0)} 只")

    buy_signals = [s for s in signals if s.decision.value == "BUY"]
    if buy_signals:
        print(f"\n  💡 建议关注: {', '.join([s.name for s in buy_signals])}")


# ==================== 持仓管理 ====================

def cmd_position(code: str = ""):
    """查看持仓"""
    store = PositionStore()
    positions = store.get_open_positions()
    config = get_config()

    if not positions:
        print("\n📋 当前无持仓")
        return

    print(f"\n📋 持仓情况 ({len(positions)} 只)")
    print("=" * 60)

    total_value = 0
    total_pnl = 0

    for p in positions:
        print(f"\n  {p.name} ({p.code})")
        print(f"    建仓: {p.buy_date} @{p.buy_price:.2f} × {p.quantity}手 = ¥{p.cost:,.0f}")
        print(f"    现价: ¥{p.current_price:.2f} ({p.pnl_pct:+.2f}%)")
        print(f"    盈亏: {p.unrealized_pnl:+,.0f}")
        print(f"    止损: ¥{p.stop_loss:.2f}")
        print(f"    信号: 买{p.latest_buy_signals}/卖{p.latest_sell_signals}")
        total_value += p.cost + p.unrealized_pnl
        total_pnl += p.unrealized_pnl

    total_pnl_pct = (total_pnl / (total_value - total_pnl) * 100) if total_value > total_pnl else 0
    print(f"\n{'='*60}")
    print(f"  总市值: ¥{total_value:,.0f}")
    print(f"  总盈亏: {total_pnl:+,.0f} ({total_pnl_pct:+.2f}%)")
    print(f"  可用资金: ¥{config.total_capital - sum(p.cost for p in positions):,.0f}")


# ==================== 报告 ====================

def cmd_report():
    """收盘复盘报告"""
    position_store = PositionStore()
    trade_store = TradeStore()
    positions = position_store.get_open_positions()
    config = get_config()

    today = datetime.now().strftime("%Y-%m-%d")
    today_trades = trade_store.get_today_trades(today)

    print(f"\n📋 收盘复盘报告 {today}")
    print("=" * 60)

    # 今日交易
    print(f"\n📊 今日交易 ({len(today_trades)} 笔)")
    if today_trades:
        for t in today_trades:
            action_emoji = {"BUY": "🟢", "SELL": "🔴", "STOP_LOSS": "🚨"}.get(t.action, "📋")
            print(f"  {action_emoji} {t.action} {t.name} @{t.price:.2f} × {t.quantity}手")
    else:
        print("  (无交易)")

    # 持仓情况
    print(f"\n📋 持仓 ({len(positions)} 只)")
    if positions:
        total_pnl = 0
        for p in positions:
            print(f"  {p.name} ({p.code}): {p.pnl_pct:+.2f}%")
            total_pnl += p.unrealized_pnl
        print(f"  总盈亏: {total_pnl:+,.0f}")
    else:
        print("  (无持仓)")

    # 发送飞书
    notifier = get_feishu_notifier()
    if notifier.enabled and positions:
        portfolio = {
            "total_value": sum(p.cost + p.unrealized_pnl for p in positions),
            "total_pnl": sum(p.unrealized_pnl for p in positions),
            "total_pnl_pct": 0,
        }
        notifier.send_position_report(positions, portfolio)


# ==================== 设置 ====================

def cmd_llm(args):
    """大模型配置和测试"""
    from notification.llm_analyzer import LLMAnalyzer, get_llm_analyzer, reload_llm

    # 子命令处理
    if getattr(args, 'subcommand', None) == 'test':
        # 测试LLM分析
        code = args.code
        print(f"\n🤖 测试LLM分析: {code}")
        print("=" * 50)

        # 先获取信号
        from data_provider.txstock import TxStock
        tx = TxStock()
        history = tx.get_history(code, days=60)
        realtime = tx.get_realtime(code)
        if not history or not realtime:
            print(f"❌ 获取 {code} 数据失败")
            return

        from indicators.signal_counter import SignalCounter
        counter = SignalCounter()
        signal = counter.count_signals(history, realtime)

        # 调用LLM
        analyzer = get_llm_analyzer()
        if not analyzer.is_available:
            print("❌ LLM未配置或未启用")
            print("   请先配置: python main.py settings --llm-provider=deepseek --llm-api-key=xxx")
            return

        print(f"📡 使用: {analyzer.get_provider_info()['name']} ({analyzer.model})")
        print(f"📊 股票: {signal.name} ({signal.code}) ¥{signal.price:.2f}")
        print(f"🔍 买入信号: {signal.buy_count}/10 | 卖出信号: {signal.sell_count}/6")
        print(f"⏳ AI分析中...\n")

        analysis = analyzer.analyze_stock(signal.to_dict())
        if analysis:
            print(f"✅ LLM分析结果:\n")
            print(analysis)
        else:
            print("❌ LLM分析失败，请检查API配置")
        return

    if getattr(args, 'subcommand', None) == 'status':
        # 查看状态
        analyzer = get_llm_analyzer()
        info = analyzer.get_provider_info()
        print("\n🤖 LLM 状态")
        print("=" * 50)
        print(f"  启用状态: {'✅ 开启' if info['enabled'] else '❌ 关闭'}")
        print(f"  Provider: {info['name']}")
        print(f"  模型: {info['model']}")
        print(f"  API可用: {'✅' if info['is_available'] else '❌'}")
        if not info['is_available']:
            print("\n  💡 请配置: python main.py settings --llm-enabled=true --llm-provider=deepseek --llm-api-key=xxx")
        return

    # 无子命令时显示帮助
    print("\n🤖 LLM 大模型配置")
    print("=" * 50)
    print("  python main.py llm status                      # 查看LLM状态")
    print("  python main.py llm test --code 000629          # 测试AI分析")
    print()
    print("  💡 配置LLM请使用:")
    print("  python main.py settings --llm-enabled=true")
    print("  python main.py settings --llm-provider=deepseek")
    print("  python main.py settings --llm-api-key=sk-xxx")
    print()
    print("支持的Provider: deepseek / zhipu / doubao / qwen / minimax / openai")




def cmd_settings(args):
    """查看/修改设置"""
    config = get_config()

    print("\n⚙️  当前配置")
    print("=" * 50)
    print(f"  自动交易:    {config.auto_trade}")
    print(f"  只推不交易:  {config.notify_only}")
    print(f"  最大持仓:    {config.max_positions}")
    print(f"  总本金:      ¥{config.total_capital:,.0f}")
    print(f"  单笔限额:    ¥{config.single_trade_limit:,.0f}")
    print(f"  止损线:      {config.stop_loss_pct}%")
    print(f"  止盈线:      {config.take_profit_pct}%")
    print(f"  ATR倍数:     {config.atr_stop_multiplier}×")
    print(f"  开仓窗口:    {config.open_window_start}-{config.open_window_end}")
    print(f"  大盘暴跌线:  {config.market_crash_threshold}%")
    print(f"  飞书通知:    {'✅' if config.feishu_webhook_url else '❌'}")
    print()
    print("🤖 大模型配置")
    print("=" * 50)
    from notification.llm_analyzer import LLMAnalyzer
    providers = LLMAnalyzer.list_providers()
    current_info = LLMAnalyzer().get_provider_info()
    print(f"  AI增强:      {'✅ 开启' if config.llm_enabled else '❌ 关闭'}")
    print(f"  Provider:    {current_info['name']} ({config.llm_provider})")
    print(f"  模型:        {config.llm_model}")
    print(f"  API Key:    {'✅ 已配置' if config.llm_api_key else '❌ 未配置'}")
    print(f"  Base URL:    {config.llm_base_url or '(使用默认)'}")
    print(f"  支持的Provider:")
    for k, v in providers.items():
        marker = " ← 当前" if k == config.llm_provider else ""
        print(f"    - {k}: {v}{marker}")
    print()

    if any([args.auto_trade, args.notify_only, args.max_positions,
            args.total_capital, args.stop_loss, args.take_profit,
            getattr(args, 'llm_enabled', None),
            getattr(args, 'llm_provider', None),
            getattr(args, 'llm_model', None),
            getattr(args, 'llm_api_key', None),
            getattr(args, 'llm_base_url', None)]):
        # 更新 .env 文件
        _update_env(args)
        reload_config()
        print("✅ 配置已更新")


def _update_env(args):
    """更新.env文件"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        # 创建一个
        with open(env_path, "w") as f:
            f.write("# astock-signal 配置\n")

    # 读取现有配置
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip()

    # 更新
    if args.auto_trade is not None:
        env_vars["AUTO_TRADE"] = args.auto_trade.lower()
    if args.notify_only is not None:
        env_vars["NOTIFY_ONLY"] = args.notify_only.lower()
    if args.max_positions is not None:
        env_vars["MAX_POSITIONS"] = args.max_positions
    if args.total_capital is not None:
        env_vars["TOTAL_CAPITAL"] = args.total_capital
    if args.stop_loss is not None:
        env_vars["STOP_LOSS_PCT"] = args.stop_loss
    if args.take_profit is not None:
        env_vars["TAKE_PROFIT_PCT"] = args.take_profit
    if getattr(args, 'llm_enabled', None) is not None:
        env_vars["LLM_ENABLED"] = args.llm_enabled.lower()
    if getattr(args, 'llm_provider', None) is not None:
        env_vars["LLM_PROVIDER"] = args.llm_provider
    if getattr(args, 'llm_model', None) is not None:
        env_vars["LLM_MODEL"] = args.llm_model
    if getattr(args, 'llm_api_key', None) is not None:
        env_vars["LLM_API_KEY"] = args.llm_api_key
    if getattr(args, 'llm_base_url', None) is not None:
        env_vars["LLM_BASE_URL"] = args.llm_base_url

    # 写回
    with open(env_path, "w") as f:
        f.write("# astock-signal 配置\n")
        for k, v in env_vars.items():
            f.write(f"{k}={v}\n")


# ==================== 主程序 ====================

def main():
    parser = argparse.ArgumentParser(description="A股信号灯", prog="astock-signal")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # pool 子命令
    pool_parser = subparsers.add_parser("pool", help="股票池管理")
    pool_sub = pool_parser.add_subparsers(dest="subcommand")

    p_list = pool_sub.add_parser("list", help="查看股票池")
    p_add = pool_sub.add_parser("add", help="添加股票")
    p_add.add_argument("code", help="股票代码")
    p_add.add_argument("--name", help="股票名称(可选)")

    p_remove = pool_sub.add_parser("remove", help="删除股票")
    p_remove.add_argument("code", help="股票代码")

    p_enable = pool_sub.add_parser("enable", help="启用监控")
    p_enable.add_argument("code", help="股票代码")

    p_disable = pool_sub.add_parser("disable", help="禁用监控")
    p_disable.add_argument("code", help="股票代码")

    p_settings = pool_sub.add_parser("settings", help="查看/修改设置")
    p_settings.add_argument("--auto-trade", dest="auto_trade", help="自动交易开关")
    p_settings.add_argument("--notify-only", dest="notify_only", help="只推送不交易")
    p_settings.add_argument("--max-positions", dest="max_positions", help="最大持仓数")
    p_settings.add_argument("--total-capital", dest="total_capital", help="总本金")

    # analyze 子命令
    analyze_parser = subparsers.add_parser("analyze", help="分析股票")
    analyze_parser.add_argument("code", nargs="?", help="股票代码")
    analyze_parser.add_argument("--pool", action="store_true", help="分析股票池")

    # watch 子命令
    watch_parser = subparsers.add_parser("watch", help="实时监控")
    watch_parser.add_argument("--continuous", action="store_true", help="持续监控")

    # position 子命令
    subparsers.add_parser("position", help="查看持仓")

    # report 子命令
    subparsers.add_parser("report", help="收盘复盘")

    # llm 子命令
    llm_parser = subparsers.add_parser("llm", help="大模型配置")
    llm_sub = llm_parser.add_subparsers(dest="subcommand")

    llm_test = llm_sub.add_parser("test", help="测试LLM配置")
    llm_test.add_argument("--code", default="000629", help="测试分析的股票代码")

    llm_status = llm_sub.add_parser("status", help="查看LLM状态")

    # settings 子命令
    settings_parser = subparsers.add_parser("settings", help="系统设置")
    settings_parser.add_argument("--auto-trade", dest="auto_trade")
    settings_parser.add_argument("--notify-only", dest="notify_only")
    settings_parser.add_argument("--max-positions", dest="max_positions")
    settings_parser.add_argument("--total-capital", dest="total_capital")
    settings_parser.add_argument("--stop-loss", dest="stop_loss")
    settings_parser.add_argument("--take-profit", dest="take_profit")
    settings_parser.add_argument("--llm-enabled", dest="llm_enabled")
    settings_parser.add_argument("--llm-provider", dest="llm_provider")
    settings_parser.add_argument("--llm-model", dest="llm_model")
    settings_parser.add_argument("--llm-api-key", dest="llm_api_key")
    settings_parser.add_argument("--llm-base-url", dest="llm_base_url")

    args = parser.parse_args()

    # 确保数据目录存在
    os.makedirs("data", exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    # 执行对应命令
    if args.command == "pool":
        if args.subcommand == "list":
            cmd_pool_list()
        elif args.subcommand == "add":
            cmd_pool_add(args.code, getattr(args, "name", ""))
        elif args.subcommand == "remove":
            cmd_pool_remove(args.code)
        elif args.subcommand == "enable":
            cmd_pool_enable(args.code, True)
        elif args.subcommand == "disable":
            cmd_pool_enable(args.code, False)
        elif args.subcommand == "settings":
            cmd_pool_settings(args)
        else:
            cmd_pool_list()

    elif args.command == "analyze":
        cmd_analyze(args.code or "", args.pool)

    elif args.command == "watch":
        cmd_watch(args.continuous)

    elif args.command == "position":
        cmd_position()

    elif args.command == "report":
        cmd_report()

    elif args.command == "settings":
        cmd_settings(args)

    elif args.command == "llm":
        cmd_llm(args)

    else:
        # 默认显示帮助
        parser.print_help()
        print("\n💡 快速开始:")
        print("  1. python main.py pool add 000629      # 添加股票")
        print("  2. python main.py pool list            # 查看股票池")
        print("  3. python main.py analyze --pool       # 分析股票池")
        print("  4. python main.py watch                 # 启动实时监控")


if __name__ == "__main__":
    main()
