#!/usr/bin/env python3
"""Tegami without a window, for the Mac app (or any other front).

Reads one JSON message per line on stdin, the same messages tegami.html posts
({"type": "list", ...}), and writes one JSON line per answer on stdout:
{"fn": "listed", "data": {...}}, where fn is the page function Kūhaku's window
would call. {"fn": "_ready"} comes first; {"fn": "_minimize"} asks the front to
hide. Everything else the core prints goes to stderr.

    python3 tegami_engine.py
"""

import json
import os
import queue
import signal
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_out = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1, encoding="utf-8")
sys.stdout = sys.stderr  # stray prints must not break the line protocol

from tegami_core import Mail


class Engine:
    """The host: everything runs on one thread, like Kūhaku's GTK main loop."""

    def __init__(self):
        self.jobs = queue.Queue()
        self.lock = threading.Lock()
        self.mail = Mail(self)

    def js(self, fn, payload=None):
        line = json.dumps({"fn": fn, "data": payload}, ensure_ascii=False, default=str)
        with self.lock:
            _out.write(line + "\n")

    def later(self, f, *args):
        self.jobs.put((f, args))

    def minimize(self):
        self.js("_minimize")

    def quit(self):
        self.jobs.put(None)

    def read_stdin(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                print("not JSON:", line[:200], file=sys.stderr)
                continue
            self.later(self.mail.handle, msg)
        self.jobs.put(None)  # the front went away

    def run(self):
        threading.Thread(target=self.read_stdin, daemon=True).start()
        self.later(self.mail.ready)
        self.js("_ready")
        while True:
            job = self.jobs.get()
            if job is None:
                break
            f, args = job
            try:
                f(*args)
            except Exception as e:
                import traceback

                traceback.print_exc()
                self.js("toast", f"Something went wrong: {str(e)[:120]}")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    Engine().run()
