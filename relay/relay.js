// relay.js — dumb event pipe for the main-chat WhatsApp relay worker.
// Watches messages.jsonl via inotify, prints RELAY_EVENT:<json> for each new
// INBOUND enriched message. Smart logic (mapping, chat.create, forwarding)
// lives in the relay worker agent. Never replays history: cursor starts at EOF.
const fs = require('fs');
const path = require('path');

const DIR = __dirname;
// WA_MESSAGES_LOG: which bot log to tail (set by detector.py from active-link.json).
const LOG = path.join(DIR, process.env.WA_MESSAGES_LOG || 'messages.jsonl');
const CURSOR = path.join(DIR, '.relay_cursor');

function getCursor() {
  try { return parseInt(fs.readFileSync(CURSOR, 'utf8').trim(), 10) || 0; } catch { return 0; }
}
function setCursor(n) { fs.writeFileSync(CURSOR, String(n)); }
function readLines() {
  try { return fs.readFileSync(LOG, 'utf8').split('\n').filter(l => l.trim()); }
  catch { return []; }
}

function pump() {
  const lines = readLines();
  const cursor = getCursor();
  if (lines.length <= cursor) return;
  setCursor(lines.length);
  for (const l of lines.slice(cursor)) {
    let m;
    try { m = JSON.parse(l); } catch { continue; }
    if (m.type !== 'message') continue; // inbound enriched only; skips raw/sent/ready/disconnected
    if (!m.from) continue;
    console.log('RELAY_EVENT:' + JSON.stringify({
      from: m.from, to: m.to || '', body: m.body || '', author: m.author || '',
      isGroup: !!m.isGroup, chatName: m.chatName || '', hasMedia: !!m.hasMedia, ts: m.ts || '',
      mediaPath: m.mediaPath || '', mediaType: m.mediaType || '', mediaSize: m.mediaSize || 0,
      mentionedIds: m.mentionedIds || []
    }));
  }
}

if (!fs.existsSync(CURSOR)) {
  setCursor(readLines().length);
  console.log('RELAY_READY cursor=' + getCursor() + ' (history skipped, no replay)');
} else {
  console.log('RELAY_READY cursor=' + getCursor() + ' (resumed)');
}

let debounce = null;
try {
  fs.watch(LOG, (eventType) => {
    if (eventType !== 'change') return;
    clearTimeout(debounce);
    debounce = setTimeout(pump, 300);
  });
  console.log('RELAY_WATCHING ' + LOG);
} catch (e) {
  console.log('RELAY_WATCH_FAIL ' + e.message);
}
setInterval(pump, 20000); // reconciliation sweep in case of missed events
setInterval(() => {}, 60000); // keep alive
