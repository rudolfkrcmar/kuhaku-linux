"""Talking with Kūhaku: offline dictation (Whisper), and spoken conversations that listen, ask Claude and answer aloud (Piper).

A part of the Kuhaku window class (kuhaku.py), kept here by subject."""

import json
import re
import signal
import subprocess
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib

from kuhaku_config import DICTATION_FILE, HERE, STATE_DIR, VOICE_IN, VOICE_MODEL, VOICE_OUT
from kuhaku_log import report


class Voice:
    # --- dictation: record with PipeWire, transcribe offline with Whisper --
    def dictate(self, on):
        if on and not self.recorder:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                self.recorder = subprocess.Popen(
                    [
                        "parec",
                        "--rate=16000",
                        "--channels=1",
                        "--format=s16le",
                        "--file-format=wav",
                        str(DICTATION_FILE),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                # Load Whisper while the user is still speaking.
                threading.Thread(target=self.start_whisper, daemon=True).start()
            except FileNotFoundError:
                self.send("dictationDone", {"error": "No microphone recorder found."})
        elif not on and self.recorder:
            rec, self.recorder = self.recorder, None
            rec.send_signal(signal.SIGINT)  # lets parec finish the WAV header
            rec.wait()
            threading.Thread(target=self.transcribe, daemon=True).start()

    def start_whisper(self):
        """Keep one Whisper worker loaded so dictation answers in seconds."""
        with self.whisper_lock:
            if self.whisper and self.whisper.poll() is None:
                return self.whisper
            python = HERE / "venv" / "bin" / "python"
            if not python.exists():
                return None
            self.whisper = subprocess.Popen(
                [str(python), str(HERE / "transcribe.py"), self.config.get("dictation_model") or "auto"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self.whisper.stdout.readline()  # {"ready": true}
            return self.whisper

    def transcribe(self):
        text, error = "", None
        try:
            worker = self.start_whisper()
            if not worker:
                raise FileNotFoundError
            worker.stdin.write(f"{DICTATION_FILE}\n")
            worker.stdin.flush()
            reply = json.loads(worker.stdout.readline() or "{}")
            text = reply.get("text", "")
            if "error" in reply or not reply:
                error = "Dictation failed."
        except FileNotFoundError:
            error = "Dictation is not set up yet."
        except Exception:
            error = "Dictation failed."
        GLib.idle_add(self.send, "dictationDone", {"text": text, "error": error})

    # --- conversation: listen until a pause, transcribe, ask, read the answer aloud, listen again --
    def voice(self, on):
        if on and not self.convo:
            self.convo = time.time()
            threading.Thread(target=self.start_whisper, daemon=True).start()
            threading.Thread(target=self.start_speaker, daemon=True).start()
            threading.Thread(target=self.listen, args=(self.convo,), daemon=True).start()
        elif not on and self.convo:
            self.convo = 0
            p = self.voice_proc
            if p and p.poll() is None:
                p.terminate()
            self.send("voiceState", "off")

    def voice_state(self, conv, state, **extra):
        if self.convo == conv:
            GLib.idle_add(self.send, "voiceState", state if not extra else dict(extra, state=state))

    def listen(self, conv):
        """Records until the user has spoken and then paused for a moment (or stays quiet too long)."""
        import array
        import math
        import wave

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.voice_state(conv, "listening")
        try:
            rec = subprocess.Popen(
                ["parec", "--rate=16000", "--channels=1", "--format=s16le", "--raw"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self.voice_state(conv, "error", error="No microphone recorder found.")
            self.convo = 0
            return
        self.voice_proc = rec
        chunk, frames, floor, spoke, quiet, waited = 3200, [], [], False, 0, 0  # 100 ms chunks
        while self.convo == conv:
            data = rec.stdout.read(chunk)
            if not data:
                break
            a = array.array("h", data)
            level = math.sqrt(sum(x * x for x in a) / max(1, len(a)))
            if len(floor) < 4:  # the room's own sound, first 0.4 s
                floor.append(level)
                continue
            threshold = max(450, 3 * (sum(floor) / len(floor)))
            if level > threshold:
                if not spoke:
                    self.voice_state(conv, "hearing")
                spoke, quiet = True, 0
            elif spoke:
                quiet += 1
            else:
                waited += 1
                frames = frames[-5:]  # keep a little lead-in before the first word
            frames.append(data)
            if spoke and (quiet >= 12 or len(frames) > 600):  # a 1.2 s pause, or a minute at most
                break
            if not spoke and waited > 200:  # 20 s of quiet: the conversation ends by itself
                break
        rec.terminate()
        rec.wait()
        if self.convo != conv:
            return
        if not spoke:
            self.convo = 0
            GLib.idle_add(self.send, "voiceState", "off")
            return
        with wave.open(str(VOICE_IN), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"".join(frames))
        self.voice_state(conv, "thinking")
        text = ""
        try:
            worker = self.start_whisper()
            with self.whisper_lock:
                worker.stdin.write(f"{VOICE_IN}\n")
                worker.stdin.flush()
                text = json.loads(worker.stdout.readline() or "{}").get("text", "")
        except Exception:
            self.voice_state(conv, "error", error="Could not understand that.")
            self.convo = 0
            return
        if self.convo != conv:
            return
        if not text.strip() or re.fullmatch(r"\W*(thank you|thanks for watching|you)\W*", text.strip(), re.I):
            return self.listen(conv)  # only noise: keep listening
        GLib.idle_add(self.send, "voiceHeard", {"text": text.strip()})

    def start_speaker(self):
        with self.speaker_lock:
            if self.speaker and self.speaker.poll() is None:
                return self.speaker
            python = HERE / "venv" / "bin" / "python"
            if not python.exists() or not VOICE_MODEL.exists():
                return None
            self.speaker = subprocess.Popen(
                [str(python), str(HERE / "speak.py"), str(VOICE_MODEL)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self.speaker.stdout.readline()  # {"ready": true}
            return self.speaker

    def speak_then_listen(self, text, conv):
        text = re.sub(r"```.*?```", " ", text, flags=re.S)
        text = re.sub(r"https?://\S+", "a link", text)
        text = re.sub(r"[*_#`>|]+", "", text)
        text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
        text = re.sub(r"\s+", " ", text).strip()[:1500]
        if text and self.convo == conv:
            self.voice_state(conv, "speaking")
            try:
                worker = self.start_speaker()
                with self.speaker_lock:
                    worker.stdin.write(json.dumps({"text": text, "path": str(VOICE_OUT)}) + "\n")
                    worker.stdin.flush()
                    ok = json.loads(worker.stdout.readline() or "{}").get("ok")
                if ok and self.convo == conv:
                    self.voice_proc = subprocess.Popen(
                        ["paplay", str(VOICE_OUT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    self.voice_proc.wait()
            except Exception:
                report()
        if self.convo == conv:
            self.listen(conv)
