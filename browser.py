#!/usr/bin/env python3
"""Mado (窓, "window"): the web in Kūhaku's quiet design.

One thin line on top (the address, the tabs as dots), the page below. Built on
Chromium (Qt WebEngine), so big sites, sign-ins and Cloudflare's human check
simply work. Running it again opens the URL as a tab in the window that is
already there. The WebKit Mado lives on as browser_webkit.py (the fallback).

Keys: Ctrl+L address, Ctrl+T new tab, Ctrl+W close tab, Ctrl+Tab next tab,
Ctrl+F find, Ctrl+R reload, Alt+Left/Right back/forward, Ctrl+plus/minus zoom,
Ctrl+U the page's source.

Three modes in the one window, switched on the bar:
  Normal     the everyday tabs, kept between sessions.
  Private    (Ctrl+Shift+N, `mado --private`) its own tabs on an off-the-record profile:
             nothing is kept, not cookies, history, tabs or site permissions; closing the
             last private tab forgets all of it. The bar lists the trackers it stopped.
  Developer  (F12, Ctrl+Shift+I) the everyday tabs with developer tools under the page
             and phones and tablets to simulate.
"""

import base64
import json
import os
import re
import signal
import socket
import struct
import threading
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
DATA = HOME / ".local" / "share" / "kuhaku" / "mado-chromium"
CACHE = HOME / ".cache" / "mado-chromium"
STATE = HOME / ".local" / "state" / "kuhaku" / "browser.json"
TRUSTED = HOME / ".config" / "kuhaku" / "mado-certificates.json"  # host -> PEM the user chose to trust
PERMS = HOME / ".config" / "kuhaku" / "mado-permissions.json"  # host -> "media"/"clipboard" -> true/false
SEARCH = "https://www.google.com/search?q="
NEWTAB = (HERE / "newtab.html").as_uri()
CHROME_HEIGHT = 52
WEB_SCHEMES = ("http", "https", "file", "about", "data", "blob", "javascript", "chrome", "qrc", "view-source")

# The Core M-5Y31 decodes H.264 in hardware (VA-API) but not VP9 or AV1. Chromium is told
# to use the video chip, and YouTube is asked for H.264 so playback stays cool and smooth.
# One renderer per site keeps memory down on this 8 GB machine.
# Claude can drive Mado (read pages, click, type) through Chromium's DevTools port, open only
# to this computer. KUHAKU_MADO_DEBUG=0 turns it off.
if os.environ.get("KUHAKU_MADO_DEBUG", "1") != "0":
    os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "127.0.0.1:9222")
DEBUG_PORT = os.environ.get("QTWEBENGINE_REMOTE_DEBUGGING", "")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    " ".join(
        [
            "--enable-features=VaapiVideoDecoder,VaapiVideoDecodeLinuxGL,VaapiIgnoreDriverChecks",
            "--ignore-gpu-blocklist",
            "--enable-gpu-rasterization",
            "--process-per-site",
        ]
    ),
)

from PyQt6.QtCore import QEvent, QFile, QIODevice, QObject, Qt, QTimer, QUrl, pyqtSlot
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QShortcut
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEnginePermission,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QSplitter, QStackedWidget, QVBoxLayout, QWidget
from kuhaku_log import report  # faults go to the log instead of vanishing

SOCKET = f"kuhaku-mado-{os.getuid()}"

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


class Browser:
    def __init__(self, app):
        self.app = app
        self.mode = "normal"  # normal, private or developer
        self.sets = {"normal": [], "private": []}  # developer mode shows the normal tabs
        self.tabs = self.sets["normal"]
        self.front = {}
        self.profiles = {}
        self.stopped = {"normal": {}, "private": {}}  # site -> tracker host -> what was stopped
        self.stopped_seen = 0
        self.device = "Full window"
        self.session = None
        self.current = None
        self.download_note = None
        self.asking = None
        self.tls_pending = {}
        self.chrome_css = CHROME_HEIGHT
        for d in (DATA, CACHE, STATE.parent):
            d.mkdir(parents=True, exist_ok=True)
        self.night = theme() == "night"
        self.set_scheme()
        self.build_window()

    # --- engine -----------------------------------------------------------
    def key(self):
        return "private" if self.mode == "private" else "normal"

    def key_of_page(self, page):
        return "private" if self.profiles.get("private") is page.profile() else "normal"

    def profile(self, key):
        if key not in self.profiles:
            self.profiles[key] = self.build_profile(key == "private")
        return self.profiles[key]

    def build_profile(self, private):
        if private:
            # Off the record: cookies, cache and storage live in memory and end with the private tabs.
            p = QWebEngineProfile(self.app)
            p.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
            # Cookies from other sites than the one in the address line are refused, and noted.
            seen = self.stopped["private"]

            def cookie(req):
                if not req.thirdParty:
                    return True
                first = req.firstPartyUrl.host().lower().removeprefix("www.")
                seen.setdefault(first, {}).setdefault(site(req.origin.host()), "cookies refused")
                return False

            p.cookieStore().setCookieFilter(cookie)
        else:
            p = QWebEngineProfile("mado", self.app)
            p.setPersistentStoragePath(str(DATA))
            p.setCachePath(str(CACHE))
            p.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
            p.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        # An ordinary Chrome on Linux, without the "QtWebEngine" word some sites frown at.
        ua = re.sub(r"QtWebEngine/\S+ ", "", p.httpUserAgent())
        p.setHttpUserAgent(ua)
        try:
            chrome = re.search(r"Chrome/(\S+)", ua).group(1)
            p.clientHints().setFullVersionList({"Chromium": chrome, "Not(A:Brand": "24.0.0.0"})
        except Exception:
            report()
        p.quiet = Quiet(self.stopped["private" if private else "normal"])
        p.setUrlRequestInterceptor(p.quiet)
        p.downloadRequested.connect(self.on_download)
        s = p.settings()
        A = QWebEngineSettings.WebAttribute
        for attr in (
            A.FullScreenSupportEnabled,
            A.ScrollAnimatorEnabled,
            A.ScreenCaptureEnabled,
            A.JavascriptCanAccessClipboard,
            A.PdfViewerEnabled,
            A.PluginsEnabled,
            A.DnsPrefetchEnabled,
            A.WebGLEnabled,
            A.Accelerated2dCanvasEnabled,
        ):
            s.setAttribute(attr, True)
        s.setAttribute(A.PlaybackRequiresUserGesture, False)
        F = QWebEngineSettings.FontFamily
        s.setFontFamily(F.StandardFont, "Inter")
        s.setFontFamily(F.SansSerifFont, "Inter")
        script = QWebEngineScript()
        script.setName("kuhaku-h264")
        script.setSourceCode(H264_SCRIPT)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        p.scripts().insert(script)
        return p

    def set_scheme(self):
        # Pages see prefers-color-scheme from Kūhaku's day or night, not the system theme.
        try:
            self.app.styleHints().setColorScheme(Qt.ColorScheme.Dark if self.night else Qt.ColorScheme.Light)
        except Exception:
            report()

    def bg(self):
        return QColor("#121212") if self.night else QColor("#f7f4f1")

    # --- window -----------------------------------------------------------
    def build_window(self):
        w = self.window = QWidget()
        w.setWindowTitle("Mado")
        w.setWindowIcon(QIcon.fromTheme("mado"))
        w.setWindowFlag(Qt.WindowType.FramelessWindowHint)

        def closing(ev):
            self.save()
            ev.accept()

        w.closeEvent = closing
        w.setStyleSheet(f"background:{self.bg().name()}")
        box = QVBoxLayout(w)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        self.chrome = QWebEngineView()
        cpage = QWebEnginePage(QWebEngineProfile(self.app), self.chrome)  # the toolbar keeps nothing
        cpage.setBackgroundColor(self.bg())
        self.chrome.setPage(cpage)
        self.chrome.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.bridge = Bridge(self)
        self.channel = QWebChannel(cpage)
        self.channel.registerObject("chrome", self.bridge)
        cpage.setWebChannel(self.channel)
        f = QFile(":/qtwebchannel/qwebchannel.js")
        f.open(QIODevice.OpenModeFlag.ReadOnly)
        bridge = QWebEngineScript()
        bridge.setSourceCode(bytes(f.readAll()).decode() + BRIDGE_SCRIPT)
        bridge.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        bridge.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        cpage.scripts().insert(bridge)
        cpage.loadFinished.connect(lambda ok: self.push())
        self.load_chrome()
        self.chrome.setFixedHeight(self.chrome_css)

        self.stack = QStackedWidget()
        # A simulated device: the page keeps its size, centred, and the rest stays calm.
        self.watch = Watch(self.fit_device)
        self.stack.installEventFilter(self.watch)
        # Developer tools sit under the page in developer mode, and follow the tab in front.
        self.devtools = None
        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.setHandleWidth(1)
        self.split.addWidget(self.stack)
        box.addWidget(self.chrome)
        box.addWidget(self.split, 1)

        keys = {
            "Ctrl+L": lambda: (self.chrome.setFocus(), self.js("editAddress()")),
            "Ctrl+T": self.new_tab,
            "Ctrl+W": self.close_tab,
            "Ctrl+Q": self.quit,
            "Ctrl+Tab": lambda: self.step(1),
            "Ctrl+PgDown": lambda: self.step(1),
            "Ctrl+Shift+Tab": lambda: self.step(-1),
            "Ctrl+PgUp": lambda: self.step(-1),
            "Ctrl+F": lambda: (self.chrome.setFocus(), self.js("editFind()")),
            "Ctrl+R": lambda: self.current and self.current.reload(),
            "F5": lambda: self.current and self.current.reload(),
            "Alt+Left": lambda: self.current and self.current.back(),
            "Alt+Right": lambda: self.current and self.current.forward(),
            "Ctrl+=": lambda: self.zoom(0.1),
            "Ctrl++": lambda: self.zoom(0.1),
            "Ctrl+-": lambda: self.zoom(-0.1),
            "Ctrl+0": lambda: self.zoom(0),
            "Ctrl+Shift+N": lambda: self.set_mode("normal" if self.mode == "private" else "private"),
            "F12": self.toggle_developer,
            "Ctrl+Shift+I": self.toggle_developer,
            "Ctrl+Alt+I": self.toggle_developer,
            "Ctrl+U": lambda: self.current and self.new_tab("view-source:" + self.current.url().toString()),
        }
        for i in range(1, 10):
            keys[f"Ctrl+{i}"] = lambda i=i: (
                self.tabs and self.switch(self.tabs[-1 if i == 9 else min(i, len(self.tabs)) - 1])
            )
        self.shortcuts = []
        for k, fn in keys.items():
            sc = QShortcut(QKeySequence(k), w)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(fn)
            self.shortcuts.append(sc)
        self.theme_timer = QTimer()
        self.theme_timer.timeout.connect(self.follow_theme)
        self.theme_timer.start(30_000)
        # Trackers stopped in private mode show up on the bar as they happen.
        self.stopped_timer = QTimer()
        self.stopped_timer.timeout.connect(self.watch_stopped)
        self.stopped_timer.start(1000)

    # --- modes ------------------------------------------------------------
    def set_mode(self, mode):
        if mode not in ("normal", "private", "developer") or mode == self.mode:
            return
        old = self.key()
        if self.mode == "developer":
            self.developer(False)
        self.mode = mode
        new = self.key()
        if new != old:
            if self.current in self.tabs:
                self.front[old] = self.current
            self.tabs = self.sets[new]
            if self.tabs:
                f = self.front.get(new)
                self.switch(f if f in self.tabs else self.tabs[-1])
            else:
                self.new_tab()
        self.window.setWindowTitle(
            {"normal": "Mado", "private": "Mado · Private", "developer": "Mado · Developer"}[mode]
        )
        if mode == "developer":
            self.developer(True)
        self.push()

    def toggle_developer(self):
        self.set_mode("normal" if self.mode == "developer" else "developer")

    def developer(self, on):
        if on:
            if not self.devtools:
                self.devtools = QWebEngineView()
                self.devtools.setPage(QWebEnginePage(self.profile("normal"), self.devtools))
                self.split.addWidget(self.devtools)
            self.devtools.show()
            h = self.split.height()
            self.split.setSizes([h * 3 // 5, h - h * 3 // 5])
            if self.current:
                self.current.page().setDevToolsPage(self.devtools.page())
            self.emulate()
        else:
            if self.devtools:
                self.devtools.hide()
            if self.current:
                self.current.page().setDevToolsPage(None)
            self.end_emulation()
            self.stack.setContentsMargins(0, 0, 0, 0)
            if self.current:
                self.current.setFocus()

    def set_device(self, name):
        if name in DEVICES:
            self.device = name
            if self.mode == "developer":
                self.emulate(reload=True)
            self.push()

    def device_size(self):
        """The simulated screen as it fits under the bar: never wider or taller than there is room for."""
        d = DEVICES.get(self.device)
        if not d or self.mode != "developer":
            return None
        pad = 20
        return min(d[0], self.stack.width()), min(d[1], max(200, self.stack.height() - 2 * pad)), pad

    def fit_device(self):
        size = self.device_size()
        if not size:
            if self.stack.contentsMargins().left() or self.stack.contentsMargins().top():
                self.stack.setContentsMargins(0, 0, 0, 0)
            return
        w, h, pad = size
        W, H = self.stack.width(), self.stack.height()
        left = (W - w) // 2
        self.stack.setContentsMargins(left, pad, W - w - left, max(0, H - h - pad))
        if self.session:
            d = DEVICES[self.device]
            self.session.send(
                "Emulation.setDeviceMetricsOverride",
                width=w,
                height=h,
                deviceScaleFactor=d[2],
                mobile=d[3],
                screenWidth=d[0],
                screenHeight=d[1],
            )

    def emulate(self, reload=False):
        """Render the tab in front as the chosen device: its size, pixel ratio, touch and user agent."""
        self.end_emulation()
        d = DEVICES.get(self.device)
        v = self.current
        self.fit_device()
        if not d or not v or self.mode != "developer":
            return
        if DEBUG_PORT:
            try:
                self.session = DevSession(v.page().devToolsId())
                s = self.session
                if d[4]:
                    s.send(
                        "Emulation.setUserAgentOverride",
                        userAgent=d[4],
                        platform="iPhone" if "iPhone" in d[4] else "iPad" if "iPad" in d[4] else "Linux armv8l",
                    )
                self.session_view = v
                self.touch()
                self.fit_device()
            except Exception:
                self.session = None
        if reload:
            QTimer.singleShot(500, v.reload)  # once the device's user agent is in place

    def touch(self):
        # Touch is set again for every page the tab loads: Chromium forgets it on navigation.
        d = DEVICES.get(self.device)
        if self.session and d:
            self.session.send("Emulation.setTouchEmulationEnabled", enabled=d[3], maxTouchPoints=5)
            self.session.send("Emulation.setEmitTouchEventsForMouse", enabled=d[3], configuration="mobile")

    def end_emulation(self):
        # Chromium keeps a device's size, user agent and touch after the session ends,
        # so they are undone first, then the session closes.
        s, self.session = self.session, None
        if not s:
            return
        view = getattr(self, "session_view", None)
        s.send("Emulation.clearDeviceMetricsOverride")
        s.send("Emulation.setUserAgentOverride", userAgent=view.page().profile().httpUserAgent() if view else "")
        s.send("Emulation.setTouchEmulationEnabled", enabled=False)
        s.send("Emulation.setEmitTouchEventsForMouse", enabled=False)
        self.session_view = None
        QTimer.singleShot(1500, s.close)

    def watch_stopped(self):
        if self.mode != "private" or not self.current:
            return
        n = len(self.stopped["private"].get(host(self.current.url().toString()), {}))
        if n != self.stopped_seen:
            self.push()

    def load_chrome(self):
        self.chrome.load(QUrl((HERE / "browser.html").as_uri() + f"?theme={'night' if self.night else 'day'}"))

    def show(self):
        self.window.showMaximized()
        self.window.activateWindow()
        self.window.raise_()

    def restore(self):
        try:
            return [u for u in json.loads(STATE.read_text()).get("tabs", []) if u][:20]
        except Exception:
            report()
            return []

    def save(self):
        urls = [t.url().toString() for t in self.sets["normal"] if t.url().toString() not in ("", NEWTAB)]
        STATE.write_text(json.dumps({"tabs": urls}))

    def quit(self):
        self.save()
        self.app.quit()

    # --- tabs -------------------------------------------------------------
    def make_view(self, key):
        view = QWebEngineView()
        page = Page(self, self.profile(key))
        page.setBackgroundColor(self.bg())
        view.setPage(page)
        view.loading = False
        view.progress = 0.0

        def started():
            if view is getattr(self, "session_view", None):
                self.touch()
            view.loading, view.progress = True, 0.1
            self.changed(view)

        def progress(n):
            view.progress = n / 100
            self.changed(view)

        def finished(_ok):
            view.loading, view.progress = False, 1.0
            self.changed(view)

        view.loadStarted.connect(started)
        view.loadProgress.connect(progress)
        view.loadFinished.connect(finished)
        view.titleChanged.connect(lambda _t: self.push())
        view.urlChanged.connect(lambda _u: self.changed(view))
        page.linkHovered.connect(lambda link: self.js(f"window.hover && hover({json.dumps(link or '')})"))
        page.windowCloseRequested.connect(lambda: self.close_tab(view))
        page.fullScreenRequested.connect(self.on_fullscreen)
        page.permissionRequested.connect(lambda perm: self.on_permission(view, perm))
        page.certificateError.connect(lambda err: self.on_tls_error(view, err))
        page.findTextFinished.connect(lambda r: self.js(f"window.found && found({r.numberOfMatches()})"))
        return view

    def changed(self, view):
        if view is self.current:
            self.push()

    def add_tab(self, view, focus=True, key=None):
        tabs = self.sets[key or self.key()]
        at = tabs.index(self.current) + 1 if self.current in tabs else len(tabs)
        tabs.insert(at, view)
        self.stack.addWidget(view)
        if focus and tabs is self.tabs:
            self.switch(view)
        self.push()

    def new_tab(self, url=NEWTAB, focus=True, key=None):
        key = key or self.key()
        view = self.make_view(key)
        if url:
            view.load(QUrl(url))
        self.add_tab(view, focus, key)
        if focus and url == NEWTAB:
            view.setFocus()
        return view

    def open(self, urls, private=False):
        # Links from other apps open with the everyday tabs, never in private ones.
        key = "private" if private else "normal"
        views = [self.new_tab(u, focus=False, key=key) for u in urls]
        self.set_mode(key if private or self.mode == "private" else self.mode)
        if views:
            self.switch(views[-1])
        self.show()

    def switch(self, view):
        if self.current is not view:
            if self.mode == "developer" and self.devtools:
                if self.current is not None:
                    try:
                        self.current.page().setDevToolsPage(None)
                    except RuntimeError:
                        pass
                view.page().setDevToolsPage(self.devtools.page())
        self.current = view
        self.stack.setCurrentWidget(view)
        view.setFocus()
        if self.mode == "developer" and (not self.session or getattr(self, "session_view", None) is not view):
            self.emulate()
        self.push()

    def step(self, n):
        if len(self.tabs) > 1 and self.current in self.tabs:
            self.switch(self.tabs[(self.tabs.index(self.current) + n) % len(self.tabs)])

    def close_tab(self, view=None):
        view = view or self.current
        if view not in self.tabs:
            return
        i = self.tabs.index(view)
        self.tabs.remove(view)
        self.stack.removeWidget(view)
        if view is getattr(self, "session_view", None):
            self.end_emulation()
        view.page().deleteLater()
        view.deleteLater()
        if not self.tabs and self.key() == "private":
            # The last private tab closed: its cookies, cache and trackers are forgotten.
            p = self.profiles.pop("private", None)
            self.stopped["private"].clear()
            self.front.pop("private", None)
            if p:
                QTimer.singleShot(2000, p.deleteLater)
            self.current = None
            self.set_mode("normal")
            return
        if not self.tabs:
            self.quit()
            return
        if view is self.current:
            self.switch(self.tabs[min(i, len(self.tabs) - 1)])
        self.push()

    def zoom(self, d):
        v = self.current
        if v:
            v.setZoomFactor(1.0 if d == 0 else min(3.0, max(0.3, v.zoomFactor() + d)))

    # --- toolbar ----------------------------------------------------------
    def js(self, code):
        self.chrome.page().runJavaScript(code)

    def push(self):
        v = self.current
        if not v:
            return
        uri = v.url().toString()

        def title(t):
            u = t.url().toString()
            return (
                "New tab"
                if u in ("", NEWTAB)
                else (t.title() if t.title() and t.title() != u else host(u) or "New tab")
            )

        state = {
            "url": "" if uri == NEWTAB else uri,
            "title": v.title() or "",
            "loading": v.loading,
            "progress": v.progress,
            "back": v.history().canGoBack(),
            "forward": v.history().canGoForward(),
            "tabs": [{"title": title(t), "active": t is v, "loading": t.loading} for t in self.tabs],
            "download": self.download_note,
            "ask": {"host": self.asking["host"], "what": self.asking["what"]}
            if self.asking and self.asking["view"] is v
            else None,
            "mode": self.mode,
            "privateTabs": len(self.sets["private"]),
        }
        if self.mode == "private":
            seen = self.stopped["private"].get(host(uri), {})
            self.stopped_seen = len(seen)
            state["stopped"] = [{"host": h, "how": how} for h, how in sorted(seen.items())]
        if self.mode == "developer":
            size = self.device_size()
            state["devices"] = [
                {
                    "name": n,
                    "kind": "full" if not d else "laptop" if not d[3] else "tablet" if "iPad" in n else "phone",
                    "size": f"{d[0]} × {d[1]}" if d else "",
                }
                for n, d in DEVICES.items()
            ]
            state["device"] = self.device
            state["size"] = f"{size[0]} × {size[1]}" if size else ""
        self.js(f"window.update && update({json.dumps(state)})")

    def on_chrome(self, text):
        try:
            msg = json.loads(text)
        except Exception:
            report()
            return
        kind, v = msg.get("type"), self.current
        if kind == "perm":
            return self.answer_permission(bool(msg.get("allow")))
        if kind == "go":
            url = to_url(msg.get("text", ""))
            if url and v:
                v.load(QUrl(url))
                v.setFocus()
        elif kind == "back" and v:
            v.back()
        elif kind == "forward" and v:
            v.forward()
        elif kind == "reload" and v:
            v.stop() if v.loading else v.reload()
        elif kind == "tab":
            i = msg.get("index", 0)
            if 0 <= i < len(self.tabs):
                self.switch(self.tabs[i])
        elif kind == "closeTab":
            i = msg.get("index")
            self.close_tab(self.tabs[i] if i is not None and 0 <= i < len(self.tabs) else None)
        elif kind == "newTab":
            self.new_tab()
        elif kind == "mode":
            self.set_mode(msg.get("mode", "normal"))
        elif kind == "device":
            self.set_device(msg.get("name", ""))
        elif kind == "find" and v:
            text = msg.get("text", "")
            F = QWebEnginePage.FindFlag
            flags = F.FindBackward if msg.get("backwards") else F(0)
            v.page().findText(text, flags)
        elif kind == "doneEditing" and v:
            v.page().findText("")
            v.setFocus()
        elif kind == "height":
            self.chrome_css = int(msg.get("value", CHROME_HEIGHT))
            self.chrome.setFixedHeight(self.chrome_css)
        elif kind == "minimize":
            self.window.showMinimized()
        elif kind == "quit":
            self.quit()
        elif kind == "openDownloads":
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(HOME / "Downloads")))

    def follow_theme(self):
        """Day and night follow Kūhaku while Mado is open, not only when it was opened."""
        night = theme() == "night"
        if night == self.night:
            return
        self.night = night
        self.set_scheme()
        self.window.setStyleSheet(f"background:{self.bg().name()}")
        self.chrome.page().setBackgroundColor(self.bg())
        for t in self.sets["normal"] + self.sets["private"]:
            t.page().setBackgroundColor(self.bg())
        self.load_chrome()

    def on_fullscreen(self, request):
        request.accept()
        if request.toggleOn():
            self.chrome.hide()
            self.window.showFullScreen()
        else:
            self.chrome.show()
            self.window.showMaximized()

    # --- permissions ------------------------------------------------------
    def on_permission(self, view, perm):
        # Notifications: yes. Location: no. The microphone, the camera, sharing the screen and
        # reading the clipboard: each site asks once, the answer is remembered.
        T = QWebEnginePermission.PermissionType
        t = perm.permissionType()
        if t == T.Notifications:
            perm.grant()
            return
        what = {
            T.MediaAudioCapture: "the microphone",
            T.MediaVideoCapture: "the camera",
            T.MediaAudioVideoCapture: "the microphone and camera",
            T.DesktopVideoCapture: "to share the screen",
            T.DesktopAudioVideoCapture: "to share the screen",
            T.ClipboardReadWrite: "to read the clipboard",
        }.get(t)
        if not what:
            perm.deny()
            return
        kind = "clipboard" if t == T.ClipboardReadWrite else "screen" if "share" in what else "media"
        h = host(perm.origin().toString()) or "This page"
        private = view in self.sets["private"]
        said = None if private else load_json(PERMS).get(h, {}).get(kind)
        if said is not None:
            perm.grant() if said else perm.deny()
            return
        if self.asking:
            self.asking["perm"].deny()
        self.asking = {"perm": perm, "host": h, "kind": kind, "what": what, "view": view, "private": private}
        self.push()

    def answer_permission(self, allow):
        a, self.asking = self.asking, None
        if not a:
            return
        a["perm"].grant() if allow else a["perm"].deny()
        if a["kind"] != "screen" and not a["private"]:  # sharing the screen is asked every time
            p = load_json(PERMS)
            p.setdefault(a["host"], {})[a["kind"]] = bool(allow)
            save_json(PERMS, p)
        self.push()

    # --- certificates of home devices ------------------------------------
    def on_tls_error(self, view, err):
        h = err.url().host()
        chain = err.certificateChain()
        pem = bytes(chain[0].toPem()).decode() if chain else ""
        if pem and load_json(TRUSTED).get(h) == pem:
            err.acceptCertificate()
            return
        err.rejectCertificate()
        token = base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip("=")
        self.tls_pending[token] = (h, pem, err.url().toString(), view)
        esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
        page = TLS_PAGE.replace("{HOST}", esc(h)).replace("{TOKEN}", token)
        page = page.replace("{BG}", "#121212" if self.night else "#f7f4f1").replace(
            "{FG}", "#ececec" if self.night else "#111111"
        )
        page = page.replace("{ACCENT}", "#f0705a" if self.night else "#e0301e")
        QTimer.singleShot(0, lambda: view.setHtml(page, QUrl("about:blank")))

    def trust(self, token):
        if token not in self.tls_pending:
            return
        h, pem, uri, view = self.tls_pending.pop(token)
        if pem:
            t = load_json(TRUSTED)
            t[h] = pem
            save_json(TRUSTED, t)
        QTimer.singleShot(0, lambda: view.load(QUrl(uri)))

    # --- downloads --------------------------------------------------------
    def on_download(self, d):
        folder = HOME / "Downloads"
        folder.mkdir(exist_ok=True)
        target = folder / Path(d.downloadFileName() or "download").name
        stem, n = target.stem, 1
        while target.exists():
            target = folder / f"{stem} ({n}){target.suffix}"
            n += 1
        d.setDownloadDirectory(str(folder))
        d.setDownloadFileName(target.name)

        def progress():
            total = d.totalBytes()
            self.download_note = {"name": target.name, "progress": d.receivedBytes() / total if total > 0 else 0}
            self.push()

        def finished():
            if not d.isFinished():
                return
            self.download_note = {"name": target.name, "done": True}
            self.push()
            QTimer.singleShot(6000, self.clear_download)

        d.receivedBytesChanged.connect(progress)
        d.isFinishedChanged.connect(finished)
        d.accept()

    def clear_download(self):
        self.download_note = None
        self.push()


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    # Accept plain words and bare domains on the command line, not just URIs.
    private = "--private" in sys.argv[1:]
    urls = [to_url(a) or a for a in sys.argv[1:] if not a.startswith("-")]
    urls = [Path(u).resolve().as_uri() if Path(u).exists() and "://" not in u else u for u in urls]
    # Mado already open: hand it the addresses and step back.
    name = SOCKET
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(300):
        sock.write(json.dumps({"urls": urls, "private": True} if private else urls).encode())
        sock.waitForBytesWritten(1000)
        sock.disconnectFromServer()
        return 0
    sys.argv[0] = "browser.py"  # window class stays "browser.py" for Kūhaku
    app = QApplication(sys.argv)
    app.setApplicationName("browser.py")
    app.setApplicationDisplayName("Mado")
    app.setDesktopFileName("mado")
    b = Browser(app)
    server = QLocalServer()
    QLocalServer.removeServer(name)
    server.listen(name)

    def incoming():
        c = server.nextPendingConnection()

        def read():
            try:
                got = json.loads(bytes(c.readAll()).decode() or "[]")
            except Exception:
                got = []
            if isinstance(got, dict):
                b.open(got.get("urls") or [], private=bool(got.get("private")))
            else:
                b.open(got) if got else b.show()

        c.readyRead.connect(read)
        c.disconnected.connect(c.deleteLater)

    server.newConnection.connect(incoming)
    for u in b.restore() or ([] if urls and not private else [NEWTAB]):
        b.new_tab(u, focus=False)
    if b.tabs:  # nothing to restore and a link was asked for: open() makes the tab
        b.switch(b.tabs[-1])
    b.open(urls, private) if urls or private else b.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
