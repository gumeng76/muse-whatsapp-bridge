#!/usr/bin/env bash
# muse-whatsapp-bridge installer.
# The runtime layout is FLAT: all components live side by side in one
# install directory (the scripts find each other by sibling path).
# This script copies the repo's components into ~/wa-bridge accordingly.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DST="${WA_BRIDGE_HOME:-$HOME/wa-bridge}"

echo "Installing into $DST"
mkdir -p "$DST"

cp "$SRC/gateway/bot.js" "$DST/bot.js"
cp "$SRC/gateway/package.json" "$DST/package.json"
cp "$SRC/relay/detector.py" "$DST/detector.py"
cp "$SRC/relay/relay.js" "$DST/relay.js"
cp "$SRC/relay/drain-queue.py" "$DST/drain-queue.py"
cp "$SRC/relay/router-act.py" "$DST/router-act.py"
cp "$SRC/tools/send.py" "$DST/send.py"
cp "$SRC/ops/gateway-monitor.sh" "$DST/gateway-monitor.sh"
cp "$SRC/ops/detector-daemon.sh" "$DST/detector-daemon.sh"
cp "$SRC/ops/detector-cron.sh" "$DST/detector-cron.sh"
chmod +x "$DST"/*.sh "$DST"/*.py

if [ ! -f "$DST/bridge.config.json" ]; then
    cp "$SRC/config/bridge.example.json" "$DST/bridge.config.json"
    echo "Created $DST/bridge.config.json from the example — EDIT IT before starting."
fi
if [ ! -f "$DST/chat-mapping.json" ]; then
    echo "{}" > "$DST/chat-mapping.json"
fi

cd "$DST"
if [ ! -d node_modules ]; then
    echo "Installing Node dependencies (Baileys)..."
    npm install --omit=dev
fi

cat <<'EOF'

Done. Next steps:
  1. Edit bridge.config.json (ownerDm, botMentions, triggers).
  2. Start the gateway:   node bot.js
     Pair the number:    POST /paircode (see gateway/bot.js), or scan the QR.
  3. Start the detector:  ./detector-daemon.sh start
  4. Wire the Muse side: follow adapters/muse/README.md in the repo
     (copy its hook scripts into ~/hooks/scripts/ first).
  5. Put the guardians on cron, e.g.:
       */5 * * * *  ~/wa-bridge/gateway-monitor.sh
       */2 * * * *  ~/wa-bridge/detector-cron.sh
  6. Read docs/TROUBLESHOOTING.md before you need it.
EOF
