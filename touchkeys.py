"""Touch keys inside Kūhaku's own password screens (the lock and the password window).

Those screens hold the keyboard and the mouse, so the touch keyboard (kuhaku-tablet.py)
cannot reach them. When the computer is folded into a tablet they show these keys
themselves, typing straight into their password line.
"""

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

RUN = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
MODE_FILE = RUN / "kuhaku-tablet-mode"
FRONT = RUN / "kuhaku-password-front"  # tells the touch keyboard to step away meanwhile


def front(on):
    try:
        if on:
            FRONT.write_text(str(os.getpid()))
        elif FRONT.exists() and FRONT.read_text().strip() == str(os.getpid()):
            FRONT.unlink()
    except OSError:
        pass


ROWS = {
    "abc": ["qwertyuiop", "asdfghjkl", "⇧zxcvbnm⌫", "#⎵⏎"],
    "123": ["1234567890", "-/:;()€&@", "=.,?!'\"_⌫", "a⎵⏎"],
}


def tablet_mode():
    try:
        return MODE_FILE.read_text().strip() == "1"
    except OSError:
        return False


def css(fg, muted, accent):
    return f"""
        #touchkeys button {{ background: alpha({fg}, .055); border: none; box-shadow: none; border-radius: 12px;
                  color: {fg}; font-size: 22px; font-weight: 300; min-height: 58px; min-width: 40px; padding: 0; }}
        #touchkeys button:active, #touchkeys button.on {{ background: alpha({accent}, .16); color: {accent}; }}
        #touchkeys button.fn {{ background: alpha({fg}, .03); color: {muted}; font-size: 17px; }}
    """


class TouchKeys(Gtk.Box):
    def __init__(self, entry, on_enter):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, name="touchkeys")
        self.entry, self.on_enter = entry, on_enter
        self.layer, self.shift = "abc", False
        self.build()
        GLib.idle_add(lambda: self.follow_mode() and False)
        GLib.timeout_add(800, self.follow_mode)

    def build(self):
        for c in self.get_children():
            self.remove(c)
        for r, row in enumerate(ROWS[self.layer]):
            line = Gtk.Box(spacing=8, homogeneous=False)
            if r == 1:
                line.set_margin_start(34)
                line.set_margin_end(34)
            for ch in row:
                label = {"⎵": "space", "⏎": "enter", "#": "123"}.get(ch, ch.upper() if self.shift else ch)
                if ch == "a" and self.layer == "123":
                    label = "abc"
                b = Gtk.Button(label=label)
                b.set_can_focus(False)  # the password line keeps the focus
                if ch in "⇧⌫⎵⏎#a" and (len(row) <= 4 or ch in "⇧⌫"):
                    b.get_style_context().add_class("fn")
                if ch == "⇧" and self.shift:
                    b.get_style_context().add_class("on")
                b.connect("clicked", self.press, ch)
                line.pack_start(b, True, True, 0)
                if ch == "⎵":
                    line.set_child_packing(b, True, True, 0, Gtk.PackType.START)
                    b.set_size_request(420, -1)
            self.pack_start(line, False, False, 0)
        self.show_all()

    def press(self, _b, ch):
        e = self.entry
        if ch == "⌫":
            text = e.get_text()
            if text:
                e.set_text(text[:-1])
                e.set_position(-1)
        elif ch == "⏎":
            self.on_enter(e)
        elif ch == "⇧":
            self.shift = not self.shift
            self.build()
        elif ch in "#a" and len(ch) == 1 and (ch == "#" or self.layer == "123"):
            self.layer = "123" if ch == "#" else "abc"
            self.build()
        else:
            t = " " if ch == "⎵" else (ch.upper() if self.shift else ch)
            pos = len(e.get_text())
            e.insert_text(t, pos)
            e.set_position(-1)
            if self.shift:
                self.shift = False
                self.build()

    def follow_mode(self):
        self.set_visible(tablet_mode())
        return True
