# -*- coding: utf-8 -*-
"""
AI预警系统单元测试
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from dataclasses import dataclass

# 模拟 signal 对象
class FakeSignal:
    def __init__(self, code, buy_count, sell_count, price, decision="WATCH"):
        self.code = code
        self.buy_count = buy_count
        self.sell_count = sell_count
        self.price = price
        self.decision = type('obj', (object,), {'value': decision})()


class FakePosition:
    def __init__(self, code, name, buy_price, buy_date, stop_loss, atr,
                 latest_buy_signals, latest_sell_signals):
        self.code = code
        self.name = name
        self.buy_price = buy_price
        self.buy_date = buy_date
        self.stop_loss = stop_loss
        self.atr = atr
        self.latest_buy_signals = latest_buy_signals
        self.latest_sell_signals = latest_sell_signals
        self.buy_signals = latest_buy_signals  # 兼容 AIAlerter 内部访问


from monitor.ai_alerter import AIAlerter, AI_LOSS_THRESHOLD, AIAlert


def test_loss_threshold():
    """亏损8%阈值正确"""
    assert AI_LOSS_THRESHOLD == 8.0
    print("✅ test_loss_threshold: 亏损阈值=8%")


def test_ai_alert_structure():
    """AIAlert数据结构正确"""
    alert = AIAlert(
        level="DANGER",
        title="测试",
        body="内容",
        positions=["000629"],
    )
    assert alert.level == "DANGER"
    assert alert.title == "测试"
    assert "000629" in alert.positions
    print("✅ test_ai_alert_structure")


def test_loss_alert_trigger():
    """亏损>8%正确触发预警"""
    from unittest.mock import patch, MagicMock

    # Mock PositionStore
    mock_pos = FakePosition(
        code="000629", name="钒钛股份",
        buy_price=3.80, buy_date="2026-03-20",
        stop_loss=3.40, atr=0.05,
        latest_buy_signals=5, latest_sell_signals=1
    )

    # 当前价3.43，亏损 = (3.80-3.43)/3.80 = 9.7% > 8%
    mock_sig = FakeSignal(code="000629", buy_count=5, sell_count=1, price=3.43)

    with patch('monitor.ai_alerter.PositionStore') as MockStore:
        store = MagicMock()
        store.get_open_positions.return_value = [mock_pos]
        MockStore.return_value = store

        with patch('monitor.ai_alerter.get_feishu_notifier') as mock_feishu:
            notifier = MagicMock()
            notifier.enabled = False
            mock_feishu.return_value = notifier

            alerter = AIAlerter()
            # 直接调用亏损检查
            alert = alerter._check_loss_alert(mock_pos, mock_sig)

            assert alert is not None, "亏损9.7%应触发预警"
            assert alert.level == "DANGER"
            assert "9.7%" in alert.body or "9.7" in alert.body
            print(f"✅ test_loss_alert_trigger: 亏损9.7%触发预警({alert.title})")


def test_loss_no_alert_under_threshold():
    """亏损<8%不触发预警"""
    from unittest.mock import patch, MagicMock

    mock_pos = FakePosition(
        code="000629", name="钒钛股份",
        buy_price=3.60, buy_date="2026-03-25",
        stop_loss=3.40, atr=0.05,
        latest_buy_signals=5, latest_sell_signals=1
    )
    # 当前价3.50，亏损 = (3.60-3.50)/3.60 = 2.8% < 8%
    mock_sig = FakeSignal(code="000629", buy_count=5, sell_count=1, price=3.50)

    with patch('monitor.ai_alerter.PositionStore') as MockStore:
        store = MagicMock()
        store.get_open_positions.return_value = [mock_pos]
        MockStore.return_value = store

        with patch('monitor.ai_alerter.get_feishu_notifier') as mock_feishu:
            notifier = MagicMock()
            notifier.enabled = False
            mock_feishu.return_value = notifier

            alerter = AIAlerter()
            alert = alerter._check_loss_alert(mock_pos, mock_sig)
            assert alert is None, f"亏损2.8%不应触发预警，实际{alert}"
            print("✅ test_loss_no_alert_under_threshold: 亏损2.8%未触发")


def test_signal_reverse_trigger():
    """信号多转空正确触发"""
    from unittest.mock import patch, MagicMock

    mock_pos = FakePosition(
        code="000629", name="钒钛股份",
        buy_price=3.43, buy_date="2026-03-25",
        stop_loss=3.20, atr=0.05,
        latest_buy_signals=5, latest_sell_signals=1   # 之前买信号5
    )
    # 当前买信号降到3，恶化超过2
    mock_sig = FakeSignal(code="000629", buy_count=3, sell_count=1, price=3.43)

    with patch('monitor.ai_alerter.PositionStore') as MockStore:
        store = MagicMock()
        store.get_open_positions.return_value = [mock_pos]
        MockStore.return_value = store

        with patch('monitor.ai_alerter.get_feishu_notifier') as mock_feishu:
            notifier = MagicMock()
            notifier.enabled = False
            mock_feishu.return_value = notifier

            alerter = AIAlerter()
            alert = alerter._check_signal_reverse(mock_pos, mock_sig)

            assert alert is not None, "买信号5→3应触发预警"
            assert alert.level == "WARNING"
            assert "信号" in alert.title
            print(f"✅ test_signal_reverse_trigger: {alert.title}")


def test_dedup():
    """预警去重正确"""
    from unittest.mock import patch, MagicMock

    with patch('monitor.ai_alerter.PositionStore') as MockStore:
        store = MagicMock()
        store.get_open_positions.return_value = []
        MockStore.return_value = store

        with patch('monitor.ai_alerter.get_feishu_notifier') as mock_feishu:
            notifier = MagicMock()
            notifier.enabled = False
            mock_feishu.return_value = notifier

            alerter = AIAlerter()

            # 第一次应通过
            assert alerter._should_alert("test:key") is True
            # 同一 key 立即再次调用应被拦截
            assert alerter._should_alert("test:key") is False


if __name__ == "__main__":
    test_loss_threshold()
    test_ai_alert_structure()
    test_loss_alert_trigger()
    test_loss_no_alert_under_threshold()
    test_signal_reverse_trigger()
    test_dedup()
    print()
    print("🎉 AI预警系统所有测试通过!")
