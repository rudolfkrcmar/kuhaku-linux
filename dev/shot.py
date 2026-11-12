"""Render the home screen off-screen after some JavaScript and save a PNG.
python3 shot.py "show('working','Hi')" out.png"""

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk, WebKit2

js, out = sys.argv[1], sys.argv[2]
w = Gtk.OffscreenWindow()
w.set_default_size(1440, 900)
v = WebKit2.WebView()
w.add(v)
w.show_all()
v.load_uri((Path(__file__).resolve().parent.parent / "index.html").as_uri())


def snap():
    v.evaluate_javascript(js, -1, None, None, None, None, None)
    GLib.timeout_add(1500, lambda: (w.get_pixbuf().savev(out, "png", [], []), Gtk.main_quit()))


v.connect("load-changed", lambda v, e: e == WebKit2.LoadEvent.FINISHED and GLib.timeout_add(800, snap))
Gtk.main()
