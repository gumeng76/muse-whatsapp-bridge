#!/usr/bin/env python3
"""WhatsApp 消息探测器（单次任务制）。
启动 relay.js 监听 messages.jsonl，新入站消息过滤组装后写入 forward-queue.jsonl，
一批处理完（或 30 分钟无事件）即结束。"""
import fcntl, json, os, re, sys, time, subprocess, signal

DIR = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(DIR, "forward-queue.jsonl")
MODE_FILE = os.path.join(DIR, "channel-mode.json")
OUTLOG = "/tmp/relay-out.log"

# --- everything installation-specific lives in bridge.config.json ---
def _load_cfg():
    try:
        with open(os.path.join(DIR, "bridge.config.json")) as f:
            return json.load(f)
    except Exception:
        return {}
CFG = _load_cfg()
BOT_MENTIONS = CFG.get("botMentions", [])
OWNER_DM = CFG.get("ownerDm", "")
OWNER_LABEL = CFG.get("ownerLabel", "owner")
TRIG_MAIN = CFG.get("triggers", {}).get("toMain", "talk to main")
TRIG_SIDE = CFG.get("triggers", {}).get("toSidechat", ["back to side chat"])
MERGE_WINDOW = 5.0      # 同一 convoId 5 秒内连续消息合并
FLUSH_IDLE = 7.0        # 超过此秒数无新事件则刷出本轮
ABS_TIMEOUT = 30 * 60   # 兜底超时

def read_mode():
    try:
        with open(MODE_FILE) as f:
            return json.load(f).get("dm_mode", "sidechat")
    except Exception:
        return "sidechat"

def write_mode(mode):
    with open(MODE_FILE, "w") as f:
        json.dump({"dm_mode": mode,
                   "updated_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "note": "sidechat=owner DM to its side chat; main=owner DM straight to main chat"},
                  f, ensure_ascii=False)

def q(entry):
    # 与 drain-queue.py 共用同一把 flock：抽干（读-改-写）与追加互斥，防丢防重。
    lockf = open(os.path.join(DIR, "forward-queue.lock"), "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        with open(QUEUE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()

def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# Optional short labels per group (config groupLabels). Without a label
# the group subject is used; last fallback is an id prefix, which can
# collide for groups sharing a prefix, hence explicit labels.
# chatName 在当前环境常年取不到（见 AGENTS.md：client.getChatById 已坏），
KNOWN_GROUP_LABELS = CFG.get("groupLabels", {})

def group_name(ev):
    lbl = KNOWN_GROUP_LABELS.get(ev["from"])
    if lbl:
        return lbl
    cn = (ev.get("chatName") or "").strip()
    if cn:
        return cn
    return ev["from"].split("@")[0][:8]

pending = {}      # convoId -> entry（待合并）
reports = []      # 已处理报告行

def add_or_merge(convo, entry):
    """同一 convoId 5 秒内连续消息合并：body 换行拼接，forwardText 同步重建。"""
    old = pending.get(convo)
    if old and time.time() - old["_arrived"] < MERGE_WINDOW:
        old["body"] += "\n" + entry["body"]
        old["fwdTail"] = (old["fwdTail"] + entry["fwdTail"]).strip()
        old["forwardText"] = old["fwdHead"] + old["body"] + old["fwdTail"]
        return
    entry["_arrived"] = time.time()
    entry["forwardText"] = entry["fwdHead"] + entry["body"] + entry["fwdTail"]
    pending[convo] = entry

def finalize(e):
    e = dict(e)
    e.pop("_arrived", None)
    e.pop("fwdHead", None)
    e.pop("fwdTail", None)
    return e

# ---- 启动 relay.js ----
# 入站来源由 active-link.json 决定（2026-10-11 切换：Baileys 为主链路，
# 读 messages-baileys.jsonl；文件缺失/无此键则回退旧链路 messages.jsonl）。
# 每次（重）启动 relay 都重读一遍，切换链路时 daemon 无需重启即可跟随。
def active_inbound_log():
    try:
        with open(os.path.join(DIR, "active-link.json")) as f:
            return json.load(f).get("inbound_log") or "messages.jsonl"
    except Exception:
        return "messages.jsonl"

# 常驻模式（2026-10-11 程序化改造）：--daemon 时 flush 后不退出、relay 死了
# 自动重启、每 30 秒 touch .relay_cursor 心跳；无参数时保持原单次行为。
DAEMON = "--daemon" in sys.argv

def start_relay():
    relay_env = dict(os.environ, WA_MESSAGES_LOG=active_inbound_log())
    proc = subprocess.Popen(["node", "relay.js"], cwd=DIR, env=relay_env,
                            stdout=open(OUTLOG, "w"), stderr=subprocess.STDOUT,
                            start_new_session=True)
    t0 = time.time()
    while time.time() - t0 < 30:
        try:
            with open(OUTLOG) as f:
                if "RELAY_READY" in f.read():
                    print("RELAY 启动 OK，开始探测", flush=True)
                    return proc
        except FileNotFoundError:
            pass
        time.sleep(0.5)
    print("relay.js 启动失败：30 秒未见 RELAY_READY", flush=True)
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        pass
    return None

relay = start_relay()
if relay is None:
    sys.exit(1)

if DAEMON:
    def _term(signum, frame):
        try:
            os.killpg(relay.pid, signal.SIGTERM)
        except Exception:
            pass
        sys.exit(0)
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

last_event = time.time()
start = time.time()

def handle(ev):
    if ev.get("fromMe"):
        return
    convo = ev["from"]
    body = (ev.get("body") or "").strip()
    if not body and not ev.get("hasMedia"):
        return  # 非文本且无媒体的系统事件跳过
    author = ev.get("author") or ""
    ts = ev.get("ts") or now_iso()
    fwdTail = ""
    if ev.get("hasMedia") and ev.get("mediaPath"):
        fwdTail += " [media: %s]" % ev["mediaPath"]
    mentioned = ev.get("mentionedIds") or []
    if mentioned:
        fwdTail += " [mentioned: %s]" % ",".join(mentioned)

    is_dm = convo.endswith("@lid")
    is_owner_dm = (convo == OWNER_DM)  # 直连通道仅限owner DM
    if is_dm:
        nick = author or convo.split("@")[0][:8]
        # --- 直连通道切换（仅 DM）---
        # 触发语必须出现在开头（允许"和main说：..."带内容），避免正文提到触发语被误判
        m_main = (re.match(r'^\s*' + re.escape(TRIG_MAIN) + r'\s*[:：]?\s*(.*)$', body, re.S) if is_owner_dm else None)
        m_back = (re.match(r'^\s*(' + '|'.join(re.escape(t) for t in TRIG_SIDE) + r')\s*[:：]?\s*(.*)$', body, re.S) if is_owner_dm else None)
        if m_main:
            write_mode("main")
            q({"ts": ts, "convoId": convo, "fromName": author, "body": body,
               "needReply": False, "directMain": False, "sideChatName": "WA: " + OWNER_LABEL,
               "forwardText": "[system] Direct channel ON (say '" + TRIG_SIDE[0] + "' to switch back)"})
            reports.append(("系统", convo, "WA: " + OWNER_LABEL, "→main", "直连通道已切换到 main chat"))
            rest = m_main.group(1).strip()
            if rest:
                # 触发语后面带的内容作为 directMain 消息一并转发，不吞掉
                entry = {"ts": ts, "convoId": convo, "fromName": author, "body": rest,
                         "needReply": True, "directMain": True, "sideChatName": "WA: " + OWNER_LABEL,
                         "forwardText": "[WhatsApp direct] %s: %s" % (author, rest)}
                q(entry)
                reports.append(("直连", convo, "WA: " + OWNER_LABEL, "directMain", rest[:60]))
            return
        if m_back:
            write_mode("sidechat")
            q({"ts": ts, "convoId": convo, "fromName": author, "body": body,
               "needReply": False, "directMain": False, "sideChatName": "WA: " + OWNER_LABEL,
               "forwardText": "[system] Back to the side chat channel"})
            reports.append(("系统", convo, "WA: " + OWNER_LABEL, "→sidechat", "已切回 side chat 通道"))
            rest = m_back.group(2).strip()
            if rest:
                entry = {"ts": ts, "convoId": convo, "fromName": author, "body": rest,
                         "needReply": True, "sideChatName": "WA: %s" % nick,
                         "forwardText": "[需回复]【WhatsApp 单聊｜%s】\n%s: %s" % (nick, author, rest)}
                q(entry)
                reports.append(("单聊", convo, "WA: %s" % nick, "需回复", rest[:60]))
            return
        entry = {"ts": ts, "convoId": convo, "fromName": author, "body": body,
                 "needReply": True, "sideChatName": "WA: %s" % nick,
                 "fwdHead": "[需回复]【WhatsApp 单聊｜%s】\n%s: " % (nick, author),
                 "fwdTail": fwdTail}
        if is_owner_dm and read_mode() == "main":
            entry["directMain"] = True
        add_or_merge(convo, entry)
        return

    if convo.endswith("@g.us"):
        gname = group_name(ev)
        hit = any(k in body for k in BOT_MENTIONS)
        tag = "reply" if hit else "log-only"
        add_or_merge(convo, {"ts": ts, "convoId": convo, "fromName": author, "body": body,
                             "needReply": hit, "sideChatName": "WA群: %s" % gname,
                             "fwdHead": "[%s][WhatsApp group | %s]\n%s: " % (tag, gname, author),
                             "fwdTail": fwdTail})
        return

    # 未知类型后缀，保守当单聊处理
    nick = author or convo.split("@")[0][:8]
    add_or_merge(convo, {"ts": ts, "convoId": convo, "fromName": author, "body": body,
                         "needReply": True, "sideChatName": "WA: %s" % nick,
                         "fwdHead": "[需回复]【WhatsApp 单聊｜%s】\n%s: " % (nick, author),
                         "fwdTail": fwdTail})

def flush():
    for convo, e in pending.items():
        q(finalize(e))
        nr = "reply" if e["needReply"] else "log-only"
        dm = " directMain" if e.get("directMain") else ""
        reports.append(("消息", convo, e["sideChatName"], nr + dm,
                        e["forwardText"][:80].replace("\n", " ")))
    pending.clear()

pos = os.path.getsize(OUTLOG)
last_touch = time.time()
while True:
    # relay 存活检查：relay 静默死亡时 detector 旧版会假阴性空转（2026-10-06 教训）。
    # daemon 模式自动重启 relay；单次模式退出本轮，收尾段会汇报。
    if relay.poll() is not None:
        print("relay.js 已退出 (code=%s)" % relay.returncode, flush=True)
        if not DAEMON:
            break
        new_relay = start_relay()
        if new_relay is None:
            time.sleep(5)
            continue
        relay = new_relay
        pos = os.path.getsize(OUTLOG)
        last_event = time.time()
        start = time.time()
    with open(OUTLOG) as f:
        f.seek(pos)
        data = f.read()
        pos = f.tell()
    for line in data.splitlines():
        if line.startswith("RELAY_EVENT:"):
            try:
                handle(json.loads(line[len("RELAY_EVENT:"):]))
                last_event = time.time()
            except Exception as e:
                print("解析事件失败: %s" % e, flush=True)
    if (pending or reports) and time.time() - last_event > FLUSH_IDLE:
        flush()
        if not DAEMON:
            break
        for kind, convo, schat, nr, preview in reports:
            print("FLUSH: [%s] %s | %s | %s | %s" % (kind, convo, schat, nr, preview), flush=True)
        reports.clear()
        start = time.time()
        last_event = time.time()
    if time.time() - start > ABS_TIMEOUT:
        if not DAEMON:
            break
        start = time.time()
    if DAEMON and time.time() - last_touch > 30:
        last_touch = time.time()
        try:
            os.utime(os.path.join(DIR, ".relay_cursor"), None)
        except Exception:
            pass
    time.sleep(1)

# ---- 收尾：杀 relay ----
try:
    os.killpg(relay.pid, signal.SIGTERM)
except Exception:
    pass
time.sleep(1)
left = subprocess.run(["pgrep", "-f", "node relay.js"], capture_output=True, text=True)
if left.stdout.strip():
    print("警告：仍有 relay.js 残留: " + left.stdout.strip(), flush=True)
else:
    print("relay.js 已退出，无残留", flush=True)

# ---- 汇报 ----
if not reports:
    print("RESULT: 本轮无新消息")
else:
    print("RESULT: 本轮新增 %d 条" % len(reports))
    for kind, convo, schat, nr, preview in reports:
        print("  - [%s] %s | %s | %s | %s" % (kind, convo, schat, nr, preview))

# ---- 心跳：无论有无新消息都刷新 cursor 文件的 mtime ----
# relay.js 只在有新事件时才更新 cursor 内容；零流量时 mtime 停滞，
# 会导致 detector-cron.sh 误判 stale 而每 2 分钟空转一轮（2026-10-07 实测）。
# 补一次 touch，让 cron 的"2 分钟未更新→stale"语义回到"上次跑完多久"。
try:
    cursor_path = os.path.join(DIR, ".relay_cursor")
    if os.path.exists(cursor_path):
        os.utime(cursor_path, None)
except Exception as e:
    print("cursor 心跳 touch 失败: %s" % e, flush=True)
