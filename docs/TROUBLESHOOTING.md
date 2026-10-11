# Troubleshooting

Failure modes this stack has actually hit in production, and the fixes
that are now built in.

## Sends "succeed" but never arrive; inbound silently stops

The WhatsApp Web socket is half-open: `/health` still reports
`ready: true`, the send API returns `{"ok": true}`, entries are journaled
as `sent` — and nothing is delivered either way. This is the single most
dangerous failure because every surface signal says "fine".

- Detection: judge the gateway by **real inbound flow** (`idleSec`,
  time since last journaled message), never by `/health` alone. If you
  send a test and the peer never sees it while health is green, assume
  half-open.
- Fix: graceful restart (`POST /shutdown`, then start again). The session
  survives; no re-pairing needed. A hard `kill -9` is the last resort —
  it can corrupt the session.
- Never start a second gateway instance while one is alive: the session
  gets kicked and you will have to re-pair.

## HTTP 405 on connect after pairing

Baileys pins a WhatsApp Web version tuple. When WhatsApp retires that
version, connection is rejected with 405. `gateway/bot.js` keeps the
tuple in one constant (`WA_VERSION`) precisely so the fix is one line:
update it to a current tuple (see recent Baileys releases) — do not call
`fetchLatestBaileysVersion()` at runtime in restricted networks.

## Gateway process vanishes with no crash log

Observed repeatedly: the process dies silently inside a reconnect backoff
window (e.g. after a 428 `connectionClosed`). No stack trace, no OOM.

- `ops/gateway-monitor.sh` wraps the process and records the exit code and
  timestamp of **every** death (137 = SIGKILL, 143 = SIGTERM, 0 =
  /shutdown, 2 = reconnect exhausted), then revives it. Run it from cron
  every few minutes; when the bot "randomly dies", that log is where the
  answer lives.
- The session is reused on restart; re-pairing is not needed.

## Detector "alive" but messages arrive minutes late — or never

- The detector's relay child (`relay.js`) can die silently while the
  detector keeps idling: every round reports "no new messages" (a false
  negative). The daemon mode checks its relay's liveness every second
  and restarts it.
- If the detector itself is killed by a timeout, its relay becomes an
  orphan (PPID 1) that keeps writing into the shared relay output — two
  relays double-feed the detector and duplicate queue entries. If you
  ever see more than one `relay.js`, keep the one whose parent is the
  daemon and kill the rest.
- Shell pitfall: `pgrep -f "detector.py"` also matches wrapper shells
  whose command line merely *contains* that string, and matches your own
  querying shell. Anchor patterns (`^python3 detector.py --daemon`) or
  check `comm` before killing anything.

## Queue entries stuck

- Entries for a conversation whose side chat was deleted linger until
  the main chat's 10-minute stale fallback picks them up. Re-create the
  side chat + hook (adapter README), or drain manually:
  `python3 drain-queue.py '<convoId>'`.
- All queue writers/readers share `forward-queue.lock` (flock). A drain
  racing a detector append is safe by design; do not edit the queue file
  by hand while the daemon runs — use `drain-queue.py`.

## Duplicate replies

Almost always two consumers on the same journal: an old gateway writing
the same `messages.jsonl`, two relays (above), or a leftover backup
detector round overlapping the daemon. One writer per file, one relay
per detector, one daemon per install.
