"""Kūhaku's voice: loads Piper once, then turns each JSON line {"text", "path"} read from
stdin into a WAV at that path, answering one line of JSON on stdout. Fully offline."""

import json
import sys
import wave

from piper import PiperVoice

voice = PiperVoice.load(sys.argv[1])
print(json.dumps({"ready": True}), flush=True)

for line in sys.stdin:
    try:
        job = json.loads(line)
        with wave.open(job["path"], "wb") as w:
            voice.synthesize_wav(job["text"], w)
        print(json.dumps({"ok": True}), flush=True)
    except Exception as e:
        print(json.dumps({"error": str(e)}), flush=True)
