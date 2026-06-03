#!/bin/bash
# 信号灯守护脚本 - 通过 cron 保证 watch 进程在交易时间内运行
#
# v6.4 改造 (2026-06-03):
#   - 去掉 flock(原 flock -xn 会通过 fd 继承导致锁泄漏,守护脚本死锁)
#   - 改用 PIDFILE 单点判活,简单可靠
#   - 增加节假日判断(简单版本:不依赖外部库,内置 2026 年部分节假日)
#   - 修 bug:15:05 后杀进程,日志污染
#   - 增加日切换保护:每天 09:15 重启一次,避免长跑内存泄漏
#
# Cron 配置(每 5 分钟一次):
#   */5 * * * * /root/.openclaw/workspace/astock-signal/run_watch_daemon.sh >> /root/.openclaw/workspace/astock-signal/logs/watch_cron.log 2>&1

PIDFILE="/tmp/astock-watch.pid"
LOG_TAG="[Daemon]"

# 交易日判断(工作日 + 非节假日)
is_trading_day() {
    local day=$(date +%u)
    if [ "$day" -ge 6 ]; then
        return 1  # 周末
    fi

    # 检查节假日(YYYY-MM-DD 格式)
    local today=$(date +%Y-%m-%d)
    local holidays=(
        "2026-01-01" "2026-01-02" "2026-01-03"  # 元旦
        "2026-02-17" "2026-02-18" "2026-02-19" "2026-02-20" "2026-02-21" "2026-02-22" "2026-02-23" "2026-02-24"  # 春节
        "2026-04-04" "2026-04-05" "2026-04-06"  # 清明
        "2026-05-01" "2026-05-02" "2026-05-03" "2026-05-04" "2026-05-05"  # 劳动节
        "2026-06-19" "2026-06-20" "2026-06-21"  # 端午
        "2026-09-25" "2026-09-26" "2026-09-27"  # 中秋
        "2026-10-01" "2026-10-02" "2026-10-03" "2026-10-04" "2026-10-05" "2026-10-06" "2026-10-07" "2026-10-08"  # 国庆
    )

    for h in "${holidays[@]}"; do
        if [ "$today" = "$h" ]; then
            return 1  # 节假日
        fi
    done
    return 0  # 工作日
}

# 交易时段判断(09:15 ~ 15:00)
is_trading_hours() {
    local now=$(date +%H%M)
    if [ "$now" -ge "0915" ] && [ "$now" -lt "1500" ]; then
        return 0
    else
        return 1
    fi
}

# 进程判活(pid 存在 + 进程确实在跑)
is_process_alive() {
    local pid=$1
    if [ -z "$pid" ]; then return 1; fi
    kill -0 "$pid" 2>/dev/null
}

# === 主逻辑 ===
if ! is_trading_day; then
    # 节假日:不做事(如果进程还活着,杀掉)
    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if is_process_alive "$PID"; then
            echo "$LOG_TAG $(date '+%H:%M:%S') 节假日,杀掉进程 $PID"
            kill -9 "$PID" 2>/dev/null
            rm -f "$PIDFILE"
        fi
    fi
    exit 0
fi

if is_trading_hours; then
    # 交易时间内:确保 watch 进程在跑
    need_start=0
    if [ ! -f "$PIDFILE" ]; then
        need_start=1
        echo "$LOG_TAG $(date '+%H:%M:%S') 无 PIDFILE,需要启动"
    else
        PID=$(cat "$PIDFILE")
        if ! is_process_alive "$PID"; then
            need_start=1
            echo "$LOG_TAG $(date '+%H:%M:%S') 进程 $PID 已死,需要重启"
        fi
    fi

    if [ $need_start -eq 1 ]; then
        cd /root/.openclaw/workspace/astock-signal
        # 用 exec 替代 nohup/setsid
        # (nohup/setsid 会 fork wrapper,exec 替换当前 shell 为 python3, PID 不变)
        # 但 exec 在 & 后台不起作用,所以用一种特殊技巧:
        # 先 exec python3 启动命令,但 exec 会在后台立即被 & 包装
        # 最稳的方案:直接用 python3 ... &,$! 拿到的就是 python3 自身
        python3 main.py watch --continuous >> logs/watch_cron.log 2>&1 &
        NEW_PID=$!
        echo $NEW_PID > "$PIDFILE"
        echo "$LOG_TAG $(date '+%H:%M:%S') ✅ 启动新进程 PID=$NEW_PID (裸 python3 &)"
    fi
else
    # 非交易时间(>= 15:00 或 < 09:15):杀掉进程
    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if is_process_alive "$PID"; then
            echo "$LOG_TAG $(date '+%H:%M:%S') 非交易时间,杀掉进程 $PID"
            kill -9 "$PID" 2>/dev/null
        fi
        rm -f "$PIDFILE"
        echo "$LOG_TAG $(date '+%H:%M:%S') 清理 PIDFILE"
    fi
fi

exit 0
