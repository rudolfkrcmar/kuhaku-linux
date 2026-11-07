#!/usr/bin/env python3
"""The installer's screens to play with: nothing is checked, erased or installed.

Same installer.html, but every message goes to a pretend computer: a made-up drive, a pretend
install that walks through the steps, and Escape or Close quits. Useful for trying the screens
and for website pictures.
"""
import json
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GLib, Gtk, WebKit2

HERE = Path(__file__).resolve().parent
STEPS = ["Preparing the drive", "Copying Kūhaku", "Making it start", "Bringing your things", "Finishing"]


class Preview:
    def __init__(self):
        self.win = Gtk.Window(title="Install Kūhaku (preview)")
        self.win.set_decorated(False)
        self.win.fullscreen()
        self.win.connect("destroy", Gtk.main_quit)
        ucm = WebKit2.UserContentManager()
        ucm.register_script_message_handler("install")
        ucm.connect("script-message-received::install", self.on_message)
        self.view = WebKit2.WebView.new_with_user_content_manager(ucm)
        self.view.set_background_color(Gdk.RGBA(0.969, 0.957, 0.945, 1))
        self.view.connect("context-menu", lambda *a: True)
        self.win.add(self.view)
        self.view.load_uri((HERE / "installer.html").as_uri())
        self.view.connect("load-changed", lambda v, e: e == WebKit2.LoadEvent.FINISHED and self.js(
            "facts", {"disk": {"dev": "/dev/pretend", "size": 512e9, "model": "Pretend drive (preview)"},
                      "carry": None, "zone": "Europe/Prague"}))
        self.win.show_all()

    def js(self, fn, data=None):
        self.view.evaluate_javascript(f"{fn}({json.dumps(data)})", -1, None, None, None, None, None)

    def on_message(self, _m, result):
        msg = json.loads(result.get_js_value().to_string())
        k = msg.get("type")
        if k in ("close", "restart"):
            self.win.destroy()
        elif k == "check":
            ok = len(msg.get("password", "")) >= 6
            self.js("checked", {"ok": ok, "why": "" if ok else "At least six characters.", "user": "me"})
        elif k == "install":
            self.pretend(msg)

    def pretend(self, msg):
        self.js("detail", f"Preview: nothing is installed. Hidden: {msg.get('hide') or 'none'}; to fetch: {msg.get('add') or 'none'}")
        plan = [(i + 1, t) for i, t in enumerate(STEPS)]

        def tick(state={"i": 0, "pct": 0}):
            if state["i"] == 2 and state["pct"] < 100:          # just after "Copying Kūhaku" appeared
                state["pct"] += 10
                self.js("pct", state["pct"])
                return True
            if state["i"] == len(plan):
                self.js("done")
                return False
            n, text = plan[state["i"]]
            self.js("step", {"n": n, "text": text})
            self.js("detail", f"(pretend) {text}")
            state["i"] += 1
            return True
        GLib.timeout_add(900, tick)


if __name__ == "__main__":
    Preview()
    Gtk.main()
