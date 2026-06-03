#!/bin/bash
# 信号灯守护脚本 - 通过cron保证watch进程始终运行
# 仅在交易时间内检测并续跑，避免休市期间空跑

LOCKFILE="/tmp/astock-watch.lock"
PIDFILE="/tmp/astock-watch.pid"

# 检查是否在交易时间（工作日 09:15-15:05）
is_trading_hours() {
    now=$(date +%H%M)
    day=$(date +%u)  # 1=周一, 7=周日
    # 周末跳过
    if [ "$day" -ge 6 ]; then
        return 1
    fi
    # 09:15-15:05 之间
    if [ "$now" -ge "0915" ] && [ "$now" -le "1505" ]; then
        return 0
    else
        return 1
    fi
}

# 检查进程是否还在跑
if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        # 进程活着，检查是否应该继续
        if is_trading_hours; then
            exit 0
        else
            # 休市了，杀掉进程
            kill -9 "$PID" 2>/dev/null
            rm -f "$PIDFILE"
            exit 0
        fi
    fi
fi

# 没有进程，且在交易时间内，启动watch
if is_trading_hours; then
    cd /root/.openclaw/workspace/astock-signal
    nohup python3 main.py watch --continuous >> logs/watch_cron.log 2>&1 &
    echo $! > "$PIDFILE"
fi
exit 0