#!/bin/bash
# baileys-test-monitor.sh — Baileys bot (端口 18924) 存活巡检 + 自动拉起
# 2026-10-11 起 Baileys 为主链路：正式模式（发送开启），入站写 messages-baileys.jsonl。
# 用途：记录 bot 每次死亡的精确时间与退出码（SIGKILL=137 / SIGTERM=143 / 进程内退出=0/2），
#       下次静默退出时直接看 bot-baileys-test-monitor.log 即可定位"谁动的手"。
# 注意：只管 Baileys bot，不碰旧链路 bot.js（旧链路由 whatsapp-bot-watchdog 看门狗管）。
DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$DIR/gateway.log"
MONLOG="$DIR/bot-baileys-test-monitor.log"
PIDFILE="$DIR/.gateway.pid"
PORT=18924

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

alive() {
    curl -sf -m 5 "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -q '"ok":true'
}

start_testbot() {
    cd "$DIR" || return 1
    # wrapper: 进程退出时把退出码+时间写入 MONLOG，然后才真正结束
    setsid bash -c "
        BAILEYS_PORT=${PORT} node bot.js >> '${LOG}' 2>&1
        code=\$?
        echo \"\$(date -u +%Y-%m-%dT%H:%M:%SZ) WRAPPER: Baileys bot 进程退出, code=\$code (137=SIGKILL 143=SIGTERM 0=/shutdown 2=重连耗尽, 其他=崩溃)\" >> '${MONLOG}'
    " </dev/null >/dev/null 2>&1 &
    echo $! > "$PIDFILE"
    echo "$(ts) MONITOR: 已拉起 Baileys bot (wrapper pid $(cat "$PIDFILE"))" >> "$MONLOG"
}

if alive; then
    exit 0
fi

echo "$(ts) MONITOR: 检测到 Baileys bot 端口 ${PORT} 无响应，准备重启" >> "$MONLOG"
# 清理残留进程（wrapper bash + node），括号技巧防自匹配
for p in $(pgrep -f "[b]ot-baileys.js"); do kill "$p" 2>/dev/null; done
sleep 2
start_testbot
sleep 8
if alive; then
    echo "$(ts) MONITOR: 重启后 /health ok" >> "$MONLOG"
else
    echo "$(ts) MONITOR: 重启后 /health 仍无响应，需人工排查" >> "$MONLOG"
fi
