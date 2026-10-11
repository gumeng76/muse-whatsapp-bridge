#!/usr/bin/env bash
# wa-wake-__KEY__ — per-conversation wake hook (logic lives in wa-wake-common.sh).
# Generate one of these per mapped conversation; see adapters/muse/README.md.
export WA_WAKE_CONVO="__CONVO_ID__"
export WA_WAKE_KEY="__KEY__"
# Owner DM only: uncomment the next line so this hook stays silent while the
# owner's DM is routed to the main chat (channel-mode dm_mode=main).
# export WA_WAKE_DM_GUARD=1
source "$HOME/hooks/scripts/wa-wake-common.sh"
