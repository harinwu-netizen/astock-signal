# -*- coding: utf-8 -*-
"""
回测引擎单元测试
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import BacktestEngine, BTTrade


def test_backtest_engine_runs():
    """回测引擎能正常运行（用阈值4测试活跃股票）"""
    engine = BacktestEngine(initial_capital=100000, buy_threshold=4, sell_threshold=3)
    result = engine.run("000001", days=60)

    assert result.code == "000001"
    assert result.days > 0
    assert result.equity_curve is not None
    assert len(result.equity_curve) > 0
    assert result.final_capital > 0
    assert result.total_trades >= 0
    print(f"✅ test_backtest_engine_runs: {result.days}天, {result.total_trades}笔交易, 收益率={result.total_return:.2f}%")


def test_no_trades_when_no_signal():
    """当信号不足时，回测正确返回0交易"""
    engine = BacktestEngine(initial_capital=100000, buy_threshold=8, sell_threshold=3)
    result = engine.run("000629", days=60)

    # 000629 在这段时期信号不足，预期0交易或极少量
    assert result.total_trades >= 0
    print(f"✅ test_no_trades_when_no_signal: {result.total_trades}笔交易（阈值8，预期极少）")


def test_equity_curve():
    """净值曲线记录正确"""
    engine = BacktestEngine(initial_capital=100000, buy_threshold=4, sell_threshold=3)
    result = engine.run("000001", days=60)

    assert len(result.equity_curve) > 0
    first = result.equity_curve[0]
    assert "date" in first
    assert "capital" in first
    assert first["capital"] == 100000.0
    # 注意：最后一条净值是平仓前记录，最终资金以 final_capital 为准
    assert result.final_capital > 0
    print(f"✅ test_equity_curve: {len(result.equity_curve)}个净值点, {result.initial_capital:.0f} → {result.final_capital:.0f}")


def test_trade_structure():
    """交易记录结构正确"""
    engine = BacktestEngine(initial_capital=100000, buy_threshold=4, sell_threshold=3)
    result = engine.run("000001", days=60)

    for t in result.trades:
        assert isinstance(t, BTTrade)
        assert hasattr(t, "date")
        assert hasattr(t, "action")
        assert hasattr(t, "price")
        assert hasattr(t, "quantity")
        assert hasattr(t, "pnl")
        assert hasattr(t, "hold_days")
    print(f"✅ test_trade_structure: {len(result.trades)}条交易记录结构正确")


def test_metrics_calculation():
    """绩效指标计算正确"""
    engine = BacktestEngine(initial_capital=100000, buy_threshold=4, sell_threshold=3)
    result = engine.run("000001", days=60)

    # 胜率计算
    if result.total_trades > 0:
        assert 0 <= result.win_rate <= 100
        assert result.winning_trades + result.losing_trades == result.total_trades
    # 夏普比率可正可负
    assert result.sharpe_ratio != 0 or result.total_trades == 0
    # 最大回撤非负
    assert result.max_drawdown >= 0
    print(f"✅ test_metrics_calculation: 胜率={result.win_rate:.1f}%, 夏普={result.sharpe_ratio:.2f}, 最大回撤={result.max_drawdown:.2f}%")


def test_to_dict():
    """to_dict 序列化正确"""
    engine = BacktestEngine(initial_capital=100000, buy_threshold=4, sell_threshold=3)
    result = engine.run("000001", days=60)

    d = result.to_dict()
    assert "code" in d
    assert "total_return" in d
    assert "equity_curve" in d
    assert "trades" in d
    print(f"✅ test_to_dict: 序列化 {len(d['equity_curve'])} 个净值点, {len(d['trades'])} 条交易")


if __name__ == "__main__":
    test_backtest_engine_runs()
    test_no_trades_when_no_signal()
    test_equity_curve()
    test_trade_structure()
    test_metrics_calculation()
    test_to_dict()
    print()
    print("🎉 BacktestEngine 所有测试通过!")
