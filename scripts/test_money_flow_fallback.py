#!/usr/bin/env python3
"""
资金流三级 fallback 集成测试 (v6.13)
海赟 21:48 集成后,每天扫描后用这个脚本验证:
  1. 各数据源的实际使用情况
  2. screener 是否被风控
  3. 三级 fallback 链是否工作

用法:
  python3 scripts/test_money_flow_fallback.py
  # 或指定股票
  python3 scripts/test_money_flow_fallback.py 000629 603683
"""

import sys
import time
import logging
from pathlib import Path

# 把 astock-signal 加到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_provider.money_flow import get_money_flow, _cache, MoneyFlowData

logging.basicConfig(level=logging.WARNING, format="%(message)s")

# 默认测试 7 只信号灯股票
DEFAULT_STOCKS = [
    ("000629", "钒钛股份"),
    ("603683", "晶华新材"),
    ("300308", "中际旭创"),
    ("002202", "金风科技"),
    ("002792", "通宇通讯"),
    ("002371", "北方华创"),
    ("688981", "中芯国际"),
]


def test_single(code: str, name: str) -> dict:
    """测试单只股票的资金流获取,返回来源分析"""
    _cache.clear()  # 清缓存,测试实时获取
    start = time.time()
    mf = get_money_flow(code, name)
    elapsed = time.time() - start

    if mf is None:
        return {
            "code": code,
            "name": name,
            "source": "❌ 失败",
            "elapsed": f"{elapsed:.1f}s",
            "main_net": None,
            "big_net": None,
            "super_net": None,
        }

    return {
        "code": code,
        "name": name,
        "source": mf.source,
        "elapsed": f"{elapsed:.1f}s",
        "main_net": f"{mf.main_net:+.0f}",
        "big_net": f"{mf.big_net:+.0f}",
        "super_net": f"{mf.super_net:+.0f}",
    }


def main():
    stocks = DEFAULT_STOCKS
    if len(sys.argv) > 1:
        # 命令行传入的 code/name 列表
        args = sys.argv[1:]
        stocks = []
        for i in range(0, len(args), 2):
            code = args[i]
            name = args[i+1] if i+1 < len(args) else ""
            stocks.append((code, name))

    print(f"=== 资金流三级 fallback 测试 ({len(stocks)} 只股票) ===\n")
    print(f"{'代码':<10} {'名称':<12} {'来源':<14} {'耗时':<8} {'主力(万)':<12} {'大单(万)':<12} {'超大单(万)':<12}")
    print("-" * 90)

    results = []
    for code, name in stocks:
        r = test_single(code, name)
        results.append(r)
        source = r['source']
        main = r.get('main_net', '-')
        big = r.get('big_net', '-')
        super_ = r.get('super_net', '-')
        print(f"{r['code']:<10} {r['name']:<12} {source:<14} {r['elapsed']:<8} {main or '-':<12} {big or '-':<12} {super_ or '-':<12}")
        time.sleep(0.5)  # 避免触发风控

    print()
    print("=== 来源统计 ===")
    sources = {}
    for r in results:
        s = r['source']
        sources[s] = sources.get(s, 0) + 1
    for s, cnt in sources.items():
        print(f"  {s}: {cnt} 只")


if __name__ == "__main__":
    main()
