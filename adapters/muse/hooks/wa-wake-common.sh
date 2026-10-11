#!/usr/bin/env bash
# wa-wake-common.sh — 单对话专属唤醒器的共享逻辑（被 wa-wake-<label>.sh source）。
# 入口脚本需先 export：WA_WAKE_CONVO（convoId）、WA_WAKE_KEY（状态文件键）。
# Optional: WA_WAKE_DM_GUARD=1 (owner DM only) — stay silent while
# dm_mode=main routes the owner's DM to the main chat instead.
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

if [ "${WA_WAKE_DM_GUARD:-0}" = "1" ]; then
    mode=$(python3 -c "import json;print(json.load(open('$HOME/wa-bridge/channel-mode.json')).get('dm_mode','sidechat'))" 2>/dev/null || echo sidechat)
    if [ "$mode" = "main" ]; then
        silent "dm_mode=main, handled by main hook" '{}'
    fi
fi

DRAIN="$HOME/wa-bridge/drain-queue.py"
STATE_DIR="$HOME/hooks/state"
STATE="$STATE_DIR/wa-wake-${WA_WAKE_KEY}.state"
mkdir -p "$STATE_DIR"

content=$(python3 "$DRAIN" --peek "$WA_WAKE_CONVO" 2>/dev/null || true)
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
    silent "no entries for this conversation" '{"lines":0}'
fi

if [ "$sig" != "$last_sig" ]; then
    save_state
    wake "本对话有 ${lines} 条新消息待处理" "{\"lines\":${lines},\"convoId\":\"${WA_WAKE_CONVO}\"}"
fi

if [ $((now - last_ts)) -ge 300 ]; then
    save_state
    wake "本对话条目滞留超 5 分钟（${lines} 条）" "{\"lines\":${lines},\"convoId\":\"${WA_WAKE_CONVO}\",\"escalated\":true}"
fi

silent "entries unchanged, already reported" "{\"lines\":${lines}}"
