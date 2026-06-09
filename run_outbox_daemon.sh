#!/bin/bash
# outbox 守护脚本 - 通过 cron 保证 outbox_daemon.py 在运行
#
# 职责:
#   - 检查 outbox_daemon.py 是否在跑
#   - 如果死了就重启
#   - 写入日志便于排查
#
# v6.6 (2026-06-04):
#   - 替代"小海 heartbeat 转发"机制
#   - 解决 10:00-14:00 飞书送达延迟问题
#
# Cron 配置 (每 5 分钟):
#   */5 * * * * /root/.openclaw/workspace/astock-signal/run_outbox_daemon.sh >> /root/.openclaw/workspace/astock-signal/logs/outbox_daemon_cron.log 2>&1

PIDFILE="/tmp/astock-outbox-daemon.pid"
DAEMON_SCRIPT="/root/.openclaw/workspace/astock-signal/scripts/outbox_daemon.py"
LOG_TAG="[OutboxDaemon]"

# 检查进程是否在跑
is_alive() {
    if [ ! -f "$PIDFILE" ]; then return 1; fi
    local pid=$(cat "$PIDFILE")
    if [ -z "$pid" ]; then return 1; fi
    kill -0 "$pid" 2>/dev/null
}

# 主逻辑
if is_alive; then
    # 进程在跑，啥也不做
    exit 0
fi

# 进程不在跑（死了或从没启动），需要启动
echo "$LOG_TAG $(date '+%H:%M:%S') 进程不在跑，准备启动"

# 清理可能残留的 PIDFILE
rm -f "$PIDFILE"

cd /root/.openclaw/workspace/astock-signal
python3 "$DAEMON_SCRIPT" start
sleep 1

if is_alive; then
    NEW_PID=$(cat "$PIDFILE")
    echo "$LOG_TAG $(date '+%H:%M:%S') ✅ 已启动 PID=$NEW_PID"
else
    echo "$LOG_TAG $(date '+%H:%M:%S') ❌ 启动失败，请查 outbox_daemon.log"
fi

exit 0
