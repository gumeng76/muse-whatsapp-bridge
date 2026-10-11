#!/usr/bin/env python3
"""Send a WhatsApp message via the local bot API.
Usage: python3 send.py '<to>' [--mentions id1,id2] [--media /path/to/file] [--caption 'text'] < reply.txt
  <to>   : conversation id, use the message's `from` value verbatim
           (DM contact id like 12345678901234@lid, or group id like xxx@g.us)
  --mentions: optional comma-separated contact ids to @-tag in a group
           (e.g. 146046554509372@lid); each id must also appear as @xxx in text
  --media: optional local file to send as an attachment. For safety the
           gateway only accepts paths inside its media/ directory (plus
           an optional WA_MEDIA_EXTRA_ROOT), max 64 MB.
  --caption: optional caption for the media; defaults to stdin text.
  stdin  : reply text (avoids all shell-quoting issues); used as caption when
           --media is set and --caption is absent.
"""
import json
import os
import sys
import urllib.request

def _send_api():
    # WA_SEND_API env wins; else gateway.port from bridge.config.json
    # (installed next to this script); else the standard port 18924.
    if os.environ.get('WA_SEND_API'):
        return os.environ['WA_SEND_API']
    try:
        cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'bridge.config.json')
        with open(cfg) as f:
            port = json.load(f).get('gateway', {}).get('port', 18924)
        return 'http://127.0.0.1:%d/send' % port
    except Exception:
        return 'http://127.0.0.1:18924/send'

SEND_API = _send_api()

def take_opt(args, flag):
    if flag in args:
        i = args.index(flag)
        if i + 1 >= len(args):
            print(f'SEND_FAILED: {flag} needs a value', file=sys.stderr)
            sys.exit(2)
        val = args[i + 1]
        del args[i:i + 2]
        return val
    return None

def main():
    args = sys.argv[1:]
    mentions = []
    if '--mentions' in args:
        i = args.index('--mentions')
        mentions = [m.strip() for m in args[i + 1].split(',') if m.strip()]
        del args[i:i + 2]
    media = take_opt(args, '--media')
    caption = take_opt(args, '--caption')
    if len(args) != 1:
        print('usage: send.py <to> [--mentions id1,id2] [--media path] [--caption text] < reply.txt',
              file=sys.stderr)
        sys.exit(2)
    to = args[0]
    text = sys.stdin.read()
    if media:
        if caption is None:
            caption = text
        if not caption.strip():
            print('SEND_FAILED: empty caption', file=sys.stderr)
            sys.exit(1)
        payload = {'to': to, 'media': media, 'caption': caption}
    else:
        if not text.strip():
            print('SEND_FAILED: empty text', file=sys.stderr)
            sys.exit(1)
        payload = {'to': to, 'text': text}
    if mentions:
        payload['mentions'] = mentions
    req = urllib.request.Request(
        SEND_API,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=60).read().decode()
        print(resp)
    except Exception as e:
        print('SEND_FAILED: ' + str(e), file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
