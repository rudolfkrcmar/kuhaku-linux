"""Kūhaku's dictation worker: loads Whisper once, then turns each 16 kHz mono WAV
path read from stdin into one line of JSON text on stdout. Fully offline."""
import json
import sys
import wave

import numpy as np
from faster_whisper import WhisperModel

model = WhisperModel(sys.argv[1] if len(sys.argv) > 1 else "base", device="cpu", compute_type="int8")
print(json.dumps({"ready": True}), flush=True)

for path in sys.stdin:
    try:
        with wave.open(path.strip()) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        text = ""
        if audio.size >= 8000:  # under half a second: nothing said
            segments, _info = model.transcribe(audio, beam_size=1, vad_filter=True)
            text = " ".join(s.text.strip() for s in segments).strip()
        print(json.dumps({"text": text}), flush=True)
    except Exception as e:
        print(json.dumps({"error": str(e)}), flush=True)
