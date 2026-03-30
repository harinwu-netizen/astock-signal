# -*- coding: utf-8 -*-
"""
数据清洗模块单元测试
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_provider.data_clean import (
    clean_kline_data, clean_realtime_data,
    add_derived_fields, is_suspended
)


def test_normal_clean():
    """正常数据原样保留"""
    records = [
        {"date": "2026-03-26", "open": 3.30, "high": 3.36, "low": 3.22, "close": 3.36, "volume": 1414233},
        {"date": "2026-03-27", "open": 3.32, "high": 3.46, "low": 3.29, "close": 3.43, "volume": 1378247},
    ]
    result = clean_kline_data(records)
    assert len(result) == 2
    assert result[0]["close"] == 3.36
    print("✅ test_normal_clean")


def test_price_zero_filtered():
    """价格=0的数据被过滤"""
    records = [
        {"date": "2026-03-25", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0},
        {"date": "2026-03-26", "open": 3.30, "high": 3.36, "low": 3.22, "close": 3.36, "volume": 1414233},
    ]
    result = clean_kline_data(records)
    assert len(result) == 1
    assert result[0]["close"] == 3.36
    print("✅ test_price_zero_filtered")


def test_excessive_change_filtered():
    """单日涨幅>20%的数据被过滤"""
    records = [
        {"date": "2026-03-26", "open": 3.30, "high": 3.36, "low": 3.22, "close": 3.36, "volume": 1414233},
        {"date": "2026-03-27", "open": 3.32, "high": 99.0, "low": 3.29, "close": 99.0, "volume": 100},  # 涨幅异常
    ]
    result = clean_kline_data(records)
    assert len(result) == 1
    assert result[0]["close"] == 3.36
    print("✅ test_excessive_change_filtered")


def test_dedup():
    """同日期去重，保留最后一条"""
    records = [
        {"date": "2026-03-26", "open": 3.30, "high": 3.36, "low": 3.22, "close": 3.36, "volume": 100},
        {"date": "2026-03-26", "open": 3.35, "high": 3.40, "low": 3.20, "close": 3.30, "volume": 200},
        {"date": "2026-03-27", "open": 3.40, "high": 3.50, "low": 3.30, "close": 3.35, "volume": 500000},
    ]
    result = clean_kline_data(records)
    assert len(result) == 2
    # 同日期保留最后一条，即 close=3.30 那条
    assert result[0]["close"] == 3.30
    print("✅ test_dedup")


def test_sorted_ascending():
    """结果按日期升序"""
    records = [
        {"date": "2026-03-30", "open": 3.42, "high": 3.46, "low": 3.37, "close": 3.43, "volume": 1030075},
        {"date": "2026-03-26", "open": 3.30, "high": 3.36, "low": 3.22, "close": 3.36, "volume": 1414233},
        {"date": "2026-03-27", "open": 3.32, "high": 3.46, "low": 3.29, "close": 3.43, "volume": 1378247},
    ]
    result = clean_kline_data(records)
    dates = [r["date"] for r in result]
    assert dates == sorted(dates), f"未排序: {dates}"
    print("✅ test_sorted_ascending")


def test_realtime_price_zero():
    """实时行情 price=0 返回 None"""
    result = clean_realtime_data({"code": "000629", "name": "test", "price": 0, "prev_close": 3.43})
    assert result is None
    print("✅ test_realtime_price_zero")


def test_realtime_excessive_change():
    """实时行情涨幅超限被修正为0"""
    result = clean_realtime_data({
        "code": "000629", "name": "test", "price": 3.43, "prev_close": 3.43, "change_pct": 50.0
    })
    assert result is not None
    assert result["change_pct"] == 0.0
    print("✅ test_realtime_excessive_change")


def test_derived_fields():
    """衍生字段：MA/量比/涨跌幅"""
    records = [
        {"date": "2026-03-26", "open": 3.30, "high": 3.36, "low": 3.22, "close": 3.36, "volume": 1414233},
        {"date": "2026-03-27", "open": 3.32, "high": 3.46, "low": 3.29, "close": 3.43, "volume": 1378247},
        {"date": "2026-03-30", "open": 3.42, "high": 3.46, "low": 3.37, "close": 3.43, "volume": 1030075},
    ]
    result = add_derived_fields(records)
    assert "ma5" in result[0]
    assert "ma20" in result[0]
    assert "volume_ratio" in result[0]
    print("✅ test_derived_fields")


if __name__ == "__main__":
    test_normal_clean()
    test_price_zero_filtered()
    test_excessive_change_filtered()
    test_dedup()
    test_sorted_ascending()
    test_realtime_price_zero()
    test_realtime_excessive_change()
    test_derived_fields()
    print()
    print("🎉 DataClean 所有测试通过!")
