"""Load the home screen off-screen and evaluate an expression: catches script errors before a restart.
python3 jscheck.py "typeof show" """

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk, WebKit2

page = sys.argv[2] if len(sys.argv) > 2 else (Path(__file__).resolve().parent.parent / "index.html").as_uri()
w = Gtk.OffscreenWindow()
w.set_default_size(1440, 900)
s = WebKit2.Settings()
s.set_enable_write_console_messages_to_stdout(True)
v = WebKit2.WebView(settings=s)
w.add(v)
w.show_all()
v.load_uri(page)


def run():
    def done(v_, r):
        try:
            print("RESULT", v_.evaluate_javascript_finish(r).to_string())
        except Exception as e:
            print("EXC", e)
        Gtk.main_quit()

    v.evaluate_javascript(sys.argv[1], -1, None, None, None, done)


v.connect("load-changed", lambda v, e: e == WebKit2.LoadEvent.FINISHED and GLib.timeout_add(800, run))
Gtk.main()
