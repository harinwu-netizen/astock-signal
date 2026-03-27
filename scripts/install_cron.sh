#!/bin/bash
# 安装定时任务
# 用法: bash scripts/install_cron.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "📅 安装 A股信号灯 定时任务"
echo "========================================"

# 检查Python环境
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 未安装"
    exit 1
fi

# 创建日志目录
mkdir -p logs

# 获取当前crontab
echo "现有定时任务:"
crontab -l 2>/dev/null || echo "  (无)"

# 添加新的定时任务
(crontab -l 2>/dev/null | grep -v "astock-signal"; cat << 'CRON'
# A股信号灯 - 每天14:25启动监控
25 14 * * 1-5 cd /root/.openclaw/workspace/astock-signal && python3 main.py watch >> logs/watch.log 2>&1

# A股信号灯 - 每天15:10收盘报告
10 15 * * 1-5 cd /root/.openclaw/workspace/astock-signal && python3 main.py report >> logs/report.log 2>&1

# A股信号灯 - 每天09:20开盘前检查
20 9 * * 1-5 cd /root/.openclaw/workspace/astock-signal && python3 main.py analyze --pool >> logs/morning.log 2>&1
CRON
) | crontab -

echo ""
echo "✅ 定时任务已安装:"
crontab -l 2>/dev/null | grep astock-signal
echo ""
echo "📋 定时任务说明:"
echo "  1. 每天 14:25 (周一至周五) - 启动watch监控"
echo "  2. 每天 15:10 (周一至周五) - 发送收盘报告"
echo "  3. 每天 09:20 (周一至周五) - 开盘前分析"
echo ""
echo "💡 取消定时任务: crontab -e"
