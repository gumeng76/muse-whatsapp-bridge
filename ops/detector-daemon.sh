#!/bin/bash
# detector-daemon.sh — detector.py --daemon 常驻探测器的启动器/守护入口
# 2026-10-11 程序化改造：探测器由"单次任务 + cron 兜底"改为常驻进程，
# 消息落盘后秒级进 forward-queue。detector-cron.sh 负责巡检拉起（daemon 死了
# 下一轮 cron 自动调本脚本 start）。
# 用法：detector-daemon.sh start|stop|status
DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$DIR/detector-daemon.log"
PIDFILE="$DIR/.detector-daemon.pid"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

daemon_pid() {
    # 锚定行首：wrapper bash 的命令行也含 "detector.py --daemon" 字样，
    # 不锚定会误把 wrapper 当成本体（2026-10-11 实测 stop 杀错对象）。
    pgrep -f "^python3 detector.py --daemon" 2>/dev/null | head -1
}

case "${1:-status}" in
start)
    pid=$(daemon_pid)
    if [ -n "$pid" ]; then
        echo "detector daemon 已在运行 (pid $pid)"
        exit 0
    fi
    cd "$DIR" || exit 1
    setsid bash -c "
        python3 detector.py --daemon >> '${LOG}' 2>&1
        code=\$?
        echo \"\$(date -u +%Y-%m-%dT%H:%M:%SZ) WRAPPER: detector daemon 退出, code=\$code\" >> '${LOG}'
    " </dev/null >/dev/null 2>&1 &
    echo $! > "$PIDFILE"
    sleep 3
    pid=$(daemon_pid)
    if [ -n "$pid" ]; then
        echo "$(ts) detector-daemon: 已拉起 (pid $pid)" >> "$LOG"
        echo "detector daemon 已拉起 (pid $pid)"
    else
        echo "$(ts) detector-daemon: 拉起失败，看 detector-daemon.log" >> "$LOG"
        echo "detector daemon 拉起失败"
        exit 1
    fi
    ;;
stop)
    pid=$(daemon_pid)
    if [ -n "$pid" ]; then
        kill "$pid" 2>/dev/null
        echo "detector daemon (pid $pid) 已发 SIGTERM（其信号处理会连带清理 relay）"
    else
        echo "detector daemon 未在运行"
    fi
    ;;
status)
    pid=$(daemon_pid)
    if [ -n "$pid" ]; then
        echo "running (pid $pid)"
    else
        echo "not running"
        exit 1
    fi
    ;;
*)
    echo "usage: $0 start|stop|status" >&2
    exit 2
    ;;
esac
