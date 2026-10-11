#!/usr/bin/env bash
# wa-queue-wake — 只负责归 main 的队列条目：未映射新对话 / directMain / 滞留兜底。
# 已映射对话由各自专属唤醒器直达 side chat（drain-queue.py --peek-main 已排除它们）。
# 去重：按 main-worthy 子集内容签名，同一批只 wake 一次；滞留超 5 分钟再 wake 一次。
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

DRAIN="$HOME/wa-bridge/drain-queue.py"
STATE_DIR="$HOME/hooks/state"
STATE="$STATE_DIR/wa-queue-wake.state"
mkdir -p "$STATE_DIR"

content=$(python3 "$DRAIN" --peek-main 2>/dev/null || true)
lines=0
sig="empty"
if [ -n "$content" ]; then
    lines=$(printf '%s\n' "$content" | wc -l | tr -d ' ')
    sig=$(printf '%s' "$content" | sha256sum | cut -d' ' -f1)
fi

last_sig=""
last_ts=0
if [ -f "$STATE" ]; then
    read -r last_sig last_ts < "$STATE" || true
    last_ts=${last_ts:-0}
fi
now=$(date +%s)

save_state() {
    if [ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ]; then
        echo "$sig $now" > "$STATE"
    fi
}

if [ "$lines" -eq 0 ]; then
    save_state
    silent "no main-worthy entries" '{"lines":0}'
fi

if [ "$sig" != "$last_sig" ]; then
    save_state
    wake "WhatsApp 队列有 ${lines} 条归 main 处理（新对话/直连/兜底）" "{\"lines\":${lines}}"
fi

if [ $((now - last_ts)) -ge 300 ]; then
    save_state
    wake "WhatsApp 归 main 的条目滞留超 5 分钟（${lines} 条）" "{\"lines\":${lines},\"escalated\":true}"
fi

silent "main-worthy entries unchanged, already reported" "{\"lines\":${lines}}"
