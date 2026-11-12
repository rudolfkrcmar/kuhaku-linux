#!/usr/bin/env python3
"""Mado (窓, "window"): the web in Kūhaku's quiet design.

One thin line on top (the address, the tabs as dots), the page below. Built on
the same WebKit as Kūhaku. Running it again opens the URL as a tab in the window
that is already there.

Keys: Ctrl+L address, Ctrl+T new tab, Ctrl+W close tab, Ctrl+Tab next tab,
Ctrl+F find, Ctrl+R reload, Alt+Left/Right back/forward, Ctrl+plus/minus zoom.
"""
import base64
import ipaddress
import json
import os
import re
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("JavaScriptCore", "4.1")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, JavaScriptCore, WebKit2
from kuhaku_log import report  # faults go to the log instead of vanishing

HERE = Path(__file__).resolve().parent
HOME = Path.home()
DATA = HOME / ".local" / "share" / "kuhaku" / "browser-data"
CACHE = HOME / ".cache" / "mado"
STATE = HOME / ".local" / "state" / "kuhaku" / "browser.json"
TRUSTED = HOME / ".config" / "kuhaku" / "mado-certificates.json"
PERMS = HOME / ".config" / "kuhaku" / "mado-permissions.json"     # host -> "media"/"clipboard" -> true/false   # host -> PEM the user chose to trust
SEARCH = "https://www.google.com/search?q="
NEWTAB = (HERE / "newtab.html").as_uri()
CHROME_HEIGHT = 52
# Safari on a Mac: sites such as Slack refuse "Safari on Linux" as unsupported.
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"
# Cloudflare's human check sees a Linux WebKit claiming to be a Mac and asks again forever.
# Sites behind it get the honest identity; Mado learns new ones from the check's page title.
NATIVE_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15"
NATIVE_HOSTS = HOME / ".config" / "kuhaku" / "mado-honest-hosts.json"
NATIVE_SEED = ("claude.ai", "claude.com", "anthropic.com")
CHALLENGE_TITLES = ("Just a moment...", "Just a moment…", "Attention Required! | Cloudflare", "Un instant…")
WEB_SCHEMES = ("http", "https", "file", "about", "data", "blob", "javascript")

# WebKitGTK here has no AVIF decoder, yet sites serve AVIF to a modern Safari.
# Failed AVIF images are passed to Python, which fetches the file, decodes it
# with gdk-pixbuf and answers with a JPEG (or PNG) data: URL.
TLS_PAGE = """<!doctype html><meta charset="utf-8"><title>{HOST}</title>
<style>body{margin:0;height:100vh;display:grid;place-items:center;background:{BG};color:{FG};font:300 18px Inter,sans-serif}
div{max-width:560px;border-left:1.5px solid {ACCENT};padding-left:24px}h1{font-weight:200;font-size:30px;margin:0 0 14px}
p{opacity:.65;line-height:1.5;margin:0 0 26px}button{font:inherit;font-size:15px;border:0;background:none;color:{FG};padding:0;cursor:pointer;
border-bottom:1.5px solid {ACCENT}}</style><div><h1>{HOST} has its own certificate</h1>
<p>Mado can't confirm who this is. That's normal for devices at home, like a camera or a router. Continue only if you know this device.</p>
<button onclick='webkit.messageHandlers.kuhakuTls.postMessage({h: {HOSTJS}, t: {TOKENJS}})'>Trust {HOST} and continue</button></div>"""


def private_host(host):
    """True for this computer and the home network (loopback, LAN, link-local, .local names)."""
    host = (host or "").strip("[]").lower()
    if not host or host == "localhost" or host.endswith((".local", ".lan", ".home", ".internal")):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            return True
    return False

H264_SCRIPT = r"""
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

AVIF_SCRIPT = r"""
(() => {
  const H = window.webkit && webkit.messageHandlers && webkit.messageHandlers.kuhakuImage;
  if (!H) return;
  const fix = (img) => {
    const u = img.currentSrc || img.src;
    if (!u || u.startsWith('data:') || img.dataset.kuhakuAvif) return;
    const avif = /avif/i.test(u) || [...(img.parentElement && img.parentElement.tagName === 'PICTURE'
                  ? img.parentElement.querySelectorAll('source') : [])].some(s => /avif/i.test(s.type));
    if (!avif) return;
    img.dataset.kuhakuAvif = '1';
    if (img.parentElement && img.parentElement.tagName === 'PICTURE')
      img.parentElement.querySelectorAll('source').forEach(s => s.remove());
    img.removeAttribute('srcset'); img.removeAttribute('sizes');
    H.postMessage(JSON.stringify({u: new URL(u, location.href).href, r: location.href}))
      .then((d) => { if (d) img.src = d; }).catch(() => {});
  };
  const broken = (img) => img.complete && img.naturalWidth === 0;
  const check = (e) => { const t = e.target; if (t && t.tagName === 'IMG' && (e.type === 'error' || broken(t))) fix(t); };
  document.addEventListener('error', check, true);
  document.addEventListener('load', check, true);
  const scan = () => document.querySelectorAll('img').forEach((i) => { if (broken(i)) fix(i); });
  addEventListener('DOMContentLoaded', scan); addEventListener('load', scan);
  let n = 0; const t = setInterval(() => { scan(); if (++n > 15) clearInterval(t); }, 2000);
})();
"""

# Quiet by default: the usual ad and tracking hosts never load.
BLOCKED = [
    "doubleclick.net", "googlesyndication.com", "googleadservices.com", "adservice.google.com",
    "google-analytics.com", "googletagmanager.com", "googletagservices.com", "adnxs.com",
    "taboola.com", "outbrain.com", "criteo.com", "criteo.net", "amazon-adsystem.com",
    "scorecardresearch.com", "quantserve.com", "moatads.com", "pubmatic.com", "rubiconproject.com",
    "openx.net", "casalemedia.com", "adform.net", "smartadserver.com", "teads.tv", "hotjar.com",
    "facebook.net", "connect.facebook.net", "ads-twitter.com", "static.ads-twitter.com",
    "bat.bing.com", "yieldmo.com", "sharethrough.com", "media.net", "zedo.com", "adsrvr.org",
    "3lift.com", "bidswitch.net", "mathtag.com", "serving-sys.com", "popads.net", "propellerads.com",
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
        return urllib.parse.urlparse(uri or "").hostname.removeprefix("www.")
    except Exception:
        report()
        return ""


def to_url(text):
    text = text.strip()
    if not text:
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", text) and not re.match(r"^[^/]+:\d", text):
        return text
    if " " not in text and ("." in text or text.startswith("localhost")):
        return "https://" + text
    return SEARCH + urllib.parse.quote_plus(text)


class Browser(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="app.kuhaku.Mado",
                         flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.window = None
        self.tabs = []
        self.current = None
        self.download_note = None
        self.asking = None
        self.download_names = {}

    # --- lifecycle --------------------------------------------------------
    def do_activate(self):
        if not self.window:
            self.build()
            urls = self.restore() or [NEWTAB]
            for u in urls:
                self.new_tab(u, focus=False)
            self.switch(self.tabs[-1])
        self.window.present()

    def do_open(self, files, _n, _hint):
        if not self.window:
            self.build()
            for u in self.restore():
                self.new_tab(u, focus=False)
        for f in files:
            self.new_tab(f.get_uri())
        self.window.present()

    def build(self):
        DATA.mkdir(parents=True, exist_ok=True)
        CACHE.mkdir(parents=True, exist_ok=True)
        STATE.parent.mkdir(parents=True, exist_ok=True)
        self.night = theme() == "night"
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", self.night)
        # Pages and the new tab follow Kūhaku's day or night, not the system theme.
        base = "Mint-Y" if Path("/usr/share/themes/Mint-Y").is_dir() else "Adwaita"   # Debian has no Mint themes
        Gtk.Settings.get_default().set_property("gtk-theme-name", base + "-Dark" if self.night and base == "Mint-Y" else base)

        manager = WebKit2.WebsiteDataManager(base_data_directory=str(DATA), base_cache_directory=str(CACHE))
        manager.get_cookie_manager().set_persistent_storage(
            str(DATA / "cookies.sqlite"), WebKit2.CookiePersistentStorage.SQLITE)
        manager.get_cookie_manager().set_accept_policy(WebKit2.CookieAcceptPolicy.NO_THIRD_PARTY)
        manager.get_cookie_manager().connect("changed", self.refuse_passes)
        self.context = WebKit2.WebContext(website_data_manager=manager)
        self.context.connect("download-started", self.on_download)
        self.images = {}
        self.content = WebKit2.UserContentManager()
        self.content.add_script(WebKit2.UserScript.new(
            AVIF_SCRIPT, WebKit2.UserContentInjectedFrames.ALL_FRAMES,
            WebKit2.UserScriptInjectionTime.START, None, None))
        # This processor decodes only H.264 in hardware: ask YouTube for that instead of VP9/AV1,
        # which it would otherwise decode in software, slowly and hot.
        self.content.add_script(WebKit2.UserScript.new(
            H264_SCRIPT, WebKit2.UserContentInjectedFrames.ALL_FRAMES,
            WebKit2.UserScriptInjectionTime.START,
            ["https://*.youtube.com/*", "https://*.youtube-nocookie.com/*"], None))
        self.content.register_script_message_handler_with_reply("kuhakuImage", None)
        self.content.connect("script-message-with-reply-received::kuhakuImage", self.on_kuhaku_image)
        self.load_filter()
        # Certificates the user accepted (home devices like picam.local use self-made ones).
        self.tls_pending = {}
        self.content.register_script_message_handler("kuhakuTls")
        self.content.connect("script-message-received::kuhakuTls", self.on_tls_accept)
        for h, pem in self.trusted().items():
            try:
                self.context.allow_tls_certificate_for_host(Gio.TlsCertificate.new_from_pem(pem, -1), h)
            except Exception:
                report()

        self.settings = WebKit2.Settings()
        self.settings.set_enable_smooth_scrolling(True)
        # Everything a modern site expects: video (YouTube), audio, WebGL, calls.
        for name in ("enable_mediasource", "enable_media", "enable_webaudio", "enable_webgl",
                     "enable_media_stream", "enable_webrtc", "enable_encrypted_media", "enable_fullscreen",
                     "enable_media_capabilities", "enable_back_forward_navigation_gestures",
                     "javascript_can_access_clipboard"):
            setter = getattr(self.settings, "set_" + name, None)
            if setter:
                setter(True)
        self.settings.set_media_playback_requires_user_gesture(False)
        # WebKitGTK's own identity looks like an ancient Safari; sites then serve
        # broken or reduced pages. Present a current Safari instead.
        self.settings.set_user_agent(USER_AGENT)
        self.settings.set_enable_developer_extras(True)
        self.settings.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.ALWAYS)
        self.settings.set_default_font_family("Inter")
        self.settings.set_sans_serif_font_family("Inter")

        self.window = Gtk.ApplicationWindow(application=self, title="Mado")
        self.window.set_decorated(False)
        self.window.set_icon_name("mado")
        self.window.maximize()
        self.window.connect("key-press-event", self.on_key)
        self.window.connect("delete-event", lambda *a: self.save() or False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        chrome_ucm = WebKit2.UserContentManager()
        chrome_ucm.register_script_message_handler("chrome")
        chrome_ucm.connect("script-message-received::chrome", self.on_chrome)
        self.chrome = WebKit2.WebView.new_with_user_content_manager(chrome_ucm)
        self.chrome_css = CHROME_HEIGHT
        self.fit_chrome()
        # WebKit scales CSS pixels with the text size (Xft DPI); keep the bar tall enough.
        Gtk.Settings.get_default().connect("notify::gtk-xft-dpi", lambda *a: self.fit_chrome())
        self.chrome.connect("context-menu", lambda *a: True)
        bg = (0.071, 0.071, 0.071) if self.night else (0.969, 0.957, 0.945)
        self.chrome.set_background_color(Gdk.RGBA(*bg, 1))
        self.chrome.load_uri((HERE / "browser.html").as_uri() + f"?theme={'night' if self.night else 'day'}")
        self.chrome.connect("load-changed", lambda v, e: e == WebKit2.LoadEvent.FINISHED and self.push())
        GLib.timeout_add_seconds(30, self.follow_theme)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(160)
        box.pack_start(self.chrome, False, False, 0)
        box.pack_start(self.stack, True, True, 0)
        self.window.add(box)
        self.window.show_all()

    def fit_chrome(self):
        dpi = Gtk.Settings.get_default().get_property("gtk-xft-dpi")
        scale = dpi / 1024 / 96 if dpi and dpi > 0 else 1.0
        self.chrome.set_size_request(-1, round(self.chrome_css * max(1.0, scale)))

    def load_filter(self):
        store = WebKit2.UserContentFilterStore.new(str(CACHE / "filters"))
        rules = [{"trigger": {"url-filter": r"^https?://([^/]*\.)?" + re.escape(d) + r"[:/]",
                              "load-type": ["third-party"]},
                  "action": {"type": "block"}} for d in BLOCKED]
        data = GLib.Bytes.new(json.dumps(rules).encode())

        def done(s, res):
            try:
                self.content.add_filter(s.save_finish(res))
            except Exception:
                report()
        store.save("kuhaku-quiet-2", data, None, done)

    def restore(self):
        try:
            return [u for u in json.loads(STATE.read_text()).get("tabs", []) if u][:20]
        except Exception:
            report()
            return []

    def save(self):
        urls = [t.get_uri() for t in self.tabs if t.get_uri() and t.get_uri() != NEWTAB]
        STATE.write_text(json.dumps({"tabs": urls}))

    # --- tabs -------------------------------------------------------------
    def make_view(self, related=None):
        if related:
            view = WebKit2.WebView(related_view=related, settings=self.view_settings(),
                                   user_content_manager=self.content)
        else:
            view = WebKit2.WebView(web_context=self.context, settings=self.view_settings(),
                                   user_content_manager=self.content)
        for sig in ("notify::title", "notify::uri", "notify::estimated-load-progress", "notify::is-loading"):
            view.connect(sig, self.on_view_changed)
        view.connect("create", self.on_create)
        view.connect("ready-to-show", lambda v: self.add_tab(v))
        view.connect("close", lambda v: self.close_tab(v))
        view.connect("enter-fullscreen", lambda v: self.chrome.hide() or False)
        view.connect("leave-fullscreen", lambda v: self.chrome.show() or False)
        view.connect("mouse-target-changed", self.on_hover)
        view.connect("permission-request", self.on_permission)
        view.connect("decide-policy", self.on_policy)
        view.connect("notify::title", self.on_title)
        view.connect("load-failed-with-tls-errors", self.on_tls_error)
        view.get_find_controller().connect("counted-matches", self.on_found)
        view.get_find_controller().connect("failed-to-find-text", lambda c: self.js("found(0)"))
        return view

    def add_tab(self, view, focus=True):
        at = self.tabs.index(self.current) + 1 if self.current in self.tabs else len(self.tabs)
        self.tabs.insert(at, view)
        view.show()
        self.stack.add(view)
        if focus:
            self.switch(view)
        self.push()

    def new_tab(self, url=NEWTAB, focus=True):
        view = self.make_view()
        view.load_uri(url)
        self.add_tab(view, focus)
        if focus and url == NEWTAB:
            view.grab_focus()
        return view

    def on_create(self, view, _action):
        return self.make_view(related=view)

    def switch(self, view):
        self.current = view
        self.stack.set_visible_child(view)
        view.grab_focus()
        self.push()

    def close_tab(self, view=None):
        view = view or self.current
        if view not in self.tabs:
            return
        i = self.tabs.index(view)
        self.tabs.remove(view)
        self.stack.remove(view)
        view.try_close()
        if not self.tabs:
            self.save()
            self.window.destroy()
            return
        if view is self.current:
            self.switch(self.tabs[min(i, len(self.tabs) - 1)])
        self.push()

    # --- chrome -----------------------------------------------------------
    def js(self, code):
        self.chrome.evaluate_javascript(code, -1, None, None, None, None, None)

    def push(self):
        v = self.current
        if not v:
            return
        uri = v.get_uri() or ""
        state = {
            "url": "" if uri == NEWTAB else uri,
            "title": v.get_title() or "",
            "loading": v.is_loading(),
            "progress": v.get_estimated_load_progress(),
            "back": v.can_go_back(), "forward": v.can_go_forward(),
            "tabs": [{"title": (t.get_title() or host(t.get_uri()) or "New tab") if t.get_uri() != NEWTAB else "New tab",
                      "active": t is v, "loading": t.is_loading()} for t in self.tabs],
            "download": self.download_note,
            "ask": {"host": self.asking["host"], "what": self.asking["what"]}
                   if self.asking and self.asking["view"] is v else None,
        }
        self.js(f"update({json.dumps(state)})")

    def on_view_changed(self, view, _pspec):
        if view is self.current or _pspec.name == "title":
            self.push()

    def on_permission(self, view, request):
        # Notifications and protected video (DRM): yes. Location: no. The microphone, the
        # camera and reading the clipboard: each site asks once, the answer is remembered.
        if isinstance(request, (WebKit2.NotificationPermissionRequest,
                                getattr(WebKit2, "MediaKeySystemPermissionRequest", WebKit2.NotificationPermissionRequest))):
            request.allow()
            return True
        clip = getattr(WebKit2, "ClipboardPermissionRequest", None)
        if isinstance(request, WebKit2.UserMediaPermissionRequest):
            kind = "media"
            audio = WebKit2.user_media_permission_is_for_audio_device(request)
            video = WebKit2.user_media_permission_is_for_video_device(request)
            what = "the microphone and camera" if audio and video else "the camera" if video else "the microphone"
        elif clip and isinstance(request, clip):
            kind, what = "clipboard", "to read the clipboard"
        else:
            request.deny()
            return True
        h = host(view.get_uri()) or "This page"
        said = self.perms().get(h, {}).get(kind)
        if said is not None:
            request.allow() if said else request.deny()
            return True
        if self.asking:
            self.asking["request"].deny()
        self.asking = {"request": request, "host": h, "kind": kind, "what": what, "view": view}
        self.push()
        return True

    def perms(self):
        try:
            return json.loads(PERMS.read_text())
        except Exception:
            report()
            return {}

    def answer_permission(self, allow):
        a, self.asking = self.asking, None
        if not a:
            return
        a["request"].allow() if allow else a["request"].deny()
        p = self.perms()
        p.setdefault(a["host"], {})[a["kind"]] = bool(allow)
        PERMS.parent.mkdir(parents=True, exist_ok=True)
        PERMS.write_text(json.dumps(p, indent=1))
        self.push()

    def on_policy(self, _view, decision, kind):
        # Links into apps (slack://, claude://, mailto:, zoommtg:...) go to those
        # apps, so sign-ins started from Slack or Claude return to them.
        if kind not in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            return False
        action = decision.get_navigation_action()
        uri = action.get_request().get_uri() or ""
        if kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            self.pick_identity(_view, uri, action)
        scheme = uri.split(":", 1)[0].lower()
        if kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION and scheme == "https" and \
                self.is_honest(urllib.parse.urlsplit(uri).hostname):
            self.forget_pass(uri, decision.use)     # each page load asks Cloudflare afresh
            return True
        if not scheme or scheme in WEB_SCHEMES:
            return False
        decision.ignore()
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error:
            pass
        return True

    # --- identity: Safari on a Mac, except where Cloudflare checks ----------
    def honest_hosts(self):
        try:
            return set(json.loads(NATIVE_HOSTS.read_text()))
        except Exception:
            report()
            return set(NATIVE_SEED)

    def is_honest(self, host):
        host = (host or "").lower()
        return any(host == h or host.endswith("." + h) for h in self.honest_hosts())

    def pick_identity(self, view, uri, action):
        host = urllib.parse.urlsplit(uri).hostname or ""
        if not host or host.endswith("challenges.cloudflare.com"):
            return
        if self.is_honest(host):
            ua = NATIVE_UA
        elif self.is_honest(urllib.parse.urlsplit(view.get_uri() or "").hostname) and \
                action.get_navigation_type() == WebKit2.NavigationType.OTHER:
            return                          # a frame or script inside an honest page: leave it be
        else:
            ua = USER_AGENT
        s = view.get_settings()
        if s.get_user_agent() != ua:
            s.set_user_agent(ua)

    def on_title(self, view, _pspec):
        if (view.get_title() or "").strip() not in CHALLENGE_TITLES:
            return
        uri = view.get_uri() or ""
        host = urllib.parse.urlsplit(uri).hostname or ""
        if not host:
            return
        if self.is_honest(host):
            self.drop_clearance(view, uri, host)
            return
        hosts = self.honest_hosts() | {".".join(host.split(".")[-2:])}
        NATIVE_HOSTS.parent.mkdir(parents=True, exist_ok=True)
        NATIVE_HOSTS.write_text(json.dumps(sorted(hosts)))
        view.get_settings().set_user_agent(NATIVE_UA)
        view.reload()

    def drop_clearance(self, view, uri, host):
        """Stuck on a check anyway (any site): forget the pass and try again, a few times at most."""
        now = time.time()
        tries = [t for t in getattr(self, "cf_tries", {}).get(host, []) if now - t < 60]
        if len(tries) >= 3:
            return                          # let the person see the check rather than loop
        self.cf_tries = getattr(self, "cf_tries", {})
        self.cf_tries[host] = tries + [now]
        self.forget_pass(uri, view.reload)

    def refuse_passes(self, cm):
        """Never keep a Cloudflare pass for the sites that check us: it is minted with a poor
        score, and every later request carrying it (page loads and the site's own background
        calls) is sent to a checkbox Mado cannot pass. Without it, those sites simply load."""
        if getattr(self, "refusing", False):
            return
        self.refusing = True

        def sweep():
            def got(cm, res):
                self.refusing = False
                try:
                    cookies = cm.get_all_cookies_finish(res)
                except GLib.Error:
                    return
                for c in cookies:
                    if c.get_name() == "cf_clearance" and self.is_honest((c.get_domain() or "").lstrip(".")):
                        cm.delete_cookie(c, None, None)
            cm.get_all_cookies(None, got)
        GLib.idle_add(sweep)

    def forget_pass(self, uri, then):
        """Cloudflare's stored pass (cf_clearance) is what breaks Mado: with it, the next page
        load on claude.ai demands a checkbox this WebKit can never pass; without it, the check
        clears on its own. So Mado does not present it on page loads, then calls then()."""
        def got(cm, res):
            try:
                stale = [c for c in cm.get_cookies_finish(res) if c.get_name() == "cf_clearance"]
            except GLib.Error:
                stale = []
            if not stale:
                then()
                return
            left = [len(stale)]

            def gone(cm, res):
                try:
                    cm.delete_cookie_finish(res)
                except GLib.Error:
                    pass
                left[0] -= 1
                if not left[0]:
                    then()
            for c in stale:
                cm.delete_cookie(c, None, gone)
        self.context.get_website_data_manager().get_cookie_manager().get_cookies(uri, None, got)

    def view_settings(self):
        """Each tab its own copy of the settings, so one tab's identity does not leak into another."""
        s = WebKit2.Settings()
        for spec in self.settings.list_properties():
            if spec.name in ("enable-dns-prefetching", "load-icons-ignoring-image-load-setting"):
                continue                    # deprecated: reading them only prints a warning
            if spec.flags & GObject.ParamFlags.READABLE and spec.flags & GObject.ParamFlags.WRITABLE \
                    and not spec.flags & GObject.ParamFlags.CONSTRUCT_ONLY:
                try:
                    s.set_property(spec.name, self.settings.get_property(spec.name))
                except Exception:
                    report()
        return s

    def trusted(self):
        try:
            return json.loads(TRUSTED.read_text())
        except Exception:
            report()
            return {}

    def on_tls_error(self, view, uri, cert, _errors):
        h = urllib.parse.urlsplit(uri).hostname or uri
        token = base64.urlsafe_b64encode(os.urandom(18)).decode()
        self.tls_pending[h] = (cert, uri, view, token)
        esc = lambda t: GLib.markup_escape_text(t)
        page = TLS_PAGE.replace("{HOST}", esc(h)).replace("{HOSTJS}", json.dumps(h)).replace("{TOKENJS}", json.dumps(token))
        page = page.replace("{BG}", "#121212" if self.night else "#f7f4f1").replace("{FG}", "#ececec" if self.night else "#111111")
        page = page.replace("{ACCENT}", "#f0705a" if self.night else "#e0301e")
        view.load_alternate_html(page, uri, None)
        return True

    def on_tls_accept(self, _ucm, result):
        try:
            msg = json.loads(result.get_js_value().to_json(0))
            h, token = msg["h"], msg["t"]
        except Exception:
            report()
            return
        if h not in self.tls_pending or self.tls_pending[h][3] != token:
            return
        cert, uri, view, _token = self.tls_pending.pop(h)
        self.context.allow_tls_certificate_for_host(cert, h)
        t = self.trusted()
        t[h] = cert.props.certificate_pem
        TRUSTED.parent.mkdir(parents=True, exist_ok=True)
        TRUSTED.write_text(json.dumps(t, indent=1))
        view.load_uri(uri)

    def on_kuhaku_image(self, _ucm, value, reply):
        ctx = value.get_context()
        try:
            msg = json.loads(value.to_string())
            url, referer = msg.get("u", ""), msg.get("r", "")
        except Exception:
            url = referer = ""

        def answer(data_url):
            reply.return_value(JavaScriptCore.Value.new_string(ctx, data_url) if data_url
                               else JavaScriptCore.Value.new_null(ctx))
            return False

        if not url.startswith(("http://", "https://")):
            answer(None)
            return True
        # Any page can ask for this, so it must not become a way for a website to read the
        # home network or this computer: devices nearby only for a tab that has that device open.
        open_hosts = {urllib.parse.urlsplit(t.get_uri() or "").hostname for t in self.tabs}

        def allowed(u):
            h = urllib.parse.urlsplit(u).hostname
            return u.startswith(("http://", "https://")) and (h in open_hosts or not private_host(h))

        class Guard(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                if not allowed(newurl):
                    raise urllib.error.URLError("redirect to a private address")
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        if url in self.images:
            answer(self.images[url])
            return True

        def work():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": referer,
                                                           "Accept": "image/avif,image/*;q=0.8"})
                if not allowed(url):
                    raise ValueError("private address")
                raw = urllib.request.build_opener(Guard).open(req, timeout=20).read(30_000_000)
                loader = GdkPixbuf.PixbufLoader()
                loader.write(raw)
                loader.close()
                pix = loader.get_pixbuf()
                if pix.get_has_alpha():
                    _ok, out = pix.save_to_bufferv("png", [], [])
                    mime = "image/png"
                else:
                    _ok, out = pix.save_to_bufferv("jpeg", ["quality"], ["90"])
                    mime = "image/jpeg"
                data_url = f"data:{mime};base64," + base64.b64encode(bytes(out)).decode()
                if len(self.images) > 200:
                    self.images.clear()
                self.images[url] = data_url
            except Exception:
                data_url = None
            GLib.idle_add(answer, data_url)
        threading.Thread(target=work, daemon=True).start()
        return True

    def follow_theme(self):
        """Day and night follow Kūhaku while Mado is open, not only when it was opened."""
        night = theme() == "night"
        if night != self.night:
            self.night = night
            Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", night)
            bg = (0.071, 0.071, 0.071) if night else (0.969, 0.957, 0.945)
            self.chrome.set_background_color(Gdk.RGBA(*bg, 1))
            self.chrome.load_uri((HERE / "browser.html").as_uri() + f"?theme={'night' if night else 'day'}")
        return True

    def on_hover(self, _view, hit, _mods):
        link = hit.get_link_uri() if hit.context_is_link() else ""
        self.js(f"hover({json.dumps(link or '')})")

    def on_found(self, _c, n):
        self.js(f"found({n})")

    def on_chrome(self, _m, result):
        try:
            msg = json.loads(result.get_js_value().to_string())
        except Exception:
            report()
            return
        kind, v = msg.get("type"), self.current
        if kind == "perm":
            return self.answer_permission(bool(msg.get("allow")))
        if kind == "go":
            url = to_url(msg.get("text", ""))
            if url and v:
                v.load_uri(url)
                v.grab_focus()
        elif kind == "back" and v:
            v.go_back()
        elif kind == "forward" and v:
            v.go_forward()
        elif kind == "reload" and v:
            v.stop_loading() if v.is_loading() else v.reload()
        elif kind == "tab":
            i = msg.get("index", 0)
            if 0 <= i < len(self.tabs):
                self.switch(self.tabs[i])
        elif kind == "closeTab":
            i = msg.get("index")
            self.close_tab(self.tabs[i] if i is not None and 0 <= i < len(self.tabs) else None)
        elif kind == "newTab":
            self.new_tab()
        elif kind == "find" and v:
            fc = v.get_find_controller()
            text = msg.get("text", "")
            if not text:
                fc.search_finish()
            else:
                opts = WebKit2.FindOptions.CASE_INSENSITIVE | WebKit2.FindOptions.WRAP_AROUND
                if msg.get("backwards"):
                    opts |= WebKit2.FindOptions.BACKWARDS
                if msg.get("next"):
                    fc.search_next() if not msg.get("backwards") else fc.search_previous()
                else:
                    fc.count_matches(text, opts, 1000)
                    fc.search(text, opts, 1000)
        elif kind == "doneEditing" and v:
            v.get_find_controller().search_finish()
            v.grab_focus()
        elif kind == "height":
            self.chrome_css = int(msg.get("value", CHROME_HEIGHT))
            self.fit_chrome()
        elif kind == "minimize":
            self.window.iconify()
        elif kind == "quit":
            self.save()
            self.window.destroy()
        elif kind == "openDownloads":
            Gio.AppInfo.launch_default_for_uri((HOME / "Downloads").as_uri(), None)

    # --- downloads --------------------------------------------------------
    def on_download(self, _ctx, download):
        def decide(d, name):
            folder = HOME / "Downloads"
            folder.mkdir(exist_ok=True)
            target = folder / Path(name).name
            stem, n = target.stem, 1
            while target.exists():
                target = folder / f"{stem} ({n}){target.suffix}"
                n += 1
            d.set_destination(target.as_uri())
            self.download_names[id(d)] = target.name
            return True

        def progress(d, _p):
            self.download_note = {"name": self.download_names.get(id(d), ""), "progress": d.get_estimated_progress()}
            self.push()

        def finished(d):
            self.download_note = {"name": self.download_names.get(id(d), ""), "done": True}
            self.push()
            GLib.timeout_add_seconds(6, self.clear_download)

        download.connect("decide-destination", decide)
        download.connect("notify::estimated-progress", progress)
        download.connect("finished", finished)

    def clear_download(self):
        self.download_note = None
        self.push()
        return False

    # --- keys -------------------------------------------------------------
    def on_key(self, _w, ev):
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        alt = ev.state & Gdk.ModifierType.MOD1_MASK
        shift = ev.state & Gdk.ModifierType.SHIFT_MASK
        k = Gdk.keyval_name(ev.keyval)
        v = self.current
        if ctrl and k in ("l", "L"):
            self.chrome.grab_focus(); self.js("editAddress()")
        elif ctrl and k in ("t", "T"):
            self.new_tab()
        elif ctrl and k in ("w", "W"):
            self.close_tab()
        elif ctrl and k in ("q", "Q"):
            self.save(); self.window.destroy()
        elif ctrl and k in ("Tab", "ISO_Left_Tab", "Page_Down", "Page_Up") and len(self.tabs) > 1:
            step = -1 if (shift or k == "Page_Up" or k == "ISO_Left_Tab") else 1
            self.switch(self.tabs[(self.tabs.index(v) + step) % len(self.tabs)])
        elif ctrl and k in ("f", "F"):
            self.chrome.grab_focus(); self.js("editFind()")
        elif (ctrl and k in ("r", "R")) or k == "F5":
            v.reload()
        elif (alt and k == "Left") or k == "XF86Back":
            v.go_back()
        elif (alt and k == "Right") or k == "XF86Forward":
            v.go_forward()
        elif ctrl and k in ("plus", "equal", "KP_Add"):
            v.set_zoom_level(min(3, v.get_zoom_level() + 0.1))
        elif ctrl and k in ("minus", "KP_Subtract"):
            v.set_zoom_level(max(0.3, v.get_zoom_level() - 0.1))
        elif ctrl and k in ("0", "KP_0"):
            v.set_zoom_level(1)
        elif ctrl and k.isdigit() and k != "0":
            i = min(int(k), len(self.tabs)) - 1
            self.switch(self.tabs[-1 if k == "9" else i])
        elif k == "Escape" and v and v.is_loading():
            v.stop_loading()
            return False
        else:
            return False
        return True


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = Browser()
    # Accept plain words and bare domains on the command line, not just URIs.
    args = [sys.argv[0]] + [to_url(a) or a for a in sys.argv[1:]]
    sys.exit(app.run(args))
