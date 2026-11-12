#!/usr/bin/env python3
"""Loads each Kūhaku page off-screen and fails if any script error shows in the console.

    python3 dev/pagecheck.py            every page
    python3 dev/pagecheck.py FILE.html  just these
"""
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk, WebKit2

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["index.html", "miru/miru.html", "kansei/kansei.html", "kiroku/kiroku.html", "mail/tegami.html",
         "ide/tsukuru.html", "browser.html", "newtab.html"]
# Errors that only mean "not inside Kūhaku's window" (no native bridge off-screen).
EXPECTED = ("messageHandlers", "webkit.messageHandlers", "Failed to load resource")


def check(page):
    errors = []
    win = Gtk.OffscreenWindow()
    win.set_default_size(1280, 800)
    settings = WebKit2.Settings()
    settings.set_allow_file_access_from_file_urls(True)
    view = WebKit2.WebView(settings=settings)
    win.add(view)
    win.show_all()
    loop = GLib.MainLoop()

    # Every script error on the page is passed back here through a small hook.
    ucm = view.get_user_content_manager()
    ucm.register_script_message_handler("pagecheck")
    ucm.connect("script-message-received::pagecheck",
                lambda _m, r: errors.append(r.get_js_value().to_string()))
    ucm.add_script(WebKit2.UserScript.new(
        "window.addEventListener('error', e => window.webkit.messageHandlers.pagecheck.postMessage("
        "String(e.message) + ' (line ' + e.lineno + ')'));",
        WebKit2.UserContentInjectedFrames.TOP_FRAME, WebKit2.UserScriptInjectionTime.START, None, None))
    view.connect("load-changed", lambda _v, e: e == WebKit2.LoadEvent.FINISHED and GLib.timeout_add(1200, loop.quit))
    GLib.timeout_add(10000, loop.quit)
    text = (ROOT / page).read_text(encoding="utf-8").replace("{THEME}", "day")
    view.load_html(text, (ROOT / page).as_uri())
    loop.run()
    win.destroy()
    return [e for e in errors if not any(x in e for x in EXPECTED)]


def main():
    pages = sys.argv[1:] or [p for p in PAGES if (ROOT / p).exists()]
    bad = 0
    for p in pages:
        errs = check(p)
        print(f"{'ok  ' if not errs else 'FAIL'} {p}" + "".join(f"\n       {e}" for e in errs))
        bad += bool(errs)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
