# -*- coding: utf-8 -*-
"""
东方财富数据源单元测试
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_provider.eastmoney import EastMoney


def test_get_history():
    em = EastMoney()
    hist = em.get_history("000629", days=5)
    assert hist is not None, "历史数据不应为空"
    assert len(hist) <= 5, f"应 ≤5 条，实际 {len(hist)}"
    assert all(k in hist[0] for k in ("date", "open", "high", "low", "close", "volume"))
    print(f"✅ get_history: 获取 {len(hist)} 条K线")


def test_get_realtime():
    em = EastMoney()
    rt = em.get_realtime("000629")
    assert rt is not None, "实时行情不应为空"
    assert rt["name"] == "钒钛股份"
    assert rt["price"] > 0
    assert rt["source"] == "东方财富"
    print(f"✅ get_realtime: {rt['name']} ¥{rt['price']}")


def test_batch_realtime():
    em = EastMoney()
    results = em.batch_get_realtime(["000629", "600519"])
    assert len(results) == 2
    assert all(r is not None for r in results)
    print(f"✅ batch_get_realtime: {len(results)} 只")


def test_get_index():
    em = EastMoney()
    indices = {
        "sh000001": "上证指数",
        "sz399001": "深证成指",
        "sz399006": "创业板指",
        "sh000688": "科创50",
    }
    for code, name in indices.items():
        r = em.get_index_realtime(code)
        assert r is not None, f"{code} 不应为空"
        assert r["price"] > 0, f"{code} 价格应>0"
        print(f"✅ get_index_realtime: {r['name']} ¥{r['price']} ({r['change_pct']:+.2f}%)")


def test_interface_compat():
    """验证接口字段与 txstock.py 一致"""
    em = EastMoney()
    rt = em.get_realtime("000629")
    expected_keys = {"code", "name", "price", "prev_close", "open",
                     "high", "low", "volume", "change_pct", "source"}
    assert expected_keys <= set(rt.keys()), f"缺少字段: {expected_keys - set(rt.keys())}"

    hist = em.get_history("000629", days=1)
    hist_keys = {"date", "open", "high", "low", "close", "volume"}
    assert hist_keys <= set(hist[0].keys()), f"缺少字段: {hist_keys - set(hist[0].keys())}"
    print("✅ interface_compat: 字段与 txstock.py 一致")


if __name__ == "__main__":
    test_get_history()
    test_get_realtime()
    test_batch_realtime()
    test_get_index()
    test_interface_compat()
    print()
    print("🎉 所有测试通过!")
