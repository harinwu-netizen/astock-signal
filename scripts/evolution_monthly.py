#!/usr/bin/env python3
"""
evolution_monthly.py - 月末定时任务：生成月度报告 & 权重调整判断
由 OpenClaw Cron 调用: 每月28-31日 18:00 执行
"""
import sys
import os

sys.path.insert(0, '/root/.openclaw/workspace/astock-signal')

if __name__ == "__main__":
    from evolution.orchestrator import on_month_end
    result = on_month_end()
    print(f"[Evolution Monthly] {result}")
