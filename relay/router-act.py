#!/usr/bin/env python3
"""Deterministic routing-action executor for the owner-DM command layer.

Used together with docs/router-rules.md: the classifier worker outputs only
a label, then calls this script to perform the routing action. This script
makes no decisions -- it is purely program-driven.

Reads owner identity from bridge.config.json next to this script
(`ownerDm`, `ownerLabel`).

Usage:
  router-act.py switch-main            # channel-mode.json -> main
  router-act.py switch-sidechat        # channel-mode.json -> sidechat
  router-act.py replymode voice|text   # switch reply-mode.json
  router-act.py requeue-main --body "<text>" [--from-name <label>]
      # re-queue as a directMain entry in forward-queue.jsonl (flock),
      # for the queue-wake hook to deliver to the main chat
"""
import fcntl
import json
import os
import sys
from datetime import datetime, timezone

DIR = os.path.dirname(os.path.abspath(__file__))
CHANNEL_MODE = os.path.join(DIR, "channel-mode.json")
REPLY_MODE = os.path.join(DIR, "reply-mode.json")
QUEUE = os.path.join(DIR, "forward-queue.jsonl")
LOCK = os.path.join(DIR, "forward-queue.lock")
CONFIG = os.path.join(DIR, "bridge.config.json")


def _config():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except Exception:
        return {}


_CFG = _config()
OWNER_DM = _CFG.get("ownerDm", "")
OWNER_LABEL = _CFG.get("ownerLabel", "owner")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def switch_main():
    cur = read_json(CHANNEL_MODE)
    cur.update({"dm_mode": "main", "updated_ts": now_iso(),
                "note": "sidechat=owner DMs go to the DM side chat; "
                        "main=owner DMs go straight to the main chat"})
    write_json(CHANNEL_MODE, cur)
    print("channel-mode.json -> main")


def switch_sidechat():
    cur = read_json(CHANNEL_MODE)
    cur.update({"dm_mode": "sidechat", "updated_ts": now_iso(),
                "note": "sidechat=owner DMs go to the DM side chat; "
                        "main=owner DMs go straight to the main chat"})
    write_json(CHANNEL_MODE, cur)
    print("channel-mode.json -> sidechat")


def replymode(mode):
    if mode not in ("voice", "text"):
        print("replymode arg must be voice|text", file=sys.stderr)
        return 2
    cur = read_json(REPLY_MODE)
    cur.update({"dm_reply_mode": mode, "updated_ts": now_iso(),
                "note": "text=default text replies; voice=default voice-note "
                        "replies (long content/links still go as text)"})
    write_json(REPLY_MODE, cur)
    print("reply-mode.json -> %s" % mode)
    return 0


def requeue_main(body, from_name=None):
    body = (body or "").strip()
    if not body:
        print("requeue-main: empty body, skipped", file=sys.stderr)
        return 2
    from_name = from_name or OWNER_LABEL
    entry = {"ts": now_iso(), "convoId": OWNER_DM, "fromName": from_name,
             "body": body, "needReply": True, "directMain": True,
             "sideChatName": "WA: " + OWNER_LABEL,
             "forwardText": "[WhatsApp direct] %s: %s" % (from_name, body)}
    line = json.dumps(entry, ensure_ascii=False)
    lockf = open(LOCK, "w")
    fcntl.flock(lockf, fcntl.LOCK_EX)
    try:
        with open(QUEUE, "a") as f:
            f.write(line + "\n")
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()
    print("requeue-main ok: %d chars" % len(body))
    return 0


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = args[0]
    if cmd == "switch-main":
        switch_main()
    elif cmd == "switch-sidechat":
        switch_sidechat()
    elif cmd == "replymode":
        if len(args) < 2:
            print("replymode needs an argument", file=sys.stderr)
            return 2
        return replymode(args[1])
    elif cmd == "requeue-main":
        body = ""
        from_name = None
        i = 1
        while i < len(args):
            if args[i] == "--body" and i + 1 < len(args):
                body = args[i + 1]
                i += 2
            elif args[i] == "--from-name" and i + 1 < len(args):
                from_name = args[i + 1]
                i += 2
            else:
                i += 1
        return requeue_main(body, from_name)
    else:
        print("unknown command: %s" % cmd, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
