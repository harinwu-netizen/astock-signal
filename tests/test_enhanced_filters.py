# -*- coding: utf-8 -*-
"""
尾盘强化过滤及升级版市场过滤器单元测试
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_provider.data_selector import get_selector
from data_provider.eastmoney import EastMoney
from trading.enhanced_filters import (
    check_volume承接, check板块共振, check个股位置,
    run_all_enhanced_filters, _get_board_history
)
from strategy.market_filter import get_market_filter


def test_board_kline():
    """板块K线获取（东方财富支持BK码）"""
    hist = _get_board_history("BK0479", days=30)
    assert hist is not None and len(hist) >= 25, f"钢铁板块K线获取失败，实际{len(hist) if hist else 0}条"
    print(f"✅ test_board_kline: 获取钢铁板块 {len(hist)} 条K线")


def test_volume_filter():
    """量能承接过滤器"""
    selector = get_selector()
    hist = selector.get_history("000629", days=65)
    result = check_volume承接("000629", hist)
    assert result is not None
    assert not result.passed  # 今日量比通常偏低，应该不通过
    assert "量比" in result.detail
    print(f"✅ test_volume_filter: {result.detail}")


def test_block_filter():
    """板块共振过滤器"""
    selector = get_selector()
    hist = selector.get_history("000629", days=65)
    result = check板块共振("000629", hist)
    assert result is not None
    # 钢铁板块今日涨幅+0.56%，应该通过
    assert result.passed, f"板块共振应通过: {result.detail}"
    assert "钢铁" in result.detail
    print(f"✅ test_block_filter: {result.detail}")


def test_position_filter():
    """个股位置过滤器"""
    selector = get_selector()
    hist = selector.get_history("000629", days=65)
    result = check个股位置("000629", hist)
    assert result is not None
    # 000629 现价应该在 MA60 下方（弱势），应不通过
    assert not result.passed, f"个股位置应不通过: {result.detail}"
    assert "MA60" in result.detail
    print(f"✅ test_position_filter: {result.detail}")


def test_market_filter_multi_index():
    """大盘过滤器-多指数联合判断"""
    mf = get_market_filter()
    status, worst = mf.get_market_status()
    indices = mf.get_multi_index_status()

    assert status is not None
    assert worst is not None
    assert "sh000001" in indices, "缺少上证指数"
    assert "sz399006" in indices, "缺少创业板指"
    assert "sh000688" in indices, "缺少科创50"

    for code, data in indices.items():
        print(f"  {data['name']}: {data['price']:.2f}({data['change_pct']:+.2f}%)")

    print(f"✅ test_market_filter_multi_index: 状态={status.value}, 最大跌幅={worst:+.2f}%")


def test_enhanced_filters_integration():
    """强化过滤器集成测试"""
    selector = get_selector()
    hist = selector.get_history("000629", days=65)

    results = run_all_enhanced_filters("000629", hist)
    assert len(results) == 3
    # 000629: 量能❌ 板块✅ 个股位置❌
    assert results[0].filter_name == "量能承接"
    assert results[1].filter_name == "板块共振"
    assert results[2].filter_name == "个股位置"

    failed = [r for r in results if not r.passed]
    print(f"✅ test_enhanced_filters_integration: {len(failed)}/3 层失败")
    for r in results:
        print(f"  {'❌' if not r.passed else '✅'} {r.filter_name}: {r.detail}")


if __name__ == "__main__":
    test_board_kline()
    test_volume_filter()
    test_block_filter()
    test_position_filter()
    test_market_filter_multi_index()
    test_enhanced_filters_integration()
    print()
    print("🎉 所有测试通过!")
