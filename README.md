# muse-whatsapp-bridge

Self-hosted WhatsApp bridge for [Muse](https://muse.ai) personal AI agents.

A dedicated WhatsApp number is linked as a companion device; every DM and
group conversation is relayed to its own Muse side chat, and replies flow
back to WhatsApp. The pipeline is program-driven end to end — resident
processes and file queues do the carrying; AI turns only handle content.

> **Disclaimer.** This project uses [Baileys](https://github.com/WhiskeySockets/Baileys),
> an unofficial WhatsApp Web protocol implementation. It is not affiliated
> with WhatsApp or Meta. Unofficial clients violate WhatsApp's Terms of
> Service and numbers can be banned. **Use a dedicated number, never your
> primary one, and use this software at your own risk.** Provided as-is,
> low-maintenance.

## Architecture

```
WhatsApp  <--Baileys-->  gateway/bot.js          (HTTP API :18924)
                            | appends
                            v
                       messages.jsonl  (journal)
                            | tailed in real time (inotify)
                            v
                       relay/detector.py --daemon
                            | filter / merge / route
                            v
                       forward-queue.jsonl  (flock-guarded)
                            | per-conversation wake hooks (10s poll)
                            v
              Muse side chats (one per conversation)
                            | replies via tools/send.py
                            v
                         back to WhatsApp
```

Key design rules (learned in production):

- **The relay is a dumb pipe.** It never summarizes, rewrites, or interprets
  content. Context and memory belong to each side chat, natively.
- **Health is inbound flow, not an HTTP 200.** A half-open WhatsApp socket
  still answers `/health` with `ready: true` while silently dropping sends
  and never receiving. Watchdogs judge liveness by real message flow and
  idle time, and restart gracefully first.
- **Wake is separated from create.** Conversations that already have a side
  chat are woken directly by their own hook. Only brand-new conversations
  (and the owner's direct line) involve the main chat.

## Components

| Path | What it is |
|---|---|
| `gateway/bot.js` | Baileys gateway. HTTP API: `POST /send`, `POST /paircode`, `POST /shutdown`, `POST /group-info`, `GET /health`. Appends every event to `messages.jsonl`. Media download included. |
| `relay/detector.py` | Resident detector (`--daemon`): tails the journal via `relay.js`, filters, merges bursts (5s), tags entries `[reply]` / `[log-only]`, handles the owner's channel-mode triggers, appends to `forward-queue.jsonl` under flock. |
| `relay/relay.js` | Inotify tail of the journal; emits one line per new inbound message; cursor persisted in `.relay_cursor`. |
| `relay/drain-queue.py` | Atomic per-conversation queue drain (`<convoId>`), peek (`--peek`), and main-triage peek (`--peek-main`: unmapped / direct / stale > 10min). Shares the queue lock with the detector. |
| `tools/send.py` | Send CLI used by agents: text, `--mentions`, `--media`, `--caption`. Reads the gateway port from `bridge.config.json`. |
| `ops/` | Self-healing: `gateway-monitor.sh` (revives the gateway, records exit codes), `detector-daemon.sh` (start/stop/status), `detector-cron.sh` (cron guardian: revives the daemon, one-shot backup round only if that fails). |
| `adapters/muse/` | The Muse integration: wake-hook scripts + templates, side-chat instruction template, setup guide. |

## Quick start

Prerequisites: Linux, Node.js 20+, Python 3.10+, a Muse account, and a
spare WhatsApp number on a phone you control.

```bash
git clone <this repo> && cd muse-whatsapp-bridge
./install.sh                      # installs into ~/wa-bridge (flat layout)
cd ~/wa-bridge
cp bridge.example.json bridge.config.json   # then edit: ownerDm, botMentions, triggers
node bot.js                       # first run prints a pairing code flow via /paircode
```

Then wire the Muse side (hooks + side chats) following
`adapters/muse/README.md`, and put the `ops/` guardians on cron
(1–5 minute cadence). See `docs/TROUBLESHOOTING.md` for the failure modes
this stack has already survived — read it before you need it.

## Configuration

One file: `bridge.config.json` (template in `config/bridge.example.json`).

- `ownerDm` — your own DM conversation id with the bot (`...@lid`).
- `ownerLabel` — short name used for your DM side chat (`WA: <label>`).
- `botMentions` — strings that count as "@ the bot" in groups.
- `triggers.toMain` / `triggers.toSidechat` — owner-only DM phrases that
  switch your DM between the main chat and its side chat. They only fire at
  the very start of a message and only for the owner.
- `groupLabels` — optional short labels per group id.
- `gateway.port` — gateway HTTP port (default 18924).

State files (auto-created, git-ignored): `chat-mapping.json`
(convoId -> side chat id), `channel-mode.json` (owner DM routing mode),
`forward-queue.jsonl`, `.relay_cursor`.

## License

MIT. See `LICENSE`.
