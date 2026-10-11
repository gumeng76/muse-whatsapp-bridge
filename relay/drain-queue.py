#!/usr/bin/env python3
"""forward-queue 原子抽干/窥视工具（2026-10-11 唤醒直达改造）。

队列写入方（detector.py 的 q()）与本工具共用 forward-queue.lock 的 flock，
保证多方并发时不丢不重。

用法：
  drain-queue.py <convoId>     原子取走并打印该对话的全部条目（JSONL）
  drain-queue.py --peek <convoId>   只打印该对话条目，不取走
  drain-queue.py --peek-main   只打印归 main 处理的条目：directMain、
                               未在 chat-mapping.json 登记的新对话、
                               或滞留超过 10 分钟的兜底条目
输出统一为 JSONL（每行一条），无匹配时无输出、退出码 0。
"""
import fcntl
import json
import os
import sys
from datetime import datetime, timezone

DIR = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(DIR, "forward-queue.jsonl")
LOCK = os.path.join(DIR, "forward-queue.lock")
MAPPING = os.path.join(DIR, "chat-mapping.json")
STALE_SEC = 600


def read_lines():
    try:
        with open(QUEUE) as f:
            return [l for l in f.read().splitlines() if l.strip()]
    except FileNotFoundError:
        return []


def parse(line):
    try:
        return json.loads(line)
    except Exception:
        return None


def mapped_convos():
    try:
        with open(MAPPING) as f:
            return set(json.load(f).keys())
    except Exception:
        return set()


def is_main_worthy(entry, mapped):
    if entry is None:
        return True  # 坏行交给 main 检视，避免永远滞留
    if entry.get("directMain"):
        return True
    if entry.get("convoId") not in mapped:
        return True
    ts = entry.get("ts") or ""
    try:
        t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - t).total_seconds() > STALE_SEC:
            return True
    except Exception:
        pass
    return False


def main():
    args = sys.argv[1:]
    lockf = open(LOCK, "w")
    fcntl.flock(lockf, fcntl.LOCK_EX)
    try:
        lines = read_lines()
        entries = [parse(l) for l in lines]
        if len(args) == 1 and not args[0].startswith("--"):
            convo = args[0]
            mine = [l for l, e in zip(lines, entries)
                    if e is not None and e.get("convoId") == convo]
            rest = [l for l, e in zip(lines, entries)
                    if not (e is not None and e.get("convoId") == convo)]
            tmp = QUEUE + ".tmp"
            with open(tmp, "w") as f:
                for l in rest:
                    f.write(l + "\n")
            os.replace(tmp, QUEUE)
            for l in mine:
                print(l)
        elif len(args) == 2 and args[0] == "--peek":
            convo = args[1]
            for l, e in zip(lines, entries):
                if e is not None and e.get("convoId") == convo:
                    print(l)
        elif len(args) == 1 and args[0] == "--peek-main":
            mapped = mapped_convos()
            for l, e in zip(lines, entries):
                if is_main_worthy(e, mapped):
                    print(l)
        else:
            print(__doc__, file=sys.stderr)
            return 2
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
