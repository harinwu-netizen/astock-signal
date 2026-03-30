# -*- coding: utf-8 -*-
"""
交易复盘报告单元测试
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from unittest.mock import patch, MagicMock
from monitor.reporter import Reporter


class FakeTrade:
    def __init__(self, code, name, action, price, quantity, pnl, buy_signals, sell_signals, trade_date, reason=""):
        self.code = code
        self.name = name
        self.action = action
        self.price = price
        self.quantity = quantity
        self.pnl = pnl
        self.buy_signals = buy_signals
        self.sell_signals = sell_signals
        self.trade_date = trade_date
        self.reason = reason


class FakePosition:
    def __init__(self, code, name, buy_price, buy_date, stop_loss, atr, buy_sigs, sell_sigs):
        self.code = code
        self.name = name
        self.buy_price = buy_price
        self.buy_date = buy_date
        self.stop_loss = stop_loss
        self.atr = atr
        self.latest_buy_signals = buy_sigs
        self.latest_sell_signals = sell_sigs
        self.current_price = buy_price * 1.05
        self.pnl_pct = 5.0
        self.status = "open"


def test_signal_effectiveness():
    """信号有效性统计正确"""
    r = Reporter()

    trades = [
        FakeTrade("000629", "钒钛", "BUY", 3.5, 10, 200, 6, 1, "2026-03-25"),
        FakeTrade("000629", "钒钛", "SELL", 3.7, 10, 200, 4, 2, "2026-03-26"),
        FakeTrade("600519", "茅台", "BUY", 1600, 1, -50, 5, 2, "2026-03-25"),
        FakeTrade("600519", "茅台", "SELL", 1595, 1, -50, 3, 3, "2026-03-26"),
    ]

    stats = r._calc_signal_effectiveness(trades, lookback_days=5)

    assert "买6" in stats, f"买6信号应有统计，实际: {stats.keys()}"
    assert "买5" in stats, f"买5信号应有统计"
    assert stats["买6"]["win_rate"] == 100.0, f"买6胜率应为100%，实际{stats['买6']['win_rate']}%"
    assert stats["买5"]["win_rate"] == 0.0, f"买5胜率应为0%，实际{stats['买5']['win_rate']}%"
    assert stats["买6"]["count"] == 1
    assert stats["买5"]["count"] == 1
    print(f"✅ test_signal_effectiveness: {stats}")


def test_signal_effectiveness_no_trades():
    """无交易时返回空"""
    r = Reporter()
    stats = r._calc_signal_effectiveness([], lookback_days=5)
    assert stats == {}
    print("✅ test_signal_effectiveness_no_trades")


def test_ai_prompt_build():
    """AI复盘Prompt构建"""
    with patch('monitor.reporter.get_feishu_notifier') as mf:
        feishu = MagicMock()
        feishu.enabled = False
        mf.return_value = feishu

        with patch('monitor.reporter.get_llm_analyzer') as ml:
            llm = MagicMock()
            llm.is_available = False
            ml.return_value = llm

            r = Reporter()

            trades = [
                FakeTrade("000629", "钒钛", "BUY", 3.5, 10, 200, 6, 1, "2026-03-25", "买入信号6"),
            ]
            positions = [
                FakePosition("000629", "钒钛", 3.5, "2026-03-25", 3.3, 0.05, 5, 1),
            ]
            stats = {"买6": {"win_rate": 100.0, "avg_pnl": 200.0, "count": 1}}

            prompt = r._build_ai_prompt("2026-03-30", trades, positions, stats)

            assert "钒钛" in prompt
            assert "买信号6" in prompt
            assert "每日交易复盘" in prompt
            assert "操作建议" in prompt  # 包含操作建议
            print(f"✅ test_ai_prompt_build: Prompt长度={len(prompt)}")


if __name__ == "__main__":
    test_signal_effectiveness()
    test_signal_effectiveness_no_trades()
    test_ai_prompt_build()
    print()
    print("🎉 Reporter 所有测试通过!")
