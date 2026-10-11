// Baileys-based WhatsApp gateway: WebSocket direct connection, no browser.
// HTTP API on 127.0.0.1:<BAILEYS_PORT|18924>: POST /send, /paircode,
// /shutdown, /group-info; GET /health. Every inbound/outbound event is
// appended to a JSONL journal (default messages.jsonl) that the relay
// layer tails.
//
// Auth state lives in auth/baileys/ (multi-file auth state; keep it
// private, never commit it).
// Test mode: BAILEYS_TEST=1 -> journal goes to messages-test.jsonl and
// /send is disabled. BAILEYS_LOG=<file> overrides the journal filename.
const {
    makeWASocket,
    useMultiFileAuthState,
    DisconnectReason,
    downloadMediaMessage,
    getContentType,
} = require('@whiskeysockets/baileys');
const { HttpsProxyAgent } = require('https-proxy-agent');
const fs = require('fs');
const http = require('http');
const path = require('path');

const TEST_MODE = process.env.BAILEYS_TEST === '1';
// BAILEYS_LOG overrides the inbound log filename (used since 2026-10-11 cutover:
// production Baileys writes messages-baileys.jsonl so the old bot can keep
// running in parallel on messages.jsonl without double-feeding the detector).
const LOG = path.join(__dirname, process.env.BAILEYS_LOG || (TEST_MODE ? 'messages-test.jsonl' : 'messages.jsonl'));
const SEND_PORT = parseInt(process.env.BAILEYS_PORT || '18924', 10);
// Hardcoded WA Web version tuple: fetchLatestBaileysVersion() hits GitHub
// (blocked/unreliable here), so we pin and never call it.
const WA_VERSION = [2, 3000, 1043857760]; // Updated 2026-10-10: was outdated, caused 405

function logMsg(entry) {
    fs.appendFileSync(LOG, JSON.stringify(entry) + '\n');
}

// 深层健康状态：追踪 WhatsApp 连接真实状态，而不仅是 HTTP 存活
const connState = {
    ready: false,
    authenticated: false,
    lastMessageTs: 0,      // 最后收到消息事件的时间戳
    lastHealthTs: Date.now(),
    readyAt: 0,
    lastDisconnect: null,
};

// groupMetadata 缓存（5 分钟），避免每次消息都查群信息
const groupMetaCache = new Map(); // jid -> { subject, at }
const GROUP_META_TTL = 5 * 60 * 1000;
async function getGroupSubject(sock, jid) {
    const cached = groupMetaCache.get(jid);
    if (cached && Date.now() - cached.at < GROUP_META_TTL) return cached.subject;
    try {
        const meta = await sock.groupMetadata(jid);
        const subject = meta.subject || '';
        groupMetaCache.set(jid, { subject, at: Date.now() });
        return subject;
    } catch (e) {
        console.error('groupMetadata failed for', jid, ':', e.message);
        return (cached && cached.subject) || '';
    }
}

// Media download: save attachments to media/<YYYY-MM-DD>/ with sanitized names.
const MEDIA_DIR = path.join(__dirname, 'media');
function sanitizeFilename(name) {
    const base = path.basename(name || 'file').slice(0, 80);
    return base.replace(/[^a-zA-Z0-9._-]/g, '_') || 'file';
}
async function saveIncomingMedia(msg) {
    const buffer = await downloadMediaMessage(msg, 'buffer', {});
    if (!buffer || !buffer.length) throw new Error('empty media data');
    const contentType = getContentType(msg.message);
    // best-effort mimetype + original filename from the message payload
    const inner = msg.message[contentType] || {};
    const mimetype = inner.mimetype || 'application/octet-stream';
    const day = new Date().toISOString().slice(0, 10);
    const dir = path.join(MEDIA_DIR, day);
    fs.mkdirSync(dir, { recursive: true });
    const ext = (mimetype.split('/')[1] || 'bin').split(';')[0];
    const name = Date.now() + '_' + sanitizeFilename(inner.fileName || ('media.' + ext));
    const filePath = path.join(dir, name);
    fs.writeFileSync(filePath, buffer);
    return { path: filePath, mimetype, size: fs.statSync(filePath).size };
}

function extractBody(msg) {
    const m = msg.message || {};
    return m.conversation
        || (m.extendedTextMessage && m.extendedTextMessage.text)
        || '';
}

let sock = null;
let shuttingDown = false;
const bootTs = Date.now(); // 只处理启动之后的新消息，防止历史同步污染

async function createSocket() {
    const { state, saveCreds } = await useMultiFileAuthState(
        path.join(__dirname, 'auth', 'baileys')
    );
    const proxyAgent = new HttpsProxyAgent('http://127.0.0.1:8899');
    const s = makeWASocket({
        version: WA_VERSION,
        auth: state,
        agent: proxyAgent,
        fetchAgent: proxyAgent,
        // 配对码模式：不扫二维码
        printQRInTerminal: false,
        // 不同步完整历史，只收新消息
        syncFullHistory: false,
    });
    s.ev.on('creds.update', saveCreds);

    s.ev.on('connection.update', async (update) => {
        const { connection, lastDisconnect } = update;
        if (connection === 'open') {
            console.log('BOT_READY');
            connState.ready = true;
            connState.authenticated = true;
            connState.readyAt = Date.now();
            logMsg({ type: 'ready', ts: new Date().toISOString(), backend: 'baileys' });
        } else if (connection === 'close') {
            const code = lastDisconnect?.error?.output?.statusCode;
            const reason = Object.keys(DisconnectReason).find(
                k => DisconnectReason[k] === code
            ) || code;
            connState.ready = false;
            connState.lastDisconnect = { code, reason, ts: new Date().toISOString() };
            console.log('DISCONNECTED:' + reason + ' (' + code + ')');
            logMsg({ type: 'disconnected', ts: new Date().toISOString(), reason, code, backend: 'baileys' });
            if (shuttingDown) return;
            // 401 loggedOut: 会话真死，看门狗不许重启，等人工重配对
            if (code === DisconnectReason.loggedOut) {
                connState.authenticated = false;
                console.error('LOGGED_OUT: 需重配对，看门狗请勿重启');
                logMsg({ type: 'logged_out_needs_repair', ts: new Date().toISOString() });
                return;
            }
            // 其他原因：指数退避重建 socket
            const delays = [30_000, 60_000, 120_000];
            for (let i = 0; i < delays.length; i++) {
                console.log(`RECONNECT attempt ${i + 1} in ${delays[i] / 1000}s...`);
                await new Promise(r => setTimeout(r, delays[i]));
                if (shuttingDown) return;
                try {
                    try { s.end(undefined); } catch (_) { /* already dead */ }
                    sock = await createSocket();
                    console.log('RECONNECTED ok');
                    logMsg({ type: 'reconnected', ts: new Date().toISOString(), attempt: i + 1 });
                    return;
                } catch (e) {
                    console.log('RECONNECT failed: ' + (e && e.message || e));
                }
            }
            console.log('RECONNECT exhausted, exiting for watchdog');
            logMsg({ type: 'reconnect_giveup', ts: new Date().toISOString() });
            process.exit(2);
        } else if (connection === 'connecting') {
            console.log('EVENT:connecting');
        }
    });

    s.ev.on('messages.upsert', async ({ messages, type }) => {
        if (type !== 'notify') return; // 只处理实时推送，不碰历史同步
        for (const msg of messages) {
            try {
                await handleIncoming(s, msg);
            } catch (e) {
                console.error('msg handler error:', (e && e.stack || e).toString().slice(0, 200));
            }
        }
    });

    return s;
}

async function handleIncoming(sock, msg) {
    connState.lastMessageTs = Date.now();
    const key = msg.key || {};
    const remoteJid = key.remoteJid || '';
    // 过滤：自己发的、broadcast、协议消息、启动前历史
    if (key.fromMe) return;
    if (remoteJid === 'status@broadcast') return;
    if (msg.message && msg.message.protocolMessage) return;
    const msgTs = (msg.messageTimestamp || 0) * 1000;
    if (msgTs && msgTs < bootTs - 60_000) return; // 启动前 1 分钟以上的不同步

    const body = extractBody(msg);
    // Log raw first so nothing is lost even if enrichment fails
    try {
        logMsg({
            type: 'message_raw',
            ts: new Date().toISOString(),
            from: remoteJid,
            to: '',
            body: body
        });
    } catch (e) { console.error('raw log failed:', e.message); }

    const isGroup = remoteJid.endsWith('@g.us');
    const chatName = isGroup ? await getGroupSubject(sock, remoteJid) : '';
    const ext = (msg.message && msg.message.extendedTextMessage) || {};
    const mentionedIds = (ext.contextInfo && ext.contextInfo.mentionedJid) || [];
    const contentType = getContentType(msg.message);
    const hasMedia = !!contentType && [
        'imageMessage', 'videoMessage', 'audioMessage',
        'documentMessage', 'stickerMessage'
    ].includes(contentType);

    const entry = {
        type: 'message',
        ts: new Date().toISOString(),
        from: remoteJid,
        to: '',
        chatName: chatName,
        isGroup: isGroup,
        author: msg.pushName || '',
        body: body,
        hasMedia: hasMedia,
        mentionedIds: mentionedIds || []
    };
    if (hasMedia) {
        try {
            const saved = await saveIncomingMedia(msg);
            entry.mediaPath = saved.path;
            entry.mediaType = saved.mimetype;
            entry.mediaSize = saved.size;
            console.log(`MEDIA saved: ${saved.path} (${saved.size} bytes)`);
        } catch (e) {
            entry.mediaError = (e && e.message || e).toString().slice(0, 200);
            console.error('media download failed:', entry.mediaError);
        }
    }
    logMsg(entry);
    console.log(`MSG [${entry.isGroup ? 'GROUP:' + entry.chatName : 'DM:' + entry.author}]: ${entry.body.slice(0, 80)}`);
}

// ---- HTTP API ----
// POST /send {to, text, mentions?, media?, caption?}
// Media sends are restricted to the local media/ dir plus an optional
// extra root (env WA_MEDIA_EXTRA_ROOT), 64 MB max.
const MEDIA_ROOTS = [
    path.resolve(__dirname, 'media'),
    ...(process.env.WA_MEDIA_EXTRA_ROOT ? [path.resolve(process.env.WA_MEDIA_EXTRA_ROOT)] : []),
];
const MEDIA_MAX_BYTES = 64 * 1024 * 1024;
function resolveMedia(p) {
    const abs = path.resolve(p);
    const real = fs.realpathSync(abs);
    if (!MEDIA_ROOTS.some(r => real === r || real.startsWith(r + path.sep))) {
        throw new Error('media path outside allowed roots');
    }
    const st = fs.statSync(real);
    if (!st.isFile()) throw new Error('media is not a file');
    if (st.size > MEDIA_MAX_BYTES) throw new Error('media exceeds 64 MB');
    return real;
}
function mimeForFile(file) {
    const ext = path.extname(file).toLowerCase();
    const map = {
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
        '.gif': 'image/gif', '.webp': 'image/webp',
        '.mp4': 'video/mp4', '.mov': 'video/quicktime',
        '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg', '.wav': 'audio/wav',
        '.pdf': 'application/pdf',
    };
    return map[ext] || 'application/octet-stream';
}
function isImageExt(file) {
    return ['.jpg', '.jpeg', '.png', '.gif', '.webp'].includes(path.extname(file).toLowerCase());
}
function isAudioExt(file) {
    return ['.ogg', '.opus', '.mp3', '.m4a', '.aac', '.wav'].includes(path.extname(file).toLowerCase());
}

// sendMessage can stall indefinitely inside Baileys' media upload
// (seen in production: a 152KB image hung >5min with no error). Bound it:
// 45s per attempt, one retry, then fail loudly so callers never hang.
async function sendWithRetry(chatId, content) {
    let lastErr;
    for (let attempt = 1; attempt <= 2; attempt++) {
        try {
            return await Promise.race([
                sock.sendMessage(chatId, content),
                new Promise((_, reject) => setTimeout(
                    () => reject(new Error(`sendMessage timeout (attempt ${attempt})`)), 45000)),
            ]);
        } catch (e) {
            lastErr = e;
            if (attempt < 2) await new Promise(r => setTimeout(r, 2000));
        }
    }
    throw lastErr;
}

const server = http.createServer(async (req, res) => {
    if (req.method === 'POST' && req.url === '/send') {
        let body = '';
        req.on('data', c => body += c);
        req.on('end', async () => {
            try {
                if (TEST_MODE) throw new Error('test mode: send disabled');
                const { to, text, mentions, media, caption } = JSON.parse(body);
                let chatId = to;
                if (!to.includes('@')) {
                    const num = to.replace(/[^0-9]/g, '');
                    chatId = num + '@s.whatsapp.net';
                }
                const msgOpts = {};
                if (Array.isArray(mentions) && mentions.length) {
                    msgOpts.mentions = mentions; // Baileys 直接接受 JID 数组
                }
                if (media) {
                    const file = resolveMedia(media);
                    const cap = caption || text || '';
                    const data = fs.readFileSync(file);
                    if (isAudioExt(file)) {
                        // Voice note (PTT). WhatsApp wants Opus-in-OGG for a
                        // proper voice-message bubble; other audio formats
                        // are sent as plain audio. Captions don't apply.
                        const ext = path.extname(file).toLowerCase();
                        const audioMsg = { audio: data, ptt: true, ...msgOpts };
                        audioMsg.mimetype = (ext === '.ogg' || ext === '.opus')
                            ? 'audio/ogg; codecs=opus' : mimeForFile(file);
                        await sendWithRetry(chatId, audioMsg);
                    } else if (isImageExt(file)) {
                        await sendWithRetry(chatId, {
                            image: data, caption: cap, ...msgOpts
                        });
                    } else {
                        await sendWithRetry(chatId, {
                            document: data,
                            mimetype: mimeForFile(file),
                            fileName: path.basename(file),
                            caption: cap, ...msgOpts
                        });
                    }
                    logMsg({ type: 'sent', ts: new Date().toISOString(), to: chatId, media: file, caption: cap });
                } else {
                    await sendWithRetry(chatId, { text: text || '', ...msgOpts });
                    logMsg({ type: 'sent', ts: new Date().toISOString(), to: chatId, text });
                }
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ ok: true }));
            } catch (e) {
                res.writeHead(500, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ ok: false, error: e.message }));
            }
        });
    } else if (req.method === 'POST' && req.url === '/paircode') {
        let body = '';
        req.on('data', c => body += c);
        req.on('end', async () => {
            try {
                const { phone } = JSON.parse(body);
                const digits = phone.replace(/[^0-9]/g, '');
                const code = await sock.requestPairingCode(digits);
                console.log('PAIRCODE:' + code);
                logMsg({ type: 'paircode_issued', ts: new Date().toISOString(), phone: digits });
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ ok: true, code }));
            } catch (e) {
                res.writeHead(500, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ ok: false, error: e.message }));
            }
        });
    } else if (req.method === 'POST' && req.url === '/shutdown') {
        let body = '';
        req.on('data', c => body += c);
        req.on('end', async () => {
            console.log('SHUTDOWN requested');
            logMsg({ type: 'shutdown', ts: new Date().toISOString() });
            shuttingDown = true;
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ ok: true }));
            setTimeout(() => {
                try { sock && sock.end(undefined); } catch (_) { /* ignore */ }
                process.exit(0);
            }, 500);
        });
    } else if (req.method === 'POST' && req.url === '/group-info') {
        let body = '';
        req.on('data', c => body += c);
        req.on('end', async () => {
            try {
                const { id } = JSON.parse(body);
                const meta = await sock.groupMetadata(id);
                const participants = (meta.participants || []).map(p => ({
                    id: p.id,
                    pushname: '',
                    isAdmin: p.admin === 'admin' || p.admin === 'superadmin',
                    isSuperAdmin: p.admin === 'superadmin'
                }));
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ ok: true, name: meta.subject || '', participants }));
            } catch (e) {
                console.error('GROUP_INFO_ERROR:', (e && e.stack || e).toString().slice(0, 500));
                res.writeHead(500, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ ok: false, error: (e && e.message || String(e)).slice(0, 200) }));
            }
        });
    } else if (req.url === '/health') {
        const now = Date.now();
        const idleSec = connState.lastMessageTs ? Math.floor((now - connState.lastMessageTs) / 1000) : -1;
        const status = {
            ok: true,
            ready: connState.ready,
            authenticated: connState.authenticated,
            uptimeSec: Math.floor((now - connState.readyAt) / 1000),
            idleSec: idleSec,
            // zombie 判定：已 ready 但超过 15 分钟无消息事件
            zombie: connState.ready && idleSec > 900
        };
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(status));
    } else {
        res.writeHead(404); res.end();
    }
});

(async () => {
    console.log(TEST_MODE ? 'Baileys bot starting in TEST mode...' : 'Baileys bot starting...');
    sock = await createSocket();
    server.listen(SEND_PORT, '127.0.0.1', () => {
        console.log('SEND_API on 127.0.0.1:' + SEND_PORT);
    });
})();
