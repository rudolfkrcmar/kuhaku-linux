#!/usr/bin/env python3
"""Tegami (手紙, "letter"): Kūhaku's mail.

Mail is downloaded and kept on the computer, so it can be read and answered
offline. Replies wait in the Outbox and are sent by themselves as soon as the
computer is online. Several accounts; Gmail (with an app password), Seznam,
iCloud, Fastmail and any IMAP/SMTP mailbox. Passwords live in the system
keyring, never in a file.

This is the window: the page (mail/tegami.html) in WebKit, on GTK 4 where it is\ninstalled (any Linux desktop, X11 or Wayland, or a Flatpak) and GTK 3 otherwise. The mail itself
is tegami_core.py, which the Mac app drives too.

    tegami
"""

import os
import signal
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from kuhaku_webapp import GLib, WebApp  # GTK 4 where it is installed, GTK 3 otherwise
from tegami_core import Mail


class Tegami:
    """The host tegami_core.Mail talks to: js(), later(), minimize(), quit()."""

    def __init__(self):
        # Spelling: red lines under typos in Czech and English while writing (dictionaries via enchant;
        # Czech lives in ~/.config/enchant/hunspell). Right-click on a word offers the fixes.
        self.app = WebApp(
            "space.kuhaku.Tegami",
            "Tegami",
            "mail",
            on_message=self.handle,
            zoom=1.12,  # app type a little larger than the page's own, for reading
            spelling=["cs_CZ", "en_US"],
            menus=True,  # the spelling menu in Kūhaku's look, readable at night
            on_ready=self.ready,
            pick_words=("Attach files", "Attach"),
        )
        self.mail = Mail(self)
        self.app.load((HERE / "mail" / "tegami.html").as_uri())

    def handle(self, msg):
        self.mail.handle(msg)

    def ready(self):
        self.mail.ready()
        if os.environ.get("TEGAMI_TEST_DIR") and os.environ.get("TEGAMI_TEST_OPEN"):
            # Sandbox: open the first letter and a reply to look at.
            GLib.timeout_add(600, lambda: self.app.eval("document.querySelector('[data-open]')?.click()") and False)

    # the host, for the mail ------------------------------------------------------------------
    def js(self, fn, payload=None):
        self.app.js(fn, payload)

    def later(self, f, *args):
        self.app.later(f, *args)

    def minimize(self):
        self.app.minimize()

    def quit(self):
        self.app.quit()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    tegami = Tegami()
    tegami.app.run()
