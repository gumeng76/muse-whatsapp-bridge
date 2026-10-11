#!/usr/bin/env python3
"""Transcribe an audio file (e.g. an incoming WhatsApp voice note) to text.

Optional agent-side helper. The bridge pipeline itself never interprets
content — it only delivers the media file. When a side chat wants to
*understand* a voice message, it can run this tool locally.

Setup (once):
    python3 -m venv ~/wa-stt
    ~/wa-stt/bin/pip install -r tools/stt-requirements.txt

Usage:
    ~/wa-stt/bin/python tools/transcribe.py <audio-file> [model]
    # model defaults to "small" (good Chinese/English); "base" is faster,
    # "large-v3" is most accurate. The model downloads once (~150MB-3GB)
    # and is cached in ~/.cache/huggingface.

Output: the transcript on stdout; language/duration info on stderr.
"""
import os
import sys

# huggingface_hub's HTTP client can crash parsing proxy config when
# no_proxy contains bracketed IPv6 entries (e.g. "[::1]"). The model
# download only needs the proxy itself, so simplify no_proxy first.
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"

from faster_whisper import WhisperModel  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print("usage: transcribe.py <audio> [model]", file=sys.stderr)
        sys.exit(2)
    audio = sys.argv[1]
    model_name = sys.argv[2] if len(sys.argv) > 2 else "small"
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio, beam_size=5)
    text = "".join(s.text for s in segments).strip()
    print(
        f"[lang={info.language} p={info.language_probability:.2f} "
        f"dur={info.duration:.1f}s model={model_name}]",
        file=sys.stderr,
    )
    print(text)


if __name__ == "__main__":
    main()
