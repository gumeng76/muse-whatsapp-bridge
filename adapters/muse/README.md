# Muse adapter

This adapter connects the bridge pipeline to Muse's chat primitives:
**hooks** (event-driven wake-ups) and **side chats** (one persistent Muse
conversation per WhatsApp conversation). Only the main chat can create
side chats, so the main chat has exactly three low-frequency jobs:

1. **Create** — first message from an unmapped conversation: create the
   side chat, register the mapping, install its wake hook, hand over the
   backlog.
2. **Direct line** — the owner's DM while it is routed to the main chat.
3. **Fallback** — anything stuck in the queue for over 10 minutes.

Everything else flows side-chat-direct and never touches the main chat.

## Files

- `hooks/wa-queue-wake.sh` — the main chat's hook script. Wakes the main
  chat only for main-worthy entries: `drain-queue.py --peek-main`
  (unmapped / directMain / stale). Content-signature dedupe; re-wakes
  after 5 minutes if still pending.
- `hooks/wa-wake-common.sh` — shared logic for per-conversation hooks.
- `hooks/wa-wake-template.sh` — per-conversation hook script template.
- `templates/side-chat-instructions.md` — standing instructions to paste
  into every new side chat.

## Setup

1. Copy the hook scripts into `~/hooks/scripts/` on the Muse VM.
2. Create the main hook (10s poll, delivery: main chat) with a prompt like:

   > WhatsApp queue has entries for the main chat (new conversation /
   > owner direct line / stale fallback). Run
   > `python3 ~/wa-bridge/drain-queue.py --peek-main` and relay every
   > returned entry verbatim (forwardText, convoId, directMain, ts) to
   > the main chat. Relay only — do not summarize, reply, or drain.

3. For the owner's DM side chat (`WA: <ownerLabel>`), create it from the
   main chat, register it in `chat-mapping.json`, then install its hook
   (step below) with `WA_WAKE_DM_GUARD=1` so it stays silent while the DM
   is routed to the main chat.

## Installing a per-conversation hook (the "create" procedure)

When the main chat creates a side chat for conversation `<convoId>`:

1. `chat.create` named `WA: <label>` (DMs) or `WA group: <label>` (groups);
   paste the filled side-chat instruction template as its first
   long-term instruction.
2. Add `"<convoId>": "<new chat_id>"` to `~/wa-bridge/chat-mapping.json`.
3. Generate the hook script from `hooks/wa-wake-template.sh`
   (`__CONVO_ID__`, `__KEY__` = short stable slug, e.g. `g-family`;
   add the DM guard line for the owner's DM) into `~/hooks/scripts/`.
4. `hooks.add` — poll 10s, delivery = that side chat, prompt:

   > This conversation has new WhatsApp messages. Run
   > `python3 ~/wa-bridge/drain-queue.py '<convoId>'` to take this
   > conversation's queue entries (already removed atomically; do not
   > touch the queue file again). Process each entry per this chat's
   > standing instructions: post the body verbatim first; for `[reply]`
   > entries also send a real reply via send.py ending with the channel
   > tag; `[log-only]` entries end after the verbatim post. If the
   > script prints nothing, the entries were already handled — do
   > nothing.

5. `hooks.dry_run` (expect silent on an empty queue), then `hooks.enable`.
6. Hand over any backlog: tell the new side chat to run its drain command
   once.

After that, the conversation is on the direct path permanently.

## Latency budget (measured in production)

journal → queue ≈ 8s (5s merge window + flush), queue → wake ≈ 3s,
plus one agent turn to compose and send. End to end ≈ 1 minute,
versus 2–5 minutes for the cron-relay design this replaced.
