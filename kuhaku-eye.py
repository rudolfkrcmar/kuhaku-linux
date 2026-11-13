#!/usr/bin/env python3
"""Kūhaku's eye: the phone's camera, for Claude.

A small page served on this computer, on 127.0.0.1 only. Tailscale makes it reachable over
HTTPS from the user's own devices and no one else's (`tailscale serve`), on any network.
Opened in the phone's browser with the camera allowed, the page shows what the camera sees
and waits. When Claude asks to look, the page takes one picture and sends it here. Nothing is
streamed or recorded; only the last picture is kept.

    kuhaku-eye.py serve     the page (kuhaku-session keeps it running)
    kuhaku-eye.py look      ask the phone for a picture; prints its path, or why there is none
    kuhaku-eye.py setup     share the page on Tailscale; prints the address to open on the phone
"""

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from kuhaku_log import report

PORT = 8770
HERE = Path(__file__).resolve().parent
PAGE = HERE / "eye" / "index.html"
STATE = Path.home() / ".local/state/kuhaku/eye"
PICTURE = STATE / "latest.jpg"
PASS = STATE / "ask.pass"  # lets only this computer's user ask for a picture
WAIT = 20  # seconds the phone's request is held open, waiting for a question
ANSWER = 10  # seconds Claude waits for the picture
PHONE_GONE = WAIT + 10  # no word from the phone for this long: the page is not open
MAX_PICTURE = 20 * 1024 * 1024


class Eye:
    """What the page and the askers share: how many pictures were asked for, and the last one sent."""

    def __init__(self) -> None:
        self.changed = threading.Condition()
        self.asked = 0
        self.answered = 0
        self.phone_seen = 0.0

    def phone_open(self) -> bool:
        return time.time() - self.phone_seen < PHONE_GONE


EYE = Eye()


class Handler(BaseHTTPRequestHandler):
    server_version = "kuhaku-eye"

    def log_message(self, format, *args):
        return

    def reply(self, code: int, body: bytes, kind: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, code: int, data: dict) -> None:
        self.reply(code, json.dumps(data).encode())

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self.reply(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/wait":
            self.wait()
        else:
            self.json(404, {"error": "not here"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/picture":
            self.picture()
        elif path == "/ask":
            self.ask()
        else:
            self.json(404, {"error": "not here"})

    def wait(self) -> None:
        """The phone, holding a request open until a picture is wanted."""
        have = int(self.path.partition("have=")[2] or 0)
        end = time.time() + WAIT
        with EYE.changed:
            EYE.phone_seen = time.time()
            while EYE.asked <= have and time.time() < end:
                EYE.changed.wait(end - time.time())
            EYE.phone_seen = time.time()
            asked = EYE.asked
        self.json(200, {"asked": asked})

    def picture(self) -> None:
        """The phone, sending the picture it was asked for."""
        size = int(self.headers.get("Content-Length") or 0)
        if not 0 < size <= MAX_PICTURE or self.headers.get("Content-Type") != "image/jpeg":
            self.json(400, {"error": "a JPEG picture, please"})
            return
        number = int(self.path.partition("for=")[2] or 0)
        data = self.rfile.read(size)
        part = PICTURE.with_suffix(".part")
        part.write_bytes(data)
        part.replace(PICTURE)
        with EYE.changed:
            EYE.phone_seen = time.time()
            EYE.answered = max(EYE.answered, number)
            EYE.changed.notify_all()
        self.json(200, {"ok": True})

    def ask(self) -> None:
        """Claude (through `look`), asking for a picture."""
        if not secrets.compare_digest(self.headers.get("X-Eye-Pass", ""), PASS.read_text().strip()):
            self.json(403, {"error": "not allowed"})
            return
        with EYE.changed:
            if not EYE.phone_open():
                self.json(200, {"error": "The camera page is not open on the phone."})
                return
            EYE.asked += 1
            number = EYE.asked
            EYE.changed.notify_all()
            end = time.time() + ANSWER
            while EYE.answered < number and time.time() < end:
                EYE.changed.wait(end - time.time())
            ok = EYE.answered >= number
        if ok:
            self.json(200, {"path": str(PICTURE)})
        else:
            self.json(200, {"error": "The phone did not send a picture (its screen may be off)."})


def serve() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)
    if not PASS.exists():
        PASS.write_text(secrets.token_urlsafe(24))
        os.chmod(PASS, 0o600)
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


def look() -> int:
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/ask", data=b"", method="POST", headers={"X-Eye-Pass": PASS.read_text().strip()}
        )
        with urllib.request.urlopen(request, timeout=ANSWER + 5) as answer:
            result = json.load(answer)
    except OSError:
        report()
        print("Kūhaku's eye is not running.")
        return 1
    if "path" in result:
        print(result["path"])
        return 0
    print(result.get("error", "No picture."), "Address:", address() or "(Tailscale is not set up)")
    return 1


def address() -> str:
    """The page's address on Tailscale, if it is shared there."""
    try:
        status = json.loads(subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True).stdout)
        name = (status.get("Self") or {}).get("DNSName", "").rstrip(".")
    except (OSError, ValueError):
        report()
        return ""
    return f"https://{name}/" if name else ""


def setup() -> int:
    done = subprocess.run(["tailscale", "serve", "--bg", "--https=443", f"http://127.0.0.1:{PORT}"])
    if done.returncode:
        return done.returncode
    print(address())
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if command == "serve":
        serve()
    elif command == "look":
        sys.exit(look())
    elif command == "setup":
        sys.exit(setup())
    else:
        print(__doc__)
        sys.exit(2)
