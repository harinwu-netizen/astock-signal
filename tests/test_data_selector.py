# -*- coding: utf-8 -*-
"""
DataSourceSelector 单元测试
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from data_provider.data_selector import (
    get_selector, reload_selector, DataSourceConfig,
    DataSource, DataSourceSelector
)


def test_normal_operation():
    """正常调用，走主源（腾讯）"""
    reload_selector(DataSourceConfig(provider="auto"))
    sel = get_selector()

    rt = sel.get_realtime("000629")
    assert rt is not None
    assert rt["price"] > 0
    assert rt["source"] == "腾讯财经"
    print(f"✅ get_realtime: {rt['name']} ¥{rt['price']} ({rt['source']})")

    hist = sel.get_history("000629", days=3)
    assert hist is not None and len(hist) <= 3
    print(f"✅ get_history: {[r['date'] for r in hist]}")

    batch = sel.batch_get_realtime(["000629", "600519"])
    assert len(batch) == 2
    print(f"✅ batch_get_realtime: {[r['name'] for r in batch]}")


def test_force_source():
    """强制指定数据源"""
    sel = get_selector()

    sel.force_source("eastmoney")
    rt = sel.get_realtime("000629")
    assert rt["source"] == "东方财富"
    print(f"✅ force eastmoney: {rt['source']}")

    sel.force_source("txstock")
    rt = sel.get_realtime("000629")
    assert rt["source"] == "腾讯财经"
    print(f"✅ force txstock: {rt['source']}")

    sel.force_source("auto")
    rt = sel.get_realtime("000629")
    print(f"✅ force auto: {rt['source']}")


def test_auto_failover():
    """自动故障切换"""
    reload_selector(DataSourceConfig(provider="auto"))
    sel = get_selector()

    # 初始主源
    status = sel.get_status()
    assert status["active"] == "txstock"
    print(f"✅ 初始主源: {status['active']}")

    # 模拟 txstock 连续失败3次
    h = sel._health[DataSource.TXSTOCK]
    for i in range(3):
        h.failure_count = i + 1
        sel._switch_if_needed()

    status = sel.get_status()
    assert status["active"] == "eastmoney"
    print(f"✅ 失败3次后切换: {status['active']}")

    # 验证实际调用走 eastmoney
    rt = sel.get_realtime("000629")
    assert rt["source"] == "东方财富"
    print(f"✅ 切换后实际使用: {rt['source']}")


def test_index():
    """指数获取"""
    sel = get_selector()
    idx = sel.get_index_realtime("sh000001")
    assert idx is not None
    assert idx["price"] > 0
    print(f"✅ get_index_realtime: {idx['name']} ¥{idx['price']} ({idx['change_pct']:+.2f}%)")


if __name__ == "__main__":
    test_normal_operation()
    test_force_source()
    test_auto_failover()
    test_index()
    print()
    print("🎉 DataSourceSelector 所有测试通过!")
