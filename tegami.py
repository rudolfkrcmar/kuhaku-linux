#!/usr/bin/env python3
"""Tegami (手紙, "letter"): Kūhaku's mail.

Mail is downloaded and kept on the computer, so it can be read and answered
offline. Replies wait in the Outbox and are sent by themselves as soon as the
computer is online. Several accounts; Gmail (with an app password), Seznam,
iCloud, Fastmail and any IMAP/SMTP mailbox. Passwords live in the system
keyring, never in a file.

This is Kūhaku's window: the page (mail/tegami.html) in WebKit. The mail itself
is tegami_core.py, which the Mac app drives too.

    tegami
"""
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GLib, Gtk, WebKit2

HERE = Path(__file__).resolve().parent
HOME = Path.home()
sys.path.insert(0, str(HERE))
from terminal import theme  # noqa: E402
from tegami_core import Mail  # noqa: E402


class Tegami:
    def __init__(self):
        self.night = theme() == "night"
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", self.night)
        self.win = Gtk.Window(title="Tegami")
        self.win.set_decorated(False)
        self.win.set_icon_name("tegami")
        self.win.maximize()
        self.win.connect("destroy", Gtk.main_quit)
        ucm = WebKit2.UserContentManager()
        ucm.register_script_message_handler("mail")
        ucm.connect("script-message-received::mail", self.on_message)
        self.view = WebKit2.WebView.new_with_user_content_manager(ucm)
        self.view.set_zoom_level(1.12)  # app type a little larger than the page's own, for reading
        st = self.view.get_settings()
        st.set_allow_file_access_from_file_urls(True)
        st.set_enable_write_console_messages_to_stdout(bool(os.environ.get("TEGAMI_DEBUG")))
        bg = (0.071, 0.071, 0.071) if self.night else (0.969, 0.957, 0.945)
        self.view.set_background_color(Gdk.RGBA(*bg, 1))
        # Spelling: red lines under typos in Czech and English while writing (dictionaries via enchant;
        # Czech lives in ~/.config/enchant/hunspell). Right-click on a word offers the fixes; elsewhere no menu.
        ctx = self.view.get_context()
        ctx.set_spell_checking_enabled(True)
        ctx.set_spell_checking_languages(["cs_CZ", "en_US"])
        self.view.connect("context-menu", lambda _v, _m, _e, hit: not hit.context_is_editable())
        self.view.connect("decide-policy", self.on_policy)
        self.win.add(self.view)
        self.mail = Mail(self)
        self.view.load_uri((HERE / "mail" / "tegami.html").as_uri() + f"?theme={'night' if self.night else 'day'}")
        self.view.connect("load-changed", lambda v, e: e == WebKit2.LoadEvent.FINISHED and self.ready())
        self.win.show_all()

    def ready(self):
        self.mail.ready()
        if os.environ.get("TEGAMI_TEST_DIR") and os.environ.get("TEGAMI_TEST_OPEN"):
            # Sandbox: open the first letter and a reply to look at.
            GLib.timeout_add(600, lambda: self.view.evaluate_javascript(
                "document.querySelector('[data-open]')?.click()", -1, None, None, None, None, None) and False)

    # the host, for the mail ------------------------------------------------------------------
    def js(self, fn, payload=None):
        self.view.evaluate_javascript(f"{fn}({json.dumps(payload)})", -1, None, None, None, None, None)

    def later(self, f, *args):
        GLib.idle_add(lambda: f(*args) and False)

    def minimize(self):
        self.win.iconify()

    def quit(self):
        self.win.destroy()

    def on_message(self, _m, result):
        try:
            msg = json.loads(result.get_js_value().to_string())
        except Exception:
            return
        self.mail.handle(msg)

    def on_policy(self, _v, decision, kind):
        if kind in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri.startswith(("http://", "https://", "mailto:")):
                if uri.startswith("mailto:"):
                    self.js("composeTo", uri[7:].split("?")[0])
                else:
                    subprocess.Popen([str(HOME / ".local/bin/mado"), uri], start_new_session=True)
                decision.ignore()
                return True
        return False


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    Tegami()
    Gtk.main()
