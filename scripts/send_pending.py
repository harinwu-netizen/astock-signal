#!/usr/bin/env python3
"""
outbox 工具 (v4)
说明：C 方案改造后，信号灯只写 outbox 文件，由 OpenClaw heartbeat 读取转发。
      本脚本保留为"清空 outbox"的应急工具，由小海在发现积压时手动调用。
      不再作为自动后台进程运行！
"""

import sys
import os
import json
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUTBOX_DIR = "data/outbox"
HEALTH_FILE = "data/send_health.json"


def list_outbox():
    """列出 outbox 中所有待发消息"""
    if not os.path.exists(OUTBOX_DIR):
        return []
    files = sorted(os.listdir(OUTBOX_DIR))
    return [f for f in files if f.endswith(".json")]


def clear_outbox(older_than_min: int = 0):
    """清空 outbox 中超过指定分钟数(默认全部)的文件"""
    if not os.path.exists(OUTBOX_DIR):
        print("outbox 目录不存在")
        return 0

    files = list_outbox()
    cleared = 0
    now = datetime.now()
    for f in files:
        filepath = os.path.join(OUTBOX_DIR, f)
        if older_than_min > 0:
            mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
            age_min = (now - mtime).total_seconds() / 60
            if age_min < older_than_min:
                continue
        os.remove(filepath)
        cleared += 1
    return cleared


def show_health():
    """显示发送健康状态"""
    if not os.path.exists(HEALTH_FILE):
        print("无健康记录")
        return
    with open(HEALTH_FILE) as f:
        health = json.load(f)
    print(f"总写入: {health.get('total_outbox_written', 0)}")
    print(f"总失败: {health.get('total_outbox_failed', 0)}")
    print(f"最后状态: {health.get('last_status', 'unknown')}")
    print(f"最后更新: {health.get('last_update', 'unknown')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="outbox 工具 (C 方案)")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="列出 outbox 中的待发消息")
    sub.add_parser("clear", help="清空 outbox")
    sub.add_parser("health", help="查看健康状态")

    args = parser.parse_args()
    if args.cmd == "list":
        files = list_outbox()
        print(f"outbox 中有 {len(files)} 条消息:")
        for f in files:
            print(f"  - {f}")
    elif args.cmd == "clear":
        n = clear_outbox()
        print(f"已清空 {n} 条消息")
    elif args.cmd == "health":
        show_health()
    else:
        parser.print_help()
