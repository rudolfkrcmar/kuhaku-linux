#!/usr/bin/env python3
"""Kūhaku Terminal: a shell in Kūhaku's quiet design.

One thin line on top with the title, the terminal below with generous margins
and Kūhaku's colours. Keys work like a Mac: Cmd+C / Cmd+V copy and paste,
Ctrl+C interrupts. (On this keyboard Cmd arrives as Control and the physical
Ctrl as Super, so Super+key is sent to the shell as Ctrl+key.)

    kuhaku-terminal                 a login shell
    kuhaku-terminal -e "command"    run a command, then stay in a shell
"""

import json
import os
import signal
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, GLib, Gtk, Pango, Vte
from kuhaku_log import report  # faults go to the log instead of vanishing

HOME = Path.home()

THEMES = {
    "day": {
        "bg": "#f7f4f1",
        "fg": "#111111",
        "muted": "#666666",
        "accent": "#e0301e",
        "select": "#f6d3cd",
        "palette": [
            "#111111",
            "#c23b22",
            "#3f7d4f",
            "#a06a00",
            "#2f5f8a",
            "#7b4b8a",
            "#2a7a7a",
            "#bdb8b2",
            "#666666",
            "#e0301e",
            "#4f9a62",
            "#c08400",
            "#3f78ad",
            "#9a5fad",
            "#36999a",
            "#f7f4f1",
        ],
    },
    "night": {
        "bg": "#121212",
        "fg": "#ececec",
        "muted": "#9a9a9a",
        "accent": "#f0705a",
        "select": "#3a2422",
        "palette": [
            "#121212",
            "#e8836f",
            "#8fc39b",
            "#e3c07e",
            "#8fb3d9",
            "#c4a3d6",
            "#86c9c9",
            "#cfcfcf",
            "#5a5a5a",
            "#f0705a",
            "#a8d6b2",
            "#f0d29a",
            "#a9c7e8",
            "#d6bce4",
            "#a3dada",
            "#ececec",
        ],
    },
}


def theme():
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


def rgba(hex_):
    c = Gdk.RGBA()
    c.parse(hex_)
    return c


class Terminal:
    def __init__(self, command=None):
        t = THEMES[theme()]
        self.t = t
        self.win = Gtk.Window(title="Terminal")
        self.win.set_decorated(False)
        self.win.set_icon_name("kuhaku-terminal")
        self.win.maximize()
        self.win.connect("destroy", Gtk.main_quit)
        self.win.connect("key-press-event", self.on_key)

        css = Gtk.CssProvider()
        css.load_from_data(
            f"""
            window, .kuhaku {{ background: {t['bg']}; }}
            .title {{ font-family: 'Inter Variable', Inter, sans-serif; font-size: 14px; font-weight: 300;
                      color: {t['muted']}; }}
            .accent {{ background: {t['accent']}; min-width: 2px; }}
            .rule {{ background: alpha({t['fg']}, 0.07); min-height: 1px; }}
            button.quiet {{ background: none; border: none; box-shadow: none; color: {t['muted']};
                            font-family: 'Inter Variable', Inter, sans-serif; font-size: 18px; font-weight: 300;
                            min-width: 32px; min-height: 32px; padding: 0; border-radius: 999px;
                            transition: background 200ms, color 200ms; }}
            button.quiet:hover {{ color: {t['fg']}; background: alpha({t['fg']}, 0.08); }}
        """.encode()
        )
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.get_style_context().add_class("kuhaku")

        bar = Gtk.EventBox()
        bar.get_style_context().add_class("bar")
        row = Gtk.Box(spacing=14)
        row.set_margin_start(32)
        row.set_margin_end(28)
        row.set_size_request(-1, 52)
        line = Gtk.Box()
        line.get_style_context().add_class("accent")
        line.set_size_request(2, 20)
        line.set_valign(Gtk.Align.CENTER)
        self.title = Gtk.Label(label="Terminal", xalign=0)
        self.title.get_style_context().add_class("title")
        self.title.set_ellipsize(Pango.EllipsizeMode.END)
        row.pack_start(line, False, False, 0)
        row.pack_start(self.title, True, True, 0)
        # Kansei (管制) lives under the Terminal: the whole machine, live.
        k = Gtk.Button(label="Kansei 管制")
        k.set_tooltip_text("System control: processor, memory, programs, network, security")
        k.get_style_context().add_class("quiet")
        k.set_relief(Gtk.ReliefStyle.NONE)
        k.set_can_focus(False)
        k.connect(
            "clicked",
            lambda *_: __import__("subprocess").Popen(
                ["python3", str(Path(__file__).resolve().parent / "kansei.py")], start_new_session=True
            ),
        )
        row.pack_start(k, False, False, 0)
        for symbol, tip, action in (
            ("–", "Minimise", lambda *_: self.win.iconify()),
            ("×", "Close", lambda *_: self.win.destroy()),
        ):
            b = Gtk.Button(label=symbol)
            b.set_tooltip_text(tip)
            b.get_style_context().add_class("quiet")
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_can_focus(False)
            b.connect("clicked", action)
            row.pack_start(b, False, False, 0)
        bar.add(row)
        rule = Gtk.Box()
        rule.get_style_context().add_class("rule")

        self.term = Vte.Terminal()
        self.term.set_font(Pango.FontDescription.from_string("JetBrains Mono 11"))
        self.term.set_colors(rgba(t["fg"]), rgba(t["bg"]), [rgba(c) for c in t["palette"]])
        self.term.set_color_cursor(rgba(t["accent"]))
        self.term.set_color_cursor_foreground(rgba(t["bg"]))
        self.term.set_color_highlight(rgba(t["select"]))
        self.term.set_color_highlight_foreground(rgba(t["fg"]))
        self.term.set_cursor_shape(Vte.CursorShape.IBEAM)
        self.term.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
        self.term.set_scrollback_lines(20000)
        self.term.set_mouse_autohide(True)
        self.term.set_allow_hyperlink(True)
        self.term.set_cell_height_scale(1.2)
        self.term.connect("window-title-changed", self.on_title)
        self.term.connect("child-exited", lambda *_: self.win.destroy())

        pad = Gtk.Box()
        pad.get_style_context().add_class("kuhaku")
        pad.set_margin_start(40)
        pad.set_margin_end(40)
        pad.set_margin_top(20)
        pad.set_margin_bottom(16)
        pad.pack_start(self.term, True, True, 0)

        box.pack_start(bar, False, False, 0)
        box.pack_start(rule, False, False, 0)
        box.pack_start(pad, True, True, 0)
        self.win.add(box)

        shell = os.environ.get("SHELL", "/bin/bash")
        argv = [shell, "-l"] if not command else [shell, "-lc", f"{command}; exec {shell} -l"]
        env = [f"{k}={v}" for k, v in os.environ.items() if k not in ("COLUMNS", "LINES")]
        env += ["TERM=xterm-256color", "COLORTERM=truecolor"]
        self.term.spawn_async(
            Vte.PtyFlags.DEFAULT, str(HOME), argv, env, GLib.SpawnFlags.SEARCH_PATH, None, None, -1, None, None
        )
        self.win.show_all()
        self.term.grab_focus()

    def on_title(self, term):
        title = term.get_window_title() or "Terminal"
        self.title.set_text(title)
        self.win.set_title(title)

    def on_key(self, _w, ev):
        cmd = ev.state & Gdk.ModifierType.CONTROL_MASK  # the Mac Cmd key here
        ctrl = ev.state & Gdk.ModifierType.SUPER_MASK  # the physical Ctrl key here
        shift = ev.state & Gdk.ModifierType.SHIFT_MASK
        k = Gdk.keyval_name(Gdk.keyval_to_lower(ev.keyval)) or ""
        if cmd and not ctrl:
            if k == "c" and self.term.get_has_selection():
                self.term.copy_clipboard_format(Vte.Format.TEXT)
                return True
            if k == "v":
                self.term.paste_clipboard()
                return True
            if k == "a":
                self.term.select_all()
                return True
            if k == "k":
                self.term.reset(True, True)
                return True
            if k in ("plus", "equal"):
                self.term.set_font_scale(min(3, self.term.get_font_scale() + 0.1))
                return True
            if k == "minus":
                self.term.set_font_scale(max(0.5, self.term.get_font_scale() - 0.1))
                return True
            if k == "0":
                self.term.set_font_scale(1)
                return True
            if k == "n" or k == "t":
                os.spawnlp(os.P_NOWAIT, sys.executable, sys.executable, os.path.abspath(__file__))
                return True
            if k == "w" or k == "q":
                self.win.destroy()
                return True
            if k == "c":
                return False  # nothing selected: plain Ctrl+C reaches the shell
            if shift:
                return False
        if ctrl and len(k) == 1 and k.isalpha() or (ctrl and k in ("bracketleft", "backslash", "bracketright")):
            # Physical Ctrl+letter: send the control character to the shell.
            ch = {"bracketleft": "[", "backslash": "\\", "bracketright": "]"}.get(k, k)
            self.term.feed_child(bytes([ord(ch.upper()) & 0x1F]))
            return True
        return False


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    args = sys.argv[1:]
    command = args[args.index("-e") + 1] if "-e" in args and args.index("-e") + 1 < len(args) else None
    Terminal(command)
    Gtk.main()
