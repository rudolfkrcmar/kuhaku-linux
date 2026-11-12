"""Kūhaku's dictation worker: loads Whisper once, then turns each 16 kHz mono WAV
path read from stdin into one line of JSON text on stdout. Fully offline."""

import json
import os
import sys
import wave

import ctranslate2
import numpy as np
from faster_whisper import WhisperModel


def pick(choice):
    """The best Whisper for this computer: an NVIDIA graphics chip runs a large, accurate model
    in under a second; otherwise the processor runs a small one, one thread per core.
    (Intel and AMD graphics chips are not supported by Whisper's engine; the processor is faster there.)"""
    gpu = ctranslate2.get_cuda_device_count() > 0
    if choice in (None, "", "auto"):
        choice = "large-v3-turbo" if gpu else "base"
    if gpu:
        return WhisperModel(choice, device="cuda", compute_type="float16")
    cores = len(os.sched_getaffinity(0))
    try:  # physical cores: hyperthreads only slow Whisper down
        with open("/proc/cpuinfo") as f:
            ids = {
                (b.split("physical id")[1][:6] if "physical id" in b else "", line)
                for b in f.read().split("\n\n")
                for line in b.splitlines()
                if line.startswith("core id")
            }
        cores = max(1, len(ids)) or cores
    except OSError:
        pass
    return WhisperModel(choice, device="cpu", compute_type="int8", cpu_threads=min(cores, 8))


model = pick(sys.argv[1] if len(sys.argv) > 1 else "auto")
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
