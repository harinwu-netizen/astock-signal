#!/usr/bin/env python3
"""
飞书消息队列检查脚本 - 由OpenClaw Cron调用
检查 data/pending_messages.json，有消息则发送给用户
"""
import sys
import os
import json

sys.path.insert(0, '/root/.openclaw/workspace/astock-signal')

PENDING_FILE = '/root/.openclaw/workspace/astock-signal/data/pending_messages.json'
TARGET = 'ou_ee2947ff311d4978679c2a2d4433f62a'


def main():
    if not os.path.exists(PENDING_FILE):
        sys.exit(0)

    with open(PENDING_FILE) as f:
        pending = json.load(f)

    if not pending:
        sys.exit(0)

    msg = pending[0]

    # 打印出来，由OpenClaw Cron的session捕获并发送
    print(f"SEND_TO_FEISHU:{TARGET}:{msg['content']}")

    # 标记已处理（删除）
    pending.pop(0)
    with open(PENDING_FILE, 'w') as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
