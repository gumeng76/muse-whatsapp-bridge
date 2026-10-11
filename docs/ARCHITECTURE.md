# Architecture notes

## The dumb-pipe rule

The relay layer (gateway journal → detector → queue → wake) performs no
interpretation: no summarization, no context assembly, no routing
decisions beyond fixed rules (mapping lookup, trigger match, @
detection). All understanding lives in the side chats, which keep their
own native context and memory. This keeps the pipe fast, deterministic,
and debuggable — and keeps the main chat out of the hot path.

## Queue semantics

`forward-queue.jsonl` is an append-only file of JSON entries:

```json
{"ts": "...", "convoId": "...", "fromName": "...", "body": "...",
 "needReply": true, "sideChatName": "WA: ...", "directMain": false,
 "forwardText": "[reply][WhatsApp group | label]\nauthor: body"}
```

- The detector is the only producer; it appends under an exclusive
  `flock` on `forward-queue.lock`.
- Consumers never edit the file directly. `drain-queue.py <convoId>`
  takes the same lock, extracts exactly that conversation's entries, and
  atomically rewrites the remainder (`os.replace`). Concurrent drains of
  different conversations and concurrent appends cannot lose or
  duplicate entries.
- Entry order per conversation is preserved. Cross-conversation order
  after a drain rewrite can shuffle; entries carry timestamps, and chat
  is not order-critical at this scale.

## Wake model

Hooks poll the queue every 10s. A hook computes a signature over *its*
slice of the queue (one conversation, or the main-worthy subset):

- slice non-empty + signature changed → wake once;
- same signature still pending after 5 minutes → wake again (nudge);
- slice empty → silent, signature resets.

The main-worthy subset is computed by `drain-queue.py --peek-main`:
entries that are `directMain`, or whose `convoId` has no mapping, or
that are older than 10 minutes (fallback for broken mappings). Because
the per-conversation hooks and the main hook watch disjoint slices of
the same queue, a normal group message never wakes the main chat at all.

## Owner channel modes

The owner's DM has two routes, held in `channel-mode.json`:

- `sidechat` (default): like every other conversation — its own side
  chat, woken directly. The DM's per-conversation hook is active.
- `main`: entries are tagged `directMain` by the detector and are
  main-worthy; the DM hook's guard (`WA_WAKE_DM_GUARD`) makes it stay
  silent. The main chat answers personally and sends via `send.py`.

Switching is done by owner-only trigger phrases at the very start of a
DM (config `triggers`), evaluated by the detector — nobody else's
message can flip the mode, and a trigger word mid-message never fires.

## Process supervision

No systemd user bus is assumed. Supervision is layered shell:

- `gateway-monitor.sh` — wraps the gateway, logs every exit with its
  code, revives on health-check failure.
- `detector-daemon.sh` — start/stop/status for the resident detector;
  the daemon self-heals its relay child.
- `detector-cron.sh` — cron guardian: if the daemon is alive, exit
  silently; if dead, relaunch it; only if that fails, run one one-shot
  backup detection round.

Every layer's failure mode degrades to the next layer down, and every
layer logs enough to reconstruct what happened.
