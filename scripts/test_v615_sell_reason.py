#!/usr/bin/env python3
"""
v6.15 止损决策单元测试
- 覆盖 4 个止损条件 × 4 种市况(强/震/弱/边缘)
- 验证 max_hold_days bug 修复
- 验证 WEAK 字符串比较 bug 修复
- 验证持仓 002202(今天刚建仓,弱市,hold_days=0)不会被误卖
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timedelta
from unittest.mock import MagicMock

from models.position import Position
from models.signal import MarketStatus, Signal
from monitor.watcher import Watcher


# ---- 测试工具 ----
def make_position(
    code="sz002202", name="金风科技", buy_date=None, buy_price=20.99,
    stop_loss=20.36, stop_loss_pct=2.0, market_regime=MarketStatus.WEAK,
):
    """造一个 Position(不从 JSON 反序列化,直接造内存对象)"""
    if buy_date is None:
        buy_date = (datetime.now() - timedelta(days=0)).strftime("%Y-%m-%d")
    p = Position(
        id="test-id", code=code, name=name,
        buy_date=buy_date, buy_price=buy_price,
        quantity=142.0, cost=298058.0,
        stop_loss=stop_loss, stop_loss_pct=stop_loss_pct,
        market_regime=market_regime,
    )
    return p


def make_signal(code="sz002202", price=20.99, rsi_6=50.0):
    """造一个 Signal(只设关键字段,helper 只用 price 和 rsi_6)"""
    s = Signal.__new__(Signal)  # 绕过 __init__
    s.code = code
    s.name = ""
    s.price = price
    s.rsi_6 = rsi_6
    return s


def make_cfg():
    """造一个 cfg mock(只设 helper 用到的 max_hold 字段)"""
    cfg = MagicMock()
    cfg.weak_max_hold_days = 3
    cfg.strong_max_hold_days = 30
    cfg.consolidate_max_hold_days = 10
    cfg.weak_rsi_sell_threshold = 75.0  # v6.17: 阈值从 65 调到 75
    return cfg


# 不需要 init Watcher 全套,直接用 unbound method
def call_decide(position, sig, cfg):
    return Watcher._decide_sell_reason(None, position, sig, cfg)


# ---- 测试 ----
def test_001_atr_stop_loss():
    """条件1: ATR 追踪止损"""
    p = make_position(stop_loss=21.0, market_regime=MarketStatus.WEAK)
    sig = make_signal(price=20.5)  # < 21.0
    result = call_decide(p, sig, make_cfg())
    assert result and "ATR追踪止损" in result, f"应触发 ATR, 实际: {result}"
    print(f"✅ test_001 ATR 止损: {result}")


def test_002_hold_days_strong():
    """条件2: 持仓超期(强市,31 天)"""
    p = make_position(
        buy_date=(datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d"),
        market_regime=MarketStatus.STRONG,
    )
    sig = make_signal(price=20.99)
    result = call_decide(p, sig, make_cfg())
    assert result and "持仓超期" in result, f"应触发持仓超期, 实际: {result}"
    assert "31天≥30天" in result, f"文案应显示 31/30, 实际: {result}"
    print(f"✅ test_002 强市持仓超期: {result}")


def test_003_hold_days_consolidate():
    """条件2: 持仓超期(震荡市,11 天)"""
    p = make_position(
        buy_date=(datetime.now() - timedelta(days=11)).strftime("%Y-%m-%d"),
        market_regime=MarketStatus.CONSOLIDATE,
    )
    sig = make_signal(price=20.99)
    result = call_decide(p, sig, make_cfg())
    assert result and "持仓超期" in result, f"应触发持仓超期, 实际: {result}"
    assert "11天≥10天" in result, f"文案应显示 11/10, 实际: {result}"
    print(f"✅ test_003 震荡市持仓超期: {result}")


def test_004_hold_days_weak_disabled():
    """条件2: 弱市禁用持仓超期(13 天 > 3 天 不应触发)"""
    p = make_position(
        buy_date=(datetime.now() - timedelta(days=13)).strftime("%Y-%m-%d"),
        market_regime=MarketStatus.WEAK,
    )
    sig = make_signal(price=20.99, rsi_6=50.0)  # RSI 不高,不触发条件4
    result = call_decide(p, sig, make_cfg())
    # 弱市 13 天不触发超期 → 也不触发 RSI → 应返回 None
    assert result is None, f"弱市禁用超期,应返回 None, 实际: {result}"
    print(f"✅ test_004 弱市超期禁用: {result}")


def test_005_loss_limit():
    """条件3: 亏损超限(-3% 触发 -2% 止损)"""
    p = make_position(buy_price=21.0, stop_loss_pct=2.0, market_regime=MarketStatus.WEAK)
    sig = make_signal(price=20.37)  # -3% 左右
    result = call_decide(p, sig, make_cfg())
    assert result and "亏损超限" in result, f"应触发亏损超限, 实际: {result}"
    print(f"✅ test_005 亏损超限: {result}")


def test_006_weak_rsi_exit():
    """条件4: 弱市 RSI 反弹到位(80 > 75)"""
    p = make_position(market_regime=MarketStatus.WEAK)
    sig = make_signal(price=20.99, rsi_6=80.0)
    result = call_decide(p, sig, make_cfg())
    assert result and "弱市RSI反弹到位" in result, f"应触发 RSI 反弹, 实际: {result}"
    print(f"✅ test_006 弱市 RSI 反弹: {result}")


def test_006b_weak_rsi_below_threshold():
    """v6.17: RSI 阈值 65→75 后, RSI_6=70 不再触发(避免 6/18 金风 RSI_6=89 误触场景的回测覆盖)"""
    p = make_position(market_regime=MarketStatus.WEAK)
    sig = make_signal(price=20.99, rsi_6=70.0)  # 阈值 75 以下
    result = call_decide(p, sig, make_cfg())
    assert result is None, f"RSI_6=70 在阈值 75 下不应触发, 实际: {result}"
    print(f"✅ test_006b RSI_6=70 不触发(v6.17 新阈值 75)")


def test_007_no_trigger():
    """场景: 002202 今天刚建仓,弱市,hold_days=0,价格正常,RSI 正常 → None"""
    p = make_position(
        buy_date=datetime.now().strftime("%Y-%m-%d"),
        stop_loss=20.36,  # 当前价 20.99 > 止损
        stop_loss_pct=2.0,
        market_regime=MarketStatus.WEAK,
    )
    sig = make_signal(price=20.99, rsi_6=50.0)
    result = call_decide(p, sig, make_cfg())
    assert result is None, f"002202 当前应不触发, 实际: {result}"
    print(f"✅ test_007 002202 当前不触发: {result}")


def test_008_strong_rsi_exit_disabled():
    """条件4: 强市 RSI=70 不应触发弱市 RSI 反弹"""
    p = make_position(market_regime=MarketStatus.STRONG)
    sig = make_signal(price=20.99, rsi_6=70.0)
    result = call_decide(p, sig, make_cfg())
    assert result is None, f"强市不应触发 RSI 反弹, 实际: {result}"
    print(f"✅ test_008 强市 RSI 反弹不触发: {result}")


def test_009_priority_atr_beats_hold_days():
    """优先级: ATR 止损优先级最高(即使超期,价格先到止损)"""
    p = make_position(
        buy_date=(datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d"),
        stop_loss=21.0,  # 当前价 < 21.0
        market_regime=MarketStatus.STRONG,
    )
    sig = make_signal(price=20.5)
    result = call_decide(p, sig, make_cfg())
    assert result and "ATR追踪止损" in result, f"应优先 ATR, 实际: {result}"
    print(f"✅ test_009 ATR 优先于持仓超期: {result}")


def test_010_real_002202_dict():
    """场景: 用 002202 真实 JSON 数据(从 from_dict 反序列化)"""
    import json
    d = {
        "id": "e3e27861-5e34-47ff-b69c-b6aa050ad351",
        "code": "sz002202", "name": "金风科技",
        "buy_date": "2026-06-15", "buy_price": 20.99,
        "quantity": 142.0, "cost": 298058.0,
        "current_price": 20.99, "unrealized_pnl": 0.0, "pnl_pct": 0.0,
        "stop_loss": 20.36, "take_profit": 22.6692, "trailing_stop": 20.36,
        "latest_buy_signals": 1, "latest_sell_signals": 0,
        "latest_rebound_signals": 1, "latest_trend_signals": 0,
        "market_regime": "弱势",  # 关键: 字符串
        "atr": 0.9782, "atr_multiplier": 2.0,
        "stop_loss_pct": 2.0, "take_profit_pct": 8.0, "ma20_take_profit": 23.3395,
        "status": "open", "closed_at": "", "closed_reason": ""
    }
    p = Position.from_dict(d)
    sig = make_signal(price=20.99, rsi_6=50.0)
    result = call_decide(p, sig, make_cfg())
    # 关键: market_regime 是从 string 反序列化,is not WEAK 应该正确
    assert p.market_regime is MarketStatus.WEAK, f"反序列化类型错: {type(p.market_regime)}"
    assert result is None, f"002202 当前应不触发, 实际: {result}"
    print(f"✅ test_010 002202 真实数据(从 string 反序列化): {result}")


def test_011_strong_hold_days_just_below():
    """边界: 强市 29 天(差 1 天到 30) → 不应触发"""
    p = make_position(
        buy_date=(datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d"),
        market_regime=MarketStatus.STRONG,
    )
    sig = make_signal(price=20.99)
    result = call_decide(p, sig, make_cfg())
    assert result is None, f"29 天差 1 天, 不应触发, 实际: {result}"
    print(f"✅ test_011 强市 29 天边界: {result}")


def test_012_consolidate_hold_days_exact():
    """边界: 震荡 10 天(刚好等于) → 应触发"""
    p = make_position(
        buy_date=(datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"),
        market_regime=MarketStatus.CONSOLIDATE,
    )
    sig = make_signal(price=20.99)
    result = call_decide(p, sig, make_cfg())
    assert result and "持仓超期" in result, f"10=10 应触发, 实际: {result}"
    print(f"✅ test_012 震荡 10 天边界: {result}")


if __name__ == "__main__":
    tests = [
        test_001_atr_stop_loss,
        test_002_hold_days_strong,
        test_003_hold_days_consolidate,
        test_004_hold_days_weak_disabled,
        test_005_loss_limit,
        test_006_weak_rsi_exit,
        test_007_no_trigger,
        test_008_strong_rsi_exit_disabled,
        test_009_priority_atr_beats_hold_days,
        test_010_real_002202_dict,
        test_011_strong_hold_days_just_below,
        test_012_consolidate_hold_days_exact,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"❌ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"💥 {t.__name__}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"通过 {len(tests)-failed}/{len(tests)}")
    if failed == 0:
        print("✅ 全部通过")
        sys.exit(0)
    else:
        print(f"❌ {failed} 个失败")
        sys.exit(1)
