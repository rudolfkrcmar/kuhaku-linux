"""The web side of Mado: the scripts it puts into pages, the trackers it stops, and the small
Qt pieces between the window and the pages (DevTools session, request filter, page bridge)."""

import base64
import json
import os
import re
import socket
import struct
import threading
import time
import urllib.parse
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, pyqtSlot
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineUrlRequestInterceptor,
)

from kuhaku_log import report  # faults go to the log instead of vanishing

HOME = Path.home()
SEARCH = "https://www.google.com/search?q="
WEB_SCHEMES = ("http", "https", "file", "about", "data", "blob", "javascript", "chrome", "qrc", "view-source")
# Set by browser.py before Qt starts (it is imported after that).
DEBUG_PORT = os.environ.get("QTWEBENGINE_REMOTE_DEBUGGING", "")

H264_SCRIPT = r"""
// ==UserScript==
// @match https://*.youtube.com/*
// @match https://*.youtube-nocookie.com/*
// @run-at document-start
// ==/UserScript==
(() => {
  const no = (t) => /vp0?9|av01|av1/i.test(t || '');
  const ms = window.MediaSource;
  if (ms && ms.isTypeSupported) { const f = ms.isTypeSupported.bind(ms); ms.isTypeSupported = (t) => !no(t) && f(t); }
  const cp = HTMLMediaElement.prototype.canPlayType;
  HTMLMediaElement.prototype.canPlayType = function (t) { return no(t) ? '' : cp.call(this, t); };
  const mc = navigator.mediaCapabilities;
  if (mc && mc.decodingInfo) {
    const f = mc.decodingInfo.bind(mc);
    mc.decodingInfo = (c) => no(c && c.video && c.video.contentType)
      ? Promise.resolve({ supported: false, smooth: false, powerEfficient: false }) : f(c);
  }
})();
"""

# The toolbar (browser.html) talks the way it did under WebKit; this bridges it to Qt.
BRIDGE_SCRIPT = r"""
(() => {
  const q = [];
  window.webkit = { messageHandlers: { chrome: { postMessage: (m) => q.push(m) } } };
  const start = () => new QWebChannel(qt.webChannelTransport, (ch) => {
    const o = ch.objects.chrome;
    window.webkit.messageHandlers.chrome.postMessage = (m) => o.post(m);
    q.splice(0).forEach((m) => o.post(m));
  });
  if (window.qt && qt.webChannelTransport) start();
  else document.addEventListener('DOMContentLoaded', start);
})();
"""

TLS_PAGE = (Path(__file__).resolve().parent / "browser/tls.html").read_text(encoding="utf-8")

# Quiet by default: the usual ad and tracking hosts never load.
BLOCKED = [
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "adservice.google.com",
    "google-analytics.com",
    "googletagmanager.com",
    "googletagservices.com",
    "adnxs.com",
    "taboola.com",
    "outbrain.com",
    "criteo.com",
    "criteo.net",
    "amazon-adsystem.com",
    "scorecardresearch.com",
    "quantserve.com",
    "moatads.com",
    "pubmatic.com",
    "rubiconproject.com",
    "openx.net",
    "casalemedia.com",
    "adform.net",
    "smartadserver.com",
    "teads.tv",
    "hotjar.com",
    "facebook.net",
    "connect.facebook.net",
    "ads-twitter.com",
    "static.ads-twitter.com",
    "bat.bing.com",
    "yieldmo.com",
    "sharethrough.com",
    "media.net",
    "zedo.com",
    "adsrvr.org",
    "3lift.com",
    "bidswitch.net",
    "mathtag.com",
    "serving-sys.com",
    "popads.net",
    "propellerads.com",
]


def theme():
    """Follow Kūhaku: its saved choice, else the clock."""

    def load(p):
        try:
            return json.loads(p.read_text())
        except Exception:
            report()
            return {}

    cfg = load(HOME / ".config/kuhaku/config.json")
    st = load(HOME / ".local/state/kuhaku/state.json")
    choice = st.get("theme") or cfg.get("theme", "auto")
    if choice in ("day", "night"):
        return choice
    h = time.localtime().tm_hour
    return "night" if (h >= cfg.get("night_from", 20) or h < cfg.get("night_until", 7)) else "day"


def host(uri):
    try:
        return (urllib.parse.urlparse(uri or "").hostname or "").removeprefix("www.")
    except Exception:
        report()
        return ""


def site(h):
    """The site a host belongs to: ads.example.co.uk -> example.co.uk."""
    parts = h.lower().split(".")
    n = (
        3
        if len(parts) > 2 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "ac", "gov", "edu")
        else 2
    )
    return ".".join(parts[-n:])


def to_url(text):
    text = text.strip()
    if not text:
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", text) and not re.match(r"^[^/]+:\d", text):
        return text
    if " " not in text and ("." in text or text.startswith("localhost")):
        return "https://" + text
    return SEARCH + urllib.parse.quote_plus(text)


def load_json(p):
    try:
        return json.loads(p.read_text())
    except Exception:
        report()
        return {}


def save_json(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1))


# Google's sign-in refuses browsers built on Qt WebEngine ("This browser or app may not be
# secure"). Shown a Firefox there, as qutebrowser does, it signs in normally.
GOOGLE_SIGNIN = ("accounts.google.com",)
FIREFOX_UA = b"Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0"


IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)
IPAD_UA = (
    "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)
ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 15; {MODEL}) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
)
# Developer mode's devices: width, height (CSS pixels), pixel ratio, mobile, user agent.
DEVICES = {
    "Full window": None,
    "iPhone 16": (393, 852, 3, True, IPHONE_UA),
    "iPhone SE": (375, 667, 2, True, IPHONE_UA),
    "Pixel 9": (412, 923, 2.625, True, ANDROID_UA.replace("{MODEL}", "Pixel 9")),
    "Galaxy S24": (360, 780, 3, True, ANDROID_UA.replace("{MODEL}", "SM-S921B")),
    "iPad mini": (744, 1133, 2, True, IPAD_UA),
    "iPad Pro": (1024, 1366, 2, True, IPAD_UA),
    "Laptop": (1280, 800, 1, False, None),
}


class DevSession:
    """One DevTools session on one page, held open so Chromium keeps rendering it as the
    chosen device. Closing it ends the simulation. Chromium answers the handshake on this
    (the UI) thread, so connecting happens in the background and commands wait until then."""

    def __init__(self, target):
        self.target = target
        self.sock = None
        self.queue = []
        self.closed = False
        self.lock = threading.Lock()
        self.n = 0
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        try:
            h, port = DEBUG_PORT.rsplit(":", 1)
            sock = socket.create_connection((h, int(port)), timeout=10)
            key = base64.b64encode(os.urandom(16)).decode()
            sock.sendall(
                (
                    f"GET /devtools/page/{self.target} HTTP/1.1\r\nHost: {h}:{port}\r\n"
                    f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                    "Sec-WebSocket-Version: 13\r\n\r\n"
                ).encode()
            )
            head = b""
            while b"\r\n\r\n" not in head:
                got = sock.recv(1)
                if not got:
                    return
                head += got
            if b" 101 " not in head.split(b"\r\n", 1)[0]:
                return
            sock.settimeout(None)
            with self.lock:
                if self.closed:
                    sock.close()
                    return
                self.sock = sock
                for frame in self.queue:
                    sock.sendall(frame)
                self.queue = []
            while sock.recv(65536):  # answers are not needed, only read so the pipe never fills
                pass
        except OSError:
            pass

    def send(self, method, **params):
        self.n += 1
        data = json.dumps({"id": self.n, "method": method, "params": params}).encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n))
        frame = head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self.lock:
            if self.sock is None:
                self.queue.append(frame)
                return
            try:
                self.sock.sendall(frame)
            except OSError:
                pass

    def close(self):
        with self.lock:
            self.closed = True
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass


class Quiet(QWebEngineUrlRequestInterceptor):
    """Third-party requests to ad and tracking hosts never leave the computer.
    What was stopped is noted per site, so the private mode can show it."""

    def __init__(self, stopped):
        super().__init__()
        self.stopped = stopped

    def interceptRequest(self, info):
        h = info.requestUrl().host().lower()
        if h in GOOGLE_SIGNIN:
            # Only the agent line changes. Emptying the Sec-CH-UA hints as well looks forged:
            # Google then answers "Couldn't sign you in" and its popup ends in a 400 (gis_transform).
            info.setHttpHeader(b"User-Agent", FIREFOX_UA)
            return
        if not any(h == d or h.endswith("." + d) for d in BLOCKED):
            return
        first = info.firstPartyUrl().host().lower()
        if not any(first == d or first.endswith("." + d) for d in BLOCKED):
            info.block(True)
            self.stopped.setdefault(first.removeprefix("www."), {})[site(h)] = "tracker blocked"


class Bridge(QObject):
    def __init__(self, browser):
        super().__init__()
        self.browser = browser

    @pyqtSlot(str)
    def post(self, text):
        self.browser.on_chrome(text)


class Page(QWebEnginePage):
    def __init__(self, browser, profile):
        super().__init__(profile, browser.window)
        self.browser = browser

    def acceptNavigationRequest(self, url, kind, main_frame):
        scheme = url.scheme().lower()
        if scheme == "mado-trust":
            self.browser.trust(url.path())
            return False
        # Links into apps (slack://, claude://, mailto:, zoommtg:...) go to those apps,
        # so sign-ins started from Slack or Claude return to them.
        if scheme and scheme not in WEB_SCHEMES:
            QDesktopServices.openUrl(url)
            return False
        return True

    def createWindow(self, _kind):
        # A link opened in a new tab stays in the mode its page lives in.
        return self.browser.new_tab(None, key=self.browser.key_of_page(self)).page()


class Watch(QObject):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Resize:
            self.fn()
        return False
