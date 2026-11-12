#!/usr/bin/env python3
"""A clean window for signing in to an AI account (Claude, OpenAI, Gemini).

Mado keeps your everyday sign-ins, so a sign-in page opened there finds the account you
already use, and a second account (work) can never be chosen. This window starts with no
cookies and forgets everything when it closes. The command line tools open it in place of
the browser (see signin-bin/xdg-open); it closes itself once the page has handed the
sign-in back to the tool on this computer.

Kūhaku has no window frames, so it brings its own thin bar: what is being signed in to, a
reload, and a ×. Closing it (× or Esc) also stops the waiting sign-in tool.
"""
import json
import os
import signal
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GLib, Gtk, WebKit2  # noqa: E402
from kuhaku_log import report  # faults go to the log instead of vanishing

HOME = Path.home()
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15"
# Cloudflare's human check (claude.ai/design) loops forever when a Linux WebKit claims to be a Mac:
# KUHAKU_SIGNIN_UA=native tells the truth instead.
NATIVE_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15"
# Google's sign-in refuses embedded browsers ("This browser or app may not be secure");
# shown a Firefox there, it signs in normally.
FIREFOX_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0"


def google_as_firefox(view):
    """While on Google's sign-in, say Firefox; elsewhere the usual agent."""
    usual = view.get_settings().get_user_agent()
    usual = USER_AGENT if usual == FIREFOX_UA else usual

    def decide(v, decision, kind):
        if kind in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            host = urlparse(decision.get_navigation_action().get_request().get_uri()).hostname or ""
            v.get_settings().set_user_agent(FIREFOX_UA if host == "accounts.google.com" else usual)
        return False
    view.connect("decide-policy", decide)


def night():
    def load(p):
        try:
            return json.loads(p.read_text())
        except Exception:
            report()
            return {}
    cfg = load(HOME / ".config/kuhaku/config.json")
    choice = load(HOME / ".local/state/kuhaku/state.json").get("theme") or cfg.get("theme", "auto")
    if choice in ("day", "night"):
        return choice == "night"
    h = time.localtime().tm_hour
    return h >= cfg.get("night_from", 20) or h < cfg.get("night_until", 7)


def style():
    bg, fg, muted, faint, accent = (("#121212", "#ececec", "#9a9a9a", "rgba(236,236,236,.08)", "#f0705a") if night()
                                    else ("#f7f4f1", "#111111", "#666666", "rgba(17,17,17,.08)", "#e0301e"))
    css = f"""
    .kbar {{ background:{bg}; border-bottom:1px solid {faint}; padding:0 10px 0 22px; min-height:46px; }}
    .kbar label {{ color:{muted}; font-family:'Inter Variable','Inter',sans-serif; font-size:14px; font-weight:300; }}
    .kbar button {{ background:none; border:0; box-shadow:none; color:{muted}; min-width:34px; min-height:34px;
                   padding:0; font-family:'Inter Variable','Inter',sans-serif; font-size:20px; font-weight:200; }}
    .kbar button:hover {{ color:{accent}; }}
    .kframe {{ border:1px solid {faint}; }}
    """
    p = Gtk.CssProvider()
    p.load_from_data(css.encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), p, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


class Window:
    """One sign-in window: the page, or a popup the page opened (Google's and Apple's sign-ins)."""
    count = 0

    def __init__(self, title, view, on_close, size=(980, 820)):
        Window.count += 1
        self.view, self.on_close = view, on_close
        self.win = Gtk.Window(title="Sign in")
        self.win.set_decorated(False)
        self.win.set_default_size(*size)
        self.win.set_position(Gtk.WindowPosition.CENTER)
        self.win.get_style_context().add_class("kframe")
        self.win.connect("destroy", self.gone)
        self.win.connect("key-press-event", self.key)

        bar = Gtk.Box(spacing=4)
        bar.get_style_context().add_class("kbar")
        self.label = Gtk.Label(label=title, xalign=0)
        bar.pack_start(self.label, True, True, 0)
        again = Gtk.Button(label="↻", tooltip_text="Load again")
        again.connect("clicked", lambda *_: self.view.reload())
        close = Gtk.Button(label="×", tooltip_text="Close (Esc)")
        close.connect("clicked", lambda *_: self.win.destroy())
        bar.pack_end(close, False, False, 0)
        bar.pack_end(again, False, False, 0)
        # The bar moves the window, as a frame would.
        drag = Gtk.EventBox()
        drag.add(bar)
        drag.connect("button-press-event", lambda w, e: self.win.begin_move_drag(
            e.button, int(e.x_root), int(e.y_root), e.time) if e.button == 1 else None)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(drag, False, False, 0)
        box.pack_start(view, True, True, 0)
        self.win.add(box)

        view.connect("create", self.popup)
        view.connect("close", lambda *_: self.win.destroy())
        view.connect("load-failed", self.failed)

    def show(self):
        self.win.show_all()
        # Started behind the home screen: come to the front, where the user is.
        self.win.set_keep_above(True)
        self.win.present()
        GLib.timeout_add_seconds(2, lambda: self.win.set_keep_above(False) or False)

    def popup(self, view, _action):
        # A real popup, related to this page, so it can hand the sign-in back (window.opener).
        child = WebKit2.WebView.new_with_related_view(view)
        child.get_settings().set_user_agent(view.get_settings().get_user_agent())
        google_as_firefox(child)
        w = Window(self.label.get_text(), child, None, size=(560, 700))
        w.win.set_transient_for(self.win)
        child.connect("ready-to-show", lambda *_: w.show())
        return child

    def failed(self, _view, _event, uri, error):
        if urlparse(uri).hostname not in ("localhost", "127.0.0.1"):
            self.label.set_text(f"Could not load the page: {error.message}. Try ↻.")
        return False

    def key(self, _w, e):
        if e.keyval == Gdk.KEY_Escape:
            self.win.destroy()
            return True
        return False

    def gone(self, *_):
        if self.on_close:
            self.on_close()
        Window.count -= 1
        if Window.count <= 0:
            Gtk.main_quit()


def main(url):
    style()
    title = os.environ.get("KUHAKU_SIGNIN_TITLE") or "Sign in"
    parent = int(os.environ.get("KUHAKU_SIGNIN_PARENT") or 0)
    finished = [False]

    context = WebKit2.WebContext(website_data_manager=WebKit2.WebsiteDataManager.new_ephemeral())
    view = WebKit2.WebView(web_context=context)
    view.get_settings().set_user_agent(NATIVE_UA if os.environ.get("KUHAKU_SIGNIN_UA") == "native" else USER_AGENT)
    google_as_firefox(view)
    view.get_settings().set_javascript_can_open_windows_automatically(True)

    def closed():
        # Closed before the sign-in came back: stop the tool that is waiting for it.
        if not finished[0] and parent > 1:
            try:
                os.kill(parent, signal.SIGTERM)
            except OSError:
                pass
        Gtk.main_quit()

    main_win = Window(title, view, closed)

    def done():
        if not finished[0]:
            finished[0] = True
            main_win.label.set_text("Signed in. This window closes by itself.")
            GLib.timeout_add_seconds(2, lambda: main_win.win.destroy() or False)

    def loaded(v, _event):
        # The page handing the sign-in back to the tool on this computer (it may redirect on at once).
        if urlparse(v.get_uri() or "").hostname in ("localhost", "127.0.0.1"):
            done()

    def tool_alive():
        # The tool finished (signed in, or gave up): nothing more to do here.
        try:
            os.kill(parent, 0)
            return True
        except OSError:
            done()
            return False
    view.connect("load-changed", loaded)
    if parent > 1:
        GLib.timeout_add_seconds(1, tool_alive)
    view.load_uri(url)
    main_win.show()
    Gtk.main()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    if len(sys.argv) < 2 or not sys.argv[1].startswith(("http://", "https://")):
        sys.exit("usage: kuhaku-signin URL")
    main(sys.argv[1])
