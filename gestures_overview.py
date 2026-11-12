"""The overview: swipe up with three or four fingers and every open screen lies as a card along
the bottom. Shown by the Deck in kuhaku-gestures.py."""

import subprocess

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

from gestures_common import (
    ACCENT,
    DOWN,
    LEFT,
    NAV_STEP,
    RIGHT,
    UP,
    UP_COMMIT,
    UP_DISTANCE,
    app_for,
    describe,
    icon_surface,
    night,
    out,
    paper,
    rounded,
    showing_desktop,
    ui_scale,
)


class Overview:
    """Swipe up with three or four fingers: every open screen becomes a card along
    the bottom, Kūhaku first, then the apps in the order they were opened. Each
    card carries its app's icon and name; a small dot marks the one you came from.
    Swipe sideways, point, or use the arrow keys to move between them; click,
    press Enter or swipe up again to go to one. The cross on a card (or Delete)
    closes that app. Swipe down, press Esc or click the empty screen to stay."""

    def __init__(self, deck):
        self.deck = deck
        self.visible = False  # on screen at all
        self.open = False  # fully out and taking input
        self.following = False  # fingers on the pad
        self.mode = None  # what the current gesture does: "open" or "nav"
        self.p = self.target = 0.0
        self.q = 0.0  # the chosen card growing to the whole screen
        self.picking = None
        self.anim = None
        self.cards = []
        self.sel = 0
        self.close_hot = False
        self.bg = self.soft = None
        self.origin = "kuhaku"
        self.left_closed = False  # the app you came from was closed here
        self.grabbed = False
        self.s = ui_scale()
        self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.win.set_app_paintable(True)
        self.win.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.KEY_PRESS_MASK
        )
        self.win.connect("draw", self.draw)
        self.win.connect("button-press-event", self.on_click)
        self.win.connect("motion-notify-event", self.on_motion)
        self.win.connect("key-press-event", self.on_key)
        self.refit()

    def toggle(self):
        """The red dot (rundot.overview) opens the cards without a swipe."""
        if not self.visible:
            self.show()
            self.target = 1.0
            self.animate()
        elif self.picking is None:
            self.cancel()
        return True

    def refit(self):
        self.w, self.h = self.deck.w, self.deck.h
        self.win.move(0, 0)
        self.win.resize(self.w, self.h)

    # --- gestures --------------------------------------------------------
    def begin(self, direction, pct):
        self.mode = None
        if not self.visible:
            if direction == UP:
                self.show()
                self.mode = "open"
                self.following = True
                self.update(pct)
            return
        if self.picking is not None:
            return
        if direction in (LEFT, RIGHT):
            self.mode, self.nav_dir, self.nav_steps = "nav", (1 if direction == LEFT else -1), 0
            self.update(pct)
        elif direction == DOWN:
            self.cancel()
        elif direction == UP:
            if self.open:
                self.pick(self.sel)
            else:  # still sliding away: catch it again
                self.mode = "open"
                self.following = True
                self.update(pct)

    def update(self, pct):
        if self.mode == "open":
            self.target = max(0.0, min(1.0, pct / UP_DISTANCE))
            self.animate()
        elif self.mode == "nav":
            steps = 1 + int(pct // NAV_STEP)
            while self.nav_steps < steps:
                self.nav_steps += 1
                self.select(self.sel + self.nav_dir)

    def end(self):
        if self.mode == "open":
            self.following = False
            self.target = 1.0 if self.target >= UP_COMMIT else 0.0
            self.animate()
        self.mode = None

    # --- showing ---------------------------------------------------------
    def show(self):
        d = self.deck
        root = {}
        for line in out(
            "xprop", "-root", "_NET_CLIENT_LIST", "_NET_ACTIVE_WINDOW", "_NET_SHOWING_DESKTOP"
        ).splitlines():
            key, sep, val = line.partition("#") if "#" in line else line.partition(" = ")
            root[key.split("(")[0].strip()] = val
        clients = [w.strip() for w in root.get("_NET_CLIENT_LIST", "").split(",") if w.strip().startswith("0x")]
        active = root.get("_NET_ACTIVE_WINDOW", "").split(",")[0].strip()
        info = describe(clients)
        row, home = [], None
        for w in clients:
            t, state = info[w].get("_NET_WM_WINDOW_TYPE", ""), info[w].get("_NET_WM_STATE", "")
            if "DESKTOP" in t:
                home = home or w
            elif "DOCK" not in t and "SKIP_TASKBAR" not in state:
                row.append(w)
        hidden = lambda w: "_NET_WM_STATE_HIDDEN" in info[w].get("_NET_WM_STATE", "")
        self.origin = active if active in row and not hidden(active) else "kuhaku"
        self.left_closed = False
        self.bg = d.snapshot()
        if self.bg:
            d.shots[self.origin] = self.bg
        self.soft = None
        self.cards = [{"key": "kuhaku", "xid": home, "name": "Kūhaku", "icon": None, "hidden": False}]
        for w in row:
            app = app_for(info[w]["classes"])
            self.cards.append(
                {
                    "key": w,
                    "xid": w,
                    "name": app[0] if app else (info[w]["name"] or "App"),
                    "icon": app[1] if app else None,
                    "hidden": hidden(w),
                }
            )
        for c in self.cards:
            c["shot"], c["thumb"] = d.shots.get(c["key"]), None
        self.sel = [c["key"] for c in self.cards].index(self.origin)
        self.s = ui_scale()
        self.measure()
        for c, x in zip(self.cards, self.places(), strict=False):
            c["x"] = x
        self.p = self.target = self.q = 0.0
        self.picking = None
        self.open = self.close_hot = False
        self.visible = True
        self.win.show_all()
        self.win.get_window().raise_()
        GLib.idle_add(
            self.prepare, [self.cards[self.sel], "soft"] + [c for i, c in enumerate(self.cards) if i != self.sel]
        )

    def prepare(self, todo):
        """In idle moments: the softened background, then fresh pictures of the
        other screens. Hidden windows keep the last picture taken of them."""
        if not self.visible or not todo:
            return False
        card = todo.pop(0)
        if card == "soft":
            self.soft = self.soften(self.bg) if self.bg else None
        else:
            if card["key"] != self.origin and card["xid"] and not card["hidden"]:
                Gdk.error_trap_push()
                img = self.deck.snapshot(card["xid"])
                Gdk.flush()
                if Gdk.error_trap_pop() == 0 and img:
                    card["shot"] = self.deck.shots[card["key"]] = img
                    card["thumb"] = None
            self.thumb(card)
        self.win.queue_draw()
        return bool(todo)

    def soften(self, surf):
        """The screen behind the cards: blurred (shrunk and grown again) and dimmed."""

        def shrink(src, k):
            sw, sh = src.get_width(), src.get_height()
            dst = cairo.ImageSurface(cairo.FORMAT_RGB24, max(1, sw // k), max(1, sh // k))
            c = cairo.Context(dst)
            c.scale(dst.get_width() / sw, dst.get_height() / sh)
            c.set_source_surface(src, 0, 0)
            c.get_source().set_filter(cairo.FILTER_GOOD)
            c.get_source().set_extend(cairo.EXTEND_PAD)
            c.paint()
            return dst

        small = shrink(shrink(surf, 4), 6)
        soft = cairo.ImageSurface(cairo.FORMAT_RGB24, self.w, self.h)
        c = cairo.Context(soft)
        c.scale(self.w / small.get_width(), self.h / small.get_height())
        c.set_source_surface(small, 0, 0)
        c.get_source().set_filter(cairo.FILTER_BILINEAR)
        c.get_source().set_extend(cairo.EXTEND_PAD)
        c.paint()
        c.identity_matrix()
        c.set_source_rgba(0, 0, 0, 0.5)
        c.paint()
        return soft

    def measure(self):
        """Card size, fixed while the overview is open (closing apps only re-centres)."""
        s, n = self.s, max(1, len(self.cards))
        self.gap, margin = 36 * s, 72 * s
        th = self.h * 0.2
        tw = th * self.w / self.h
        if n * tw + (n - 1) * self.gap > self.w - 2 * margin:
            tw = (self.w - 2 * margin - (n - 1) * self.gap) / n
            th = tw * self.h / self.w
        self.tw, self.th = int(tw), int(th)
        self.top = round(self.h - self.th - 96 * s)

    def places(self):
        n = len(self.cards)
        x = (self.w - (n * self.tw + (n - 1) * self.gap)) / 2
        return [x + i * (self.tw + self.gap) for i in range(n)]

    def thumb(self, card):
        tw, th = self.tw, self.th
        if card["thumb"] and card["thumb"].get_width() == tw:
            return card["thumb"]
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, tw, th)
        cr = cairo.Context(surf)
        rounded(cr, 0, 0, tw, th, 10 * self.s)
        cr.clip()
        shot = card["shot"]
        if shot:
            cr.save()
            cr.scale(tw / shot.get_width(), th / shot.get_height())
            cr.set_source_surface(shot, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_GOOD)
            cr.paint()
            cr.restore()
        else:  # not seen yet: its icon on paper
            cr.set_source_rgb(*paper())
            cr.paint()
            size = int(min(56 * self.s, th * 0.4))
            icon = icon_surface(card["icon"], size)
            if icon:
                cr.set_source_surface(icon, (tw - size) / 2, (th - size) / 2)
                cr.paint()
            else:
                self.label(cr, card, tw / 2, th / 2 - 8 * self.s, tw, (0.5,) * 3, with_icon=False)
        card["thumb"] = surf
        return surf

    def label(self, cr, card, cx, y, width, color, with_icon=True):
        """Icon and name, centred on cx. Kūhaku's icon is its red line."""
        s = self.s
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription.from_string(f"Inter {10.5 * s:.1f}")
        fd.set_weight(Pango.Weight.NORMAL)
        layout.set_font_description(fd)
        layout.set_text(card["name"], -1)
        isz = round(18 * s) if with_icon else 0
        igap = 8 * s if with_icon else 0
        layout.set_width(int(max(10, width - isz - igap) * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        lw, lh = layout.get_pixel_size()
        x = round(cx - (isz + igap + lw) / 2)
        if with_icon:
            if card["key"] == "kuhaku":
                cr.set_source_rgb(*ACCENT["night" if night() else "day"])
                cr.rectangle(x + 2 * s, round(y + lh / 2 - s), isz - 4 * s, max(1, round(2 * s)))
                cr.fill()
            else:
                icon = icon_surface(card["icon"], isz)
                if icon:
                    cr.set_source_surface(icon, x, round(y + (lh - isz) / 2))
                    cr.paint()
        cr.set_source_rgb(*color)
        cr.move_to(x + isz + igap, y)
        PangoCairo.show_layout(cr, layout)
        return lh

    # --- choosing --------------------------------------------------------
    def select(self, i):
        i = max(0, min(len(self.cards) - 1, i))
        if i != self.sel:
            self.sel = i
            self.close_hot = False
            self.win.queue_draw()

    def hit(self, x, y):
        pad = self.gap / 2
        for i, c in enumerate(self.cards):
            if (
                c["x"] - pad <= x <= c["x"] + self.tw + pad
                and self.top - 16 * self.s <= y <= self.top + self.th + 56 * self.s
            ):
                return i
        return None

    def on_close_button(self, x, y):
        if not self.cards or self.cards[self.sel]["key"] == "kuhaku":
            return False
        cx, cy = self.cards[self.sel]["x"], self.top
        return (x - cx) ** 2 + (y - cy) ** 2 <= (16 * self.s) ** 2

    def on_motion(self, _w, ev):
        if not self.open:
            return
        hot = self.on_close_button(ev.x, ev.y)
        if not hot:
            i = self.hit(ev.x, ev.y)
            if i is not None:
                self.select(i)
        if hot != self.close_hot:
            self.close_hot = hot
            self.win.queue_draw()

    def on_click(self, _w, ev):
        if not self.open:
            return True
        if self.on_close_button(ev.x, ev.y):
            self.close(self.sel)
            return True
        i = self.hit(ev.x, ev.y)
        if i is None:
            self.cancel()
        else:
            self.pick(i)
        return True

    def on_key(self, _w, ev):
        if not self.open:
            return True
        k = Gdk.keyval_name(ev.keyval)
        if k in ("Right", "Tab", "l"):
            self.select(self.sel + 1)
        elif k in ("Left", "ISO_Left_Tab", "h"):
            self.select(self.sel - 1)
        elif k == "Home":
            self.select(0)
        elif k == "End":
            self.select(len(self.cards) - 1)
        elif k in ("Return", "KP_Enter", "space", "Up"):
            self.pick(self.sel)
        elif k in ("Delete", "BackSpace", "w", "q"):
            self.close(self.sel)
        elif k in ("Escape", "Down"):
            self.cancel()
        return True

    def close(self, i):
        card = self.cards[i]
        if card["key"] == "kuhaku":
            return
        subprocess.Popen(["wmctrl", "-i", "-c", card["key"]])
        self.deck.shots.pop(card["key"], None)
        del self.cards[i]
        if card["key"] == self.origin:
            # Behind the cards is now Kūhaku, and staying means going home.
            self.origin, self.left_closed = "kuhaku", True
            home = self.cards[0]["shot"]
            if home:
                self.bg, self.soft = home, self.soften(home)
        self.sel = min(i, len(self.cards) - 1)
        self.close_hot = False
        self.animate()

    def pick(self, i):
        if not 0 <= i < len(self.cards):
            return
        self.sel = self.picking = i
        self.open = False
        self.ungrab()
        self.animate()

    def cancel(self):
        self.open = False
        self.following = False
        self.ungrab()
        self.target = 0.0
        self.animate()

    def arrive(self):
        if self.picking is None:
            return
        key = self.cards[self.picking]["key"]
        if key != self.origin or self.left_closed or (key == "kuhaku") != showing_desktop():
            self.deck.go(key, None)
            self.deck.settle(key, 4)
        GLib.timeout_add(120, self.hide)
        GLib.timeout_add(900, self.deck.remember)

    def hide(self):
        if self.left_closed and self.picking is None:
            self.deck.go("kuhaku", None)
        self.ungrab()
        self.win.hide()
        self.visible = self.open = self.following = self.left_closed = False
        self.picking = self.mode = None
        self.p = self.target = self.q = 0.0
        self.bg = self.soft = None
        self.cards = []
        return False

    def grab(self):
        seat = Gdk.Display.get_default().get_default_seat()
        if (
            seat.grab(self.win.get_window(), Gdk.SeatCapabilities.KEYBOARD, False, None, None, None, None)
            == Gdk.GrabStatus.SUCCESS
        ):
            self.grabbed = True

    def ungrab(self):
        if self.grabbed:
            Gdk.Display.get_default().get_default_seat().ungrab()
            self.grabbed = False

    # --- motion ----------------------------------------------------------
    def animate(self):
        if not self.anim:
            self.anim = GLib.timeout_add(16, self.step)

    def step(self):
        moving = False
        rate = 0.5 if self.following else 0.34
        for name, goal in (("p", self.target), ("q", 1.0 if self.picking is not None else 0.0)):
            v = getattr(self, name)
            if abs(goal - v) < 0.004:
                setattr(self, name, goal)
            else:
                setattr(self, name, v + (goal - v) * rate)
                moving = True
        for c, x in zip(self.cards, self.places(), strict=False):
            if abs(x - c["x"]) < 0.5:
                c["x"] = x
            else:
                c["x"] += (x - c["x"]) * 0.3
                moving = True
        self.win.queue_draw()
        if moving or self.following:
            return True
        self.anim = None
        if self.picking is not None:
            self.arrive()
        elif self.p == 0.0:
            self.hide()
        elif self.p == 1.0 and not self.open:
            self.open = True
            self.grab()
        return False

    def draw(self, _w, cr):
        p, q, s = self.p, self.q, self.s
        if self.bg:
            cr.set_source_surface(self.bg, 0, 0)
        else:
            cr.set_source_rgb(*paper())
        cr.paint()
        veil = p * (1 - q)
        if self.soft:
            cr.set_source_surface(self.soft, 0, 0)
            cr.paint_with_alpha(veil)
        else:
            cr.set_source_rgba(0, 0, 0, 0.5 * veil)
            cr.paint()
        if not self.cards:
            return True
        accent = ACCENT["night" if night() else "day"]
        y = round(self.top + (1 - p) * (self.h - self.top + 80 * s))
        alpha = min(1.0, p * 1.3) * (1 - q)
        r = 10 * s
        for i, card in enumerate(self.cards):
            if i == self.picking:
                continue
            x = round(card["x"])
            chosen = i == self.sel and self.picking is None
            cr.push_group()
            # a soft shadow, the picture, a hairline edge
            for k in range(3):
                grow = (k + 1) * 3 * s
                rounded(cr, x - grow, y - grow + 4 * s, self.tw + 2 * grow, self.th + 2 * grow, r + grow)
                cr.set_source_rgba(0, 0, 0, 0.07)
                cr.fill()
            if card["thumb"]:
                cr.set_source_surface(card["thumb"], x, y)
                cr.paint()
            else:
                rounded(cr, x, y, self.tw, self.th, r)
                cr.set_source_rgb(*paper())
                cr.fill()
            rounded(cr, x + 0.5, y + 0.5, self.tw - 1, self.th - 1, r)
            cr.set_source_rgba(1, 1, 1, 0.14)
            cr.set_line_width(1)
            cr.stroke()
            if chosen:
                pad = 6 * s
                rounded(cr, x - pad, y - pad, self.tw + 2 * pad, self.th + 2 * pad, r + pad)
                cr.set_source_rgb(*accent)
                cr.set_line_width(2.5 * s)
                cr.stroke()
            lh = self.label(
                cr, card, x + self.tw / 2, y + self.th + 18 * s, self.tw, (1, 1, 1) if chosen else (0.82, 0.82, 0.82)
            )
            if card["key"] == self.origin:  # where you came from
                cr.new_path()
                cr.arc(x + self.tw / 2, y + self.th + 18 * s + lh + 10 * s, 2.5 * s, 0, 6.2832)
                cr.set_source_rgba(1, 1, 1, 0.75)
                cr.fill()
            if chosen and card["key"] != "kuhaku" and self.open:
                cr.new_path()
                cr.arc(x, y, 13 * s, 0, 6.2832)
                cr.set_source_rgb(*(accent if self.close_hot else (0.14, 0.14, 0.14)))
                cr.fill_preserve()
                cr.set_source_rgba(1, 1, 1, 0.3)
                cr.set_line_width(1)
                cr.stroke()
                k = 4.5 * s
                cr.set_source_rgb(1, 1, 1)
                cr.set_line_width(1.6 * s)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.move_to(x - k, y - k)
                cr.line_to(x + k, y + k)
                cr.move_to(x + k, y - k)
                cr.line_to(x - k, y + k)
                cr.stroke()
            cr.pop_group_to_source()
            cr.paint_with_alpha(alpha if chosen else alpha * 0.9)
        if self.picking is not None:
            card = self.cards[self.picking]
            e = 1 - (1 - q) ** 2
            x0 = card["x"]
            fx, fy = x0 * (1 - e), y * (1 - e)
            fw, fh = self.tw + (self.w - self.tw) * e, self.th + (self.h - self.th) * e
            cr.save()
            rounded(cr, fx, fy, fw, fh, r * (1 - e))
            cr.clip()
            shot = card["shot"]
            if shot:
                cr.translate(fx, fy)
                cr.scale(fw / shot.get_width(), fh / shot.get_height())
                cr.set_source_surface(shot, 0, 0)
                cr.get_source().set_filter(cairo.FILTER_GOOD if e < 0.98 else cairo.FILTER_NEAREST)
            else:
                cr.set_source_rgb(*paper())
            cr.paint()
            cr.restore()
        return True
