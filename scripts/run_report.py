#!/usr/bin/env python3
"""
收盘报告执行器 - 用于 Cron 触发
先执行 watch 扫描生成报告，再触发收盘后统计更新
"""
import subprocess
import sys
import os

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 先执行 watch 扫描（生成信号/持仓/决策报告）
result1 = subprocess.run(
    [sys.executable, "main.py", "watch"],
    capture_output=False,
)
# 再执行 report（生成复盘报告 + 更新信号统计）
result2 = subprocess.run(
    [sys.executable, "main.py", "report"],
    capture_output=False,
)

sys.exit(result2.returncode)