"""The pen anywhere: a clear sheet over the screen while the pen is near; what is written becomes type."""

import math
import os
import sys
import threading
import time
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from tablet_base import NOTES_FRONT, THEMES, current_theme, draw_ink, ease, is_finger, log, pressure, recognize
from tablet_keyboard import text_at

# --- the pen anywhere --------------------------------------------------------------------


def notes_in_front():
    try:
        pid = int(NOTES_FRONT.read_text())
        return Path(f"/proc/{pid}").exists()
    except (OSError, ValueError):
        return False


class Ink:
    """A clear sheet over the whole screen while the pen is near: what is written on it becomes type."""

    def __init__(self, app):
        self.app = app
        self.strokes = []
        self.cur = None
        self.state = "off"  # off, ready (shown), writing, reading, placing
        self.hide_id = self.recog_id = self.hold_id = None
        self.suspended_until = 0.0
        self.held = False
        self.result = None
        self.anim_t0 = 0.0
        self.tick_id = None
        self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.win.set_app_paintable(True)
        self.win.set_accept_focus(False)
        self.win.set_keep_above(True)
        visual = self.win.get_screen().get_rgba_visual()
        if visual:
            self.win.set_visual(visual)
        self.win.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.TOUCH_MASK
        )
        self.win.connect("draw", self.draw)
        self.win.connect("touch-event", self.on_touch)
        self.fingers = {}
        GLib.timeout_add(1000, self.watchdog)
        self.win.connect("button-press-event", self.on_press)
        self.win.connect("motion-notify-event", self.on_motion)
        self.win.connect("button-release-event", self.on_release)
        self.win.connect("realize", self.on_realize)
        self.fit()
        self.win.get_screen().connect("size-changed", lambda *_: self.fit())

    def on_realize(self, *_):
        gw = self.win.get_window()
        cur = Gdk.Cursor.new_from_name(gw.get_display(), "pencil") or Gdk.Cursor.new_from_name(
            gw.get_display(), "crosshair"
        )
        if cur:
            gw.set_cursor(cur)

    def fit(self):
        s = self.win.get_screen()
        self.W, self.H = s.get_width(), s.get_height()
        self.win.move(0, 0)
        self.win.resize(self.W, self.H)

    def shape(self):
        region = cairo.Region(cairo.RectangleInt(0, 0, self.W, self.H))
        kb = self.app.keyboard.body_rect()
        if kb:
            region.subtract(cairo.RectangleInt(*[int(v) for v in kb]))
        self.win.input_shape_combine_region(region)

    # --- the pen comes and goes ------------------------------------------------------------
    def pen(self, what):
        now = time.monotonic()
        if self.state == "off" and notes_in_front():
            return False
        if what == "up" and self.held:
            self.held = False
            self.suspended_until = now + 0.8
            return False
        if not self.app.settings["pen_writing"] or now < self.suspended_until or self.held:
            if now < self.suspended_until and what == "near":
                self.suspended_until = max(self.suspended_until, now + 0.6)  # hovering on as a pointer
            return False
        if os.environ.get("KT_DEBUG") and self.state == "off":
            print("pen near", file=sys.stderr, flush=True)
        if self.state == "off":
            self.theme = THEMES[current_theme()]
            self.fit()
            self.shape()
            self.win.show()
            self.state = "ready"
        self.keep()
        return False

    def keep(self):
        if self.hide_id:
            GLib.source_remove(self.hide_id)
        self.hide_id = GLib.timeout_add(1500, self.idle_hide)

    def watchdog(self):
        """Never let the clear sheet stay over the screen without the pen: a lost pen-up
        (or anything else odd) would otherwise leave every tap and click caught on it."""
        if self.state != "off" and not self.app.pen_recent(4) and self.state not in ("reading", "placing"):
            log("ink: pen gone, sheet cleared", self.state)
            self.cancel()
        if self.state in ("reading", "placing") and not self.app.pen_recent(15):
            self.cancel()
        return True

    def on_touch(self, _w, ev):
        seq = ev.get_event_sequence()
        t = ev.type
        if t == Gdk.EventType.TOUCH_BEGIN:
            # while the pen is near (or there is writing on the sheet) every touch is the hand
            palm = self.app.pen_recent(1.2) or self.strokes or self.cur is not None
            self.fingers[seq] = None if palm else (ev.x_root, ev.y_root, time.monotonic())
        elif t == Gdk.EventType.TOUCH_UPDATE:
            f = self.fingers.get(seq)
            if f and abs(ev.x_root - f[0]) + abs(ev.y_root - f[1]) > 18:
                # a finger scrolling or dragging: get out of its way for the next touch
                self.fingers[seq] = None
                if not self.strokes:
                    self.off()
        elif t in (Gdk.EventType.TOUCH_END, Gdk.EventType.TOUCH_CANCEL):
            f = self.fingers.pop(seq, None)
            if f and t == Gdk.EventType.TOUCH_END and not self.strokes and time.monotonic() - f[2] < 0.5:
                self.pass_tap(ev.x_root, ev.y_root)
        return True

    def idle_hide(self):
        self.hide_id = None
        if self.state == "ready" and not self.strokes and not self.cur:
            self.off()
        return False

    def off(self):
        self.win.hide()
        self.state = "off"
        Gdk.Display.get_default().sync()

    def cancel(self):
        for attr in ("recog_id", "hold_id"):
            if getattr(self, attr):
                GLib.source_remove(getattr(self, attr))
                setattr(self, attr, None)
        self.strokes, self.cur, self.result = [], None, None
        self.off()

    def pass_tap(self, x, y):
        """A tap goes through to whatever is under it."""
        self.off()
        self.suspended_until = time.monotonic() + 0.35
        self.app.typer.put("click", x, y)

    # --- writing ---------------------------------------------------------------------------
    def on_press(self, _w, ev):
        if ev.button != 1:
            return True
        if is_finger(ev):
            self.finger = (ev.x_root, ev.y_root, time.monotonic())
            return True
        self.finger = None
        if self.recog_id:
            GLib.source_remove(self.recog_id)
            self.recog_id = None
        if self.state in ("reading", "placing"):
            return True
        if not self.strokes:
            self.t0 = time.monotonic()
            self.hold_id = GLib.timeout_add(480, self.on_hold, ev.x_root, ev.y_root)
        self.state = "writing"
        self.cur = [(ev.x_root, ev.y_root, (time.monotonic() - self.t0) * 1000, pressure(ev))]
        self.win.queue_draw()
        return True

    def on_motion(self, _w, ev):
        self.keep()
        if self.cur is None or is_finger(ev):
            return True
        last = self.cur[-1]
        if abs(ev.x_root - last[0]) + abs(ev.y_root - last[1]) < 1.2:
            return True
        self.cur.append((ev.x_root, ev.y_root, (time.monotonic() - self.t0) * 1000, pressure(ev)))
        if self.hold_id and abs(ev.x_root - self.cur[0][0]) + abs(ev.y_root - self.cur[0][1]) > 9:
            GLib.source_remove(self.hold_id)
            self.hold_id = None
        x0, y0 = min(last[0], ev.x_root) - 12, min(last[1], ev.y_root) - 12
        self.win.queue_draw_area(
            int(x0), int(y0), int(abs(last[0] - ev.x_root) + 24), int(abs(last[1] - ev.y_root) + 24)
        )
        return True

    def on_hold(self, x, y):
        """The pen held still: it is a pointer until it is lifted away."""
        self.hold_id = None
        if self.cur and len(self.strokes) == 0:
            self.cur = None
            self.held = True
            self.off()
        return False

    def on_release(self, _w, ev):
        if ev.button != 1:
            return True
        if is_finger(ev):
            f = getattr(self, "finger", None)
            if f and not self.strokes and abs(ev.x_root - f[0]) + abs(ev.y_root - f[1]) < 14:
                self.pass_tap(ev.x_root, ev.y_root)
            else:
                self.off() if not self.strokes else None
            return True
        if self.hold_id:
            GLib.source_remove(self.hold_id)
            self.hold_id = None
        if self.cur is None:
            return True
        s = self.cur
        self.cur = None
        span = max(abs(p[0] - s[0][0]) + abs(p[1] - s[0][1]) for p in s)
        quick = (s[-1][2] - s[0][2]) < 350
        if not self.strokes and span < 10 and quick:
            self.state = "ready"
            self.pass_tap(s[0][0], s[0][1])
            return True
        if not self.strokes and len(s) > 3:
            # one long straight line is a gesture, not writing: up or down scrolls what is under it
            dx, dy = s[-1][0] - s[0][0], s[-1][1] - s[0][1]
            dist = math.hypot(dx, dy)
            length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(s, s[1:], strict=False))
            if dist > 0 and length / dist < 1.18:
                if abs(dy) > 140 and abs(dy) > 2.5 * abs(dx):
                    self.off()
                    self.suspended_until = time.monotonic() + 0.3
                    self.app.typer.put("scroll", s[0][0], s[0][1], 4 if dy > 0 else 5, max(1, int(abs(dy) / 40)))
                    return True
                if abs(dx) > 260 and abs(dx) > 4 * abs(dy):
                    self.state = "ready"
                    self.win.queue_draw()
                    return True
        self.strokes.append(s)
        self.recog_id = GLib.timeout_add(820, self.read)
        return True

    def bbox(self):
        pts = [p for s in self.strokes for p in s]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def read(self):
        self.recog_id = None
        if not self.strokes:
            return False
        self.state = "reading"
        self.anim_t0 = time.monotonic()
        self.start_tick()
        strokes = self.strokes
        x0, y0, x1, y1 = self.bbox()
        lang = self.app.settings["language"]

        def work():
            try:
                cands = recognize(
                    [[(p[0] - x0 + 20, p[1] - y0 + 20, p[2]) for p in s] for s in strokes],
                    x1 - x0 + 40,
                    y1 - y0 + 40,
                    lang,
                )
            except Exception as e:
                print("kuhaku-tablet: reading the ink:", e, file=sys.stderr)
                cands = None
            GLib.idle_add(self.place, strokes, cands)

        threading.Thread(target=work, daemon=True).start()
        self.win.queue_draw()
        return False

    def place(self, strokes, cands):
        log("ink read", len(strokes), "strokes ->", (cands or ["(nothing)" if cands is not None else "(offline)"])[0])
        if os.environ.get("KT_DEBUG"):
            print("ink read", (cands or [])[:5], file=sys.stderr, flush=True)
        if strokes is not self.strokes:
            return False
        x0, y0, x1, y1 = self.bbox()
        self.state = "placing"
        self.anim_t0 = time.monotonic()
        self.result = (cands[0].strip() if cands else None, x0, y0, x1, y1, cands is None)
        self.start_tick()
        GLib.timeout_add(520 if cands else 900, self.finish)
        return False

    def finish(self):
        res = self.result
        self.strokes, self.result = [], None
        self.state = "ready"
        self.off()
        if res and res[0]:
            text, x0, y0, x1, y1, _ = res
            self.suspended_until = time.monotonic() + 0.5
            self.app.typer.put("write", x0 + min(14, (x1 - x0) / 4), (y0 + y1) / 2, text)
        return False

    def start_tick(self):
        if not self.tick_id:
            self.tick_id = GLib.timeout_add(16, self.tick)

    def tick(self):
        if self.strokes:
            x0, y0, x1, y1 = self.bbox()
            self.win.queue_draw_area(
                int(x0) - 40, int(y0) - 40, int(x1 - x0) + 80 + int(max(0, (y1 - y0) * 0.62 * 12)), int(y1 - y0) + 120
            )
        else:
            self.win.queue_draw()
        if self.state in ("reading", "placing"):
            return True
        self.tick_id = None
        return False

    def draw(self, _w, cr):
        t = getattr(self, "theme", THEMES["day"])
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        width = 3.0
        strokes = self.strokes + ([self.cur] if self.cur else [])
        if self.state == "reading":
            # the ink breathes in the accent while it is being read
            p = 0.5 + 0.5 * math.sin((time.monotonic() - self.anim_t0) * 6)
            draw_ink(cr, strokes, t["fg"], 0.85 - 0.25 * p, width)
            draw_ink(cr, strokes, t["accent"], 0.15 + 0.35 * p, width)
        elif self.state == "placing" and self.result:
            text, x0, y0, x1, y1, offline = self.result
            p = ease((time.monotonic() - self.anim_t0) / 0.42)
            draw_ink(cr, strokes, t["accent"] if text else t["muted"], 0.8 * (1 - p), width * (1 + 0.6 * p))
            if text:
                size = max(16, min(64, (y1 - y0) * 0.62))
                layout_x = x0
                cr.save()
                cr.translate(0, (1 - p) * 8)
                text_at(
                    cr, text, size, t["fg"], layout_x, (y0 + y1) / 2, weight=Pango.Weight.LIGHT, alpha=p, align="left"
                )
                cr.restore()
            elif offline:
                text_at(
                    cr, "No connection to read handwriting", 15, t["muted"], (x0 + x1) / 2, y1 + 24, alpha=min(1, p * 2)
                )
        else:
            draw_ink(cr, strokes, t["fg"], 0.88, width)
        return True
