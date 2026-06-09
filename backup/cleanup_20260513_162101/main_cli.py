#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
逆流选股法 — 独立运行 CLI

用法：
  python main_cli.py scan              # 每日扫描
  python main_cli.py scan --date 2026-05-11  # 指定日期扫描
  python main_cli.py status           # 查看股票池状态
  python main_cli.py add 000629       # 手动添加股票
  python main_cli.py remove 000629      # 移出股票
  python main_cli.py test-moneyflow    # 测试资金流数据获取
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime

# 确保 .env 加载
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=False)

from stock_pool.逆流_selector import 逆流Selector, SelectorConfig, print_candidates
from stock_pool.pool_manager import PoolManager
from stock_pool.ai_analyzer import AIAnalyzer, format_analysis_for_feishu
from stock_pool.money_flow_history import MoneyFlowHistory


def cmd_scan(args):
    """每日扫描"""
    scan_date = args.date or datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"【逆流选股法 · 每日扫描】{scan_date}")
    print(f"{'='*60}\n")

    # 1. 量化扫描
    print("🔍 L1+L2 量化扫描中...")
    cfg = SelectorConfig(
        use_index_pool=args.index_pool,
        max_candidates=args.max_candidates,
        net_outflow_thresh=args.threshold,
        net_outflow_window=args.window,
    )
    selector = 逆流Selector(cfg)
    candidates = selector.scan(scan_date=scan_date)
    print(f"\n量化扫描完成，候选 {len(candidates)} 只")
    if candidates:
        print_candidates(candidates)
    else:
        print("无候选股票")

    if not candidates:
        print("\n⚠️ 无候选，跳过后续步骤")
        return

    # 2. AI 分析
    if not args.no_ai:
        print(f"\n🤖 AI 分析中（{len(candidates)} 只）...")
        analyzer = AIAnalyzer()
        results = []

        for i, c in enumerate(candidates):
            print(f"  分析 {i+1}/{len(candidates)}: {c.code} {c.name}...", end=" ", flush=True)
            result = analyzer.analyze(
                code=c.code,
                name=c.name,
                close=c.close,
                price_change=f"{c.price_range_10d:+.2f}%",
                net_outflow=c.net_outflow_10d,
                super_big_outflow=c.super_net_outflow_10d,
                scan_date=scan_date,
            )
            results.append((c, result))
            print(f"✅ {result.概率} | {result.入池建议}")
            if i < len(candidates) - 1:
                time.sleep(1.5)

        # 3. 入池决策
        print(f"\n📋 入池决策：")
        pm = PoolManager()

        # 统计
        recommended = [r for r in results if r[1].is_recommended]
        print(f"  建议入池: {len(recommended)} 只")
        print(f"  不建议入池: {len(results) - len(recommended)} 只")

        for c, result in results:
            if result.is_recommended:
                added = pm.add_to_observation(
                    code=c.code,
                    signal_reason="逆流信号",
                    ai_analysis={
                        "概率": result.概率,
                        "摘要": result.分析摘要,
                        "核心逻辑": result.核心逻辑,
                        "风险": result.风险提示,
                    },
                    candidate_data={
                        "name": c.name,
                        "score": c.score,
                        "close": c.close,
                        "net_outflow_10d": c.net_outflow_10d,
                    },
                )
                if added:
                    print(f"  ✅ {c.code} {c.name} → 入观察池 | {result.核心逻辑}")
                else:
                    print(f"  ⏭️  {c.code} 已在池中，跳过")
            else:
                print(f"  ❌ {c.code} {c.name} → 跳过 | {result.核心逻辑 or 'AI不建议入池'}")

    else:
        print("\n⏭️ 跳过 AI 分析（--no-ai 模式）")
        # 直接入池
        pm = PoolManager()
        for c in candidates[:5]:  # 最多5只
            pm.add_to_observation(
                code=c.code,
                signal_reason="逆流信号（无AI）",
                candidate_data={
                    "name": c.name,
                    "score": c.score,
                    "close": c.close,
                    "net_outflow_10d": c.net_outflow_10d,
                },
            )
            print(f"  ✅ {c.code} {c.name} → 入观察池")

    # 4. 飞书通知
    if not args.no_notify:
        send_feishu_notification(scan_date, candidates, results if not args.no_ai else None)

    print(f"\n✅ 扫描完成！")


def cmd_status(args):
    """查看股票池状态"""
    pm = PoolManager()
    pm.print_status()


def cmd_add(args):
    """手动添加股票"""
    pm = PoolManager()
    code = args.code.upper().zfill(6)
    name = args.name or code

    added = pm.add_to_observation(
        code=code,
        signal_reason="手动添加",
        candidate_data={"name": name, "score": 0, "close": 0, "net_outflow_10d": 0},
    )
    if added:
        print(f"✅ {code} {name} 已入观察池")
    else:
        print(f"⏭️ {code} 已在观察池中")


def cmd_remove(args):
    """移出股票"""
    pm = PoolManager()
    code = args.code.upper().zfill(6)
    removed = pm.remove(code, args.reason or "手动移出")
    if removed:
        print(f"✅ {code} 已移出")
    else:
        print(f"⚠️ {code} 不在池中")


def cmd_test_moneyflow(args):
    """测试资金流数据"""
    code = args.code or "000629"
    start = args.start or "2025-04-01"
    end = args.end or "2025-04-30"
    name = args.name or ("攀钢钒钛" if code == "000629" else code)

    print(f"\n测试资金流获取: {code} {name}")
    print(f"区间: {start} ~ {end}\n")

    mfh = MoneyFlowHistory()
    df = mfh.get_history(code, start, end, name)
    print(f"获取 {len(df)} 条记录")
    if not df.empty:
        print(df.to_string())

        # 近10日累计
        net = mfh.get_net_outflow(code, end, window=10, name=name)
        print(f"\n近10日累计大单净流出: {net:.2f}万元" if net is not None else "\n获取失败")


def send_feishu_notification(scan_date, candidates, ai_results):
    """发送飞书通知"""
    try:
        from notification.feishu import get_feishu_notifier
        notifier = get_feishu_notifier()
        if not notifier:
            print("\n⚠️ 飞书未配置，跳过通知")
            return

        lines = [
            f"📊 **逆流选股 · 每日扫描** {scan_date}",
            "",
        ]

        if ai_results:
            recommended = [(c, r) for c, r in ai_results if r.is_recommended]
            lines.append(f"**建议入池 ({len(recommended)} 只)**")
            for c, r in recommended:
                emoji = {"高": "🟢", "中": "🟡", "低": "🔴"}.get(r.概率, "⚪")
                lines.append(f"{emoji} {c.code} {c.name} | {r.概率} | {r.核心逻辑}")
        else:
            lines.append(f"**候选股票 ({len(candidates)} 只)**")
            for c in candidates[:5]:
                lines.append(f"  {c.code} {c.name} | 评分{c.score:.1f}")

        lines.append("")
        lines.append("_由 astock-signal 逆流选股模块生成_")

        msg = "\n".join(lines)
        notifier.send_text(msg)
        print(f"\n📨 飞书通知已发送")
    except Exception as e:
        print(f"\n⚠️ 飞书通知失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="逆流选股法 CLI")
    sub = parser.add_subparsers(dest="cmd", help="子命令")

    # scan
    p_scan = sub.add_parser("scan", help="每日扫描")
    p_scan.add_argument("--date", help="扫描日期 YYYY-MM-DD")
    p_scan.add_argument("--no-ai", action="store_true", help="跳过AI分析")
    p_scan.add_argument("--no-notify", action="store_true", help="跳过飞书通知")
    p_scan.add_argument("--index-pool", action="store_true", default=False, help="使用指数成分股池（默认关闭）")
    p_scan.add_argument("--max-candidates", type=int, default=20, help="最大候选数")
    p_scan.add_argument("--threshold", type=float, default=3000, help="大单净流出阈值（万元）")
    p_scan.add_argument("--window", type=int, default=10, help="累计天数")
    p_scan.set_defaults(func=cmd_scan)

    # status
    p_status = sub.add_parser("status", help="查看股票池状态")
    p_status.set_defaults(func=cmd_status)

    # add
    p_add = sub.add_parser("add", help="手动添加股票")
    p_add.add_argument("code", help="股票代码")
    p_add.add_argument("--name", help="股票名称")
    p_add.set_defaults(func=cmd_add)

    # remove
    p_remove = sub.add_parser("remove", help="移出股票")
    p_remove.add_argument("code", help="股票代码")
    p_remove.add_argument("--reason", help="移出原因")
    p_remove.set_defaults(func=cmd_remove)

    # test-moneyflow
    p_test = sub.add_parser("test-moneyflow", help="测试资金流数据")
    p_test.add_argument("--code", default="000629", help="股票代码")
    p_test.add_argument("--start", help="开始日期 YYYY-MM-DD")
    p_test.add_argument("--end", help="结束日期 YYYY-MM-DD")
    p_test.add_argument("--name", help="股票名称")
    p_test.set_defaults(func=cmd_test_moneyflow)

    args = parser.parse_args()

    if args.cmd is None:
        parser.print_help()
        return

    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    args.func(args)


if __name__ == "__main__":
    main()
