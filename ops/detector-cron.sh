#!/bin/bash
# WhatsApp 探测器 cron 兜底脚本
# 设计：事件驱动（手动单次派发）为主力，cron 每分钟检查一次做兜底
# 逻辑：
# 1. 如果 detector.py 或 relay.js 正在运行 → 事件驱动活着，退出不做事
# 2. 如果 .relay_cursor 在 2 分钟内更新过 → 刚跑完，退出不做事
# 3. 否则 → 运行 detector.py 补漏
#
# 并发安全：靠第 1 步的进程检查避免双跑；cursor 本身由 relay.js 顺序更新

DIR="$(cd "$(dirname "$0")" && pwd)"
CURSOR="$DIR/.relay_cursor"

# 1. 检查事件驱动是否在干活
if pgrep -f "[d]etector.py" > /dev/null 2>&1; then
    # detector.py 正在运行，事件驱动活着
    exit 0
fi

# relay.js 单独运行不算活着：它可能是 detector 被杀后留下的孤儿（PPID=1）。
# 只有 relay 的父进程链里还有活的 detector.py，才算事件驱动活着。
relay_alive=0
for rp in $(pgrep -f "[n]ode relay\.js" 2>/dev/null); do
    pp=$(ps -o ppid= -p "$rp" 2>/dev/null | tr -d ' ')
    while [ -n "$pp" ] && [ "$pp" != "1" ]; do
        if ps -o args= -p "$pp" 2>/dev/null | grep -q "[d]etector\.py"; then
            relay_alive=1
            break 2
        fi
        pp=$(ps -o ppid= -p "$pp" 2>/dev/null | tr -d ' ')
    done
done
if [ "$relay_alive" = "1" ]; then
    # 有 detector.py 主进程带着的 relay，事件驱动活着
    exit 0
fi

# 1b. 常驻 daemon 不在 → 先尝试拉起（2026-10-11 改造：daemon 为主力，
# cron 只做守护；拉起失败才落到下面的单次备份轮）。
bash "$DIR/detector-daemon.sh" start >> "$DIR/detector-cron.log" 2>&1
if pgrep -f "[d]etector.py --daemon" > /dev/null 2>&1; then
    echo "$(date -u +%FT%TZ) detector-cron: daemon relaunched, skip backup round" >> "$DIR/detector-cron.log"
    exit 0
fi

# 2. 检查 cursor 是否刚更新过（2 分钟内）
if [ -f "$CURSOR" ]; then
    now=$(date +%s)
    mtime=$(stat -c %Y "$CURSOR" 2>/dev/null || echo 0)
    age=$((now - mtime))
    if [ "$age" -lt 120 ]; then
        # 2 分钟内刚跑过，不着急
        exit 0
    fi
fi

# 3. 事件驱动没在干活，且 cursor 已 stale → 跑一轮补漏
cd "$DIR" || exit 1

# 确认 bot 健康才跑（避免 bot 挂了还空转）
# 2026-10-11 切换后主链路是 Baileys（18924，detector 消费的就是它的落盘），查它。
if ! curl -s --max-time 5 http://127.0.0.1:18924/health | grep -q '"ready":true'; then
    echo "$(date -u +%FT%TZ) detector-cron: bot not ready, skip" >> "$DIR/detector-cron.log"
    exit 0
fi

# 本轮开始前已存在的 relay PID（手动轮的或孤儿），不归我们管
pre_relays=" $(pgrep -f "[n]ode relay\.js" 2>/dev/null | sort -n | tr '\n' ' ') "

echo "$(date -u +%FT%TZ) detector-cron: event-driven stale, running backup detection" >> "$DIR/detector-cron.log"
timeout 90 python3 detector.py >> "$DIR/detector-cron.log" 2>&1

# 心跳兜底：timeout 90 可能 SIGTERM 杀掉 detector.py，导致其末尾的 touch 没执行。
# 只要本轮跑完了（无论超时或正常退出），都刷新一次 cursor mtime，
# 防止零流量时 cron 因 mtime 停滞而每 2 分钟空转一轮。
touch "$CURSOR" 2>/dev/null

# 清理 relay：只杀本轮 detector 新起的 relay（detector 正常退出会自己清，
# 这里是兜底）。开始前就已存在的 relay（手动轮的或孤儿）一律不动；
# 如果此刻还有 detector.py 在跑（并发的手动轮），一个都不杀，避免误伤。
if ! pgrep -f "[d]etector.py" >/dev/null 2>&1; then
    for rp in $(pgrep -f "[n]ode relay\.js" 2>/dev/null); do
        case "$pre_relays" in
            *" $rp "*) ;;
            *) kill "$rp" 2>/dev/null;;
        esac
    done
fi

echo "$(date -u +%FT%TZ) detector-cron: done" >> "$DIR/detector-cron.log"
