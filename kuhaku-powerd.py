#!/usr/bin/env python3
"""Kūhaku's power: the screen, sleep, the lid and the battery, in place of xfce4-power-manager.

  Screen off  after the minutes chosen in Settings without a touch (X's own DPMS), unless an
              app asks it to stay on (a video playing, through org.freedesktop.ScreenSaver).
  Sleep       after the minutes chosen without a touch, unless something holds it off: Claude
              working (keepawake.py, through org.freedesktop.PowerManagement.Inhibit), a video.
  The lid     Sleep (the system does it), Lock, or Screen off, as chosen. While Claude works the
              lid only turns the screen off; if it is still closed when the work ends, it sleeps.
  Power key   left to Kūhaku's menu (kuhaku-keys.py), never the system's instant power off.
  Battery     a gentle card at 20% (a good moment to plug in), a firmer one at 10%, and sleep
              before it runs out.
  Charging    held at 80% to keep the battery young (the firmware keeps the limit). It fills to
              100% when you will need it: shortly before you usually unplug (learned), before a
              trip in the calendar (a flight, a train, a hotel, days somewhere), and while you
              travel (plugged in on a network that is not one of your usual places). "Full once"
              fills until the next time you head off, "Always full" turns the care off.

The choices live under "power" in ~/.config/kuhaku/desktop.json; Settings writes them.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from gi.repository import Gio, GLib

from kuhaku_log import report  # faults go to the log instead of vanishing
from kuhaku_x11 import X, Xss, open_display

CONFIG = Path.home() / ".config" / "kuhaku" / "desktop.json"
LID_SAVED = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "kuhaku" / "lid" / "saved"
DEFAULTS = {"screen_off": 10, "sleep_after": 0, "lid": 1, "charge": 80, "full_once": 0}
# minutes, minutes (0 never), 1 sleep / 3 lock / 0 screen off, 80 care / 100 always full, fill once
NUDGE, LOW, CRITICAL = 20, 10, 4  # battery percent: a gentle card, a firm one, then sleep
CARE = (75, 80)  # charge from, charge to: kept between them while plugged in
FULL = (0, 100)  # the firmware's own: always fill
SUPPLY = Path("/sys/class/power_supply")
UNPLUGS = LID_SAVED.parent.parent / "battery" / "unplugs.json"  # when you headed off, to learn the habit
AWAY = 20 * 60  # on battery this long counts as heading off (not a cable moved)
AHEAD = 150 * 60  # fill this long before the usual time: 80 to 100% takes about an hour
PLACES = UNPLUGS.with_name("places.json")  # the networks you charge on, and on which days
WHY = UNPLUGS.with_name("why.json")  # why it fills now, for the battery panel
SENSE = 5 * 60  # how often the calendar and the network are looked at
BEFORE_TRIP = 18 * 3600  # a trip this soon: fill tonight for tomorrow
USUAL_DAYS = 5  # a network charged on, on five days of the last sixty, is one of your usual places
TRAVEL = re.compile(
    r"✈|🛫|🛬|🚆|🚄|🧳|\b(flight|flug|odlet|přílet|letadl\w*|letiště|airport|flughafen|aeropuerto|aéroport|"
    r"train|zug|vlak\w*|bahn|railjet|hotel|airbnb|trip|travel\w*|reise|cesta|výlet|dovolen\w*|"
    r"holiday|vacation|urlaub|conference|konferen\w*|offsite|off-site|retreat|summit|kongres\w*)\b",
    re.I,
)
NOT_AWAY = re.compile(r"https?://|\b(room|meeting|zasedač\w*|teams|zoom|meet\.google|webex|online|call)\b", re.I)
LOGIN1 = "org.freedesktop.login1"
UPOWER = "org.freedesktop.UPower"

PM_XML = """<node><interface name="org.freedesktop.PowerManagement.Inhibit">
  <method name="Inhibit"><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="out" type="u"/></method>
  <method name="UnInhibit"><arg direction="in" type="u"/></method>
  <method name="HasInhibit"><arg direction="out" type="b"/></method>
  <signal name="HasInhibitChanged"><arg type="b"/></signal>
</interface></node>"""
SS_XML = """<node><interface name="org.freedesktop.ScreenSaver">
  <method name="Inhibit"><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="out" type="u"/></method>
  <method name="UnInhibit"><arg direction="in" type="u"/></method>
  <method name="SimulateUserActivity"/>
  <method name="GetActive"><arg direction="out" type="b"/></method>
</interface></node>"""


# --- how long since the last touch (the X screensaver extension) -----------------------
class Idle:
    def __init__(self):
        self.dpy = open_display()
        if not self.dpy:
            sys.exit("kuhaku-powerd: no display")
        self.root = X.XDefaultRootWindow(self.dpy)
        self.info = Xss.XScreenSaverAllocInfo()

    def seconds(self):
        Xss.XScreenSaverQueryInfo(self.dpy, self.root, self.info)
        return self.info.contents.idle / 1000


def xset(*args):
    try:
        subprocess.run(["xset", *args], timeout=5, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        report()


def notify(title, body, urgency="critical"):
    try:
        subprocess.Popen(["notify-send", "-a", "Power", "-u", urgency, title, body])
    except OSError:
        report()


def load():
    try:
        cfg = json.loads(CONFIG.read_text())
    except FileNotFoundError:
        cfg = {}  # kuhaku-keys.py writes it at its first start
    except Exception:
        report()
        cfg = {}
    if "power" not in cfg:
        cfg["power"] = migrate()
        try:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")
        except OSError:
            report()
    return {**DEFAULTS, **(cfg.get("power") or {})}


def migrate():
    """First start: the choices xfce4-power-manager kept (the lid as the person chose it,
    not as it was set while Claude worked)."""
    out = dict(DEFAULTS)

    def q(prop):
        try:
            r = subprocess.run(
                ["xfconf-query", "-c", "xfce4-power-manager", "-p", "/xfce4-power-manager/" + prop],
                capture_output=True,
                text=True,
                timeout=3,
            )
            return int(r.stdout.strip()) if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None  # no power manager here before: the defaults stand

    for key, prop in (("screen_off", "dpms-on-battery-off"), ("sleep_after", "inactivity-on-battery")):
        v = q(prop)
        if v is not None:
            out[key] = v if key == "screen_off" or v > 14 else 0
    try:
        out["lid"] = int(LID_SAVED.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        v = q("lid-action-on-battery")
        if v is not None:
            out["lid"] = v
    return out


# --- charging: a limit the firmware keeps, raised before you usually head off ------------
def batteries():
    """Batteries whose firmware takes a charge limit (ThinkPads, many others; Macs do not)."""
    return [b for b in sorted(SUPPLY.glob("BAT*")) if (b / "charge_control_end_threshold").exists()]


def set_limit(bat, start, end):
    """Charge between start and end percent. The firmware wants start below end at every step,
    so the order depends on the way it moves. False when not allowed (the udev rule not yet in)."""
    files = {"start": bat / "charge_control_start_threshold", "end": bat / "charge_control_end_threshold"}
    try:
        now = int(files["end"].read_text())
        has_start = files["start"].exists()
        if now == end and (not has_start or int(files["start"].read_text()) == start):
            return True
        steps = [("end", end), ("start", start)] if end > now else [("start", start), ("end", end)]
        for key, value in steps:
            if key == "end" or has_start:
                files[key].write_text(str(value))
        return True
    except PermissionError:
        return False


def likely_away(now, unplugs):
    """True when you have often headed off within the next while at this time of day: on four of
    the last fourteen days, or on two of the last three same weekdays."""

    def seen(days_back):
        lo = now - days_back * 86400
        return any(lo <= t <= lo + AHEAD for t in unplugs)

    daily = sum(seen(d) for d in range(1, 15))
    weekly = sum(seen(7 * w) for w in range(1, 4))
    return daily >= 4 or weekly >= 2


def load_unplugs():
    try:
        return [float(t) for t in json.loads(UNPLUGS.read_text())]
    except FileNotFoundError:
        return []
    except Exception:
        report()
        return []


def save_unplugs(unplugs):
    try:
        UNPLUGS.parent.mkdir(parents=True, exist_ok=True)
        UNPLUGS.write_text(json.dumps([round(t) for t in unplugs]))
    except OSError:
        report()


def trip(events, now):
    """The first calendar event that takes you away from a charger, starting within the next
    eighteen hours or under way now: travel words, or days somewhere, or hours at a place."""
    for ev in events:
        try:
            s, e = datetime.fromisoformat(ev["start"]), datetime.fromisoformat(ev["end"])
        except (KeyError, ValueError):
            continue
        if ev.get("allDay"):  # from midnight to midnight, here
            s, e = datetime.combine(s.date(), datetime.min.time()), datetime.combine(e.date(), datetime.min.time())
        start, end = s.astimezone().timestamp(), e.astimezone().timestamp()
        if not start - BEFORE_TRIP <= now <= end:
            continue
        title, where = ev.get("title", ""), ev.get("where", "")
        long = end - start
        if TRAVEL.search(title) or TRAVEL.search(where):
            return title.strip()
        if where and not NOT_AWAY.search(where) and (long >= 36 * 3600 if ev.get("allDay") else long >= 3 * 3600):
            return title.strip()
    return None


def calendar_events(now):
    try:
        import calendars

        day = date.fromtimestamp(now)
        return calendars.events(day - timedelta(days=14), day + timedelta(days=2))
    except Exception:
        report()
        return []


def network():
    """The network you are on now (its connection name), or None."""
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=5,
        ).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        report()
        return None
    for line in out.splitlines():
        name, _, kind = line.rpartition(":")
        if kind in ("802-11-wireless", "802-3-ethernet"):
            return name.replace("\\:", ":")
    return None


def load_json(path, empty):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return empty
    except Exception:
        report()
        return empty


def save_json(path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
    except OSError:
        report()


def away_from_usual(places, here, today):
    """Plugged in somewhere new (a hotel, a friend's, a phone's hotspot): you are travelling.
    The first network ever charged on is home from the start; others become usual with time."""
    if not here or not places.get("first") or here == places["first"]:
        return False
    cutoff = (today - timedelta(days=60)).isoformat()
    return sum(d >= cutoff for d in places.get("seen", {}).get(here, [])) < USUAL_DAYS


def write_power(key, value):
    try:
        cfg = json.loads(CONFIG.read_text())
        cfg.setdefault("power", {})[key] = value
        tmp = CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2) + "\n")
        tmp.replace(CONFIG)
    except Exception:
        report()


class Power:
    def __init__(self):
        self.idle = Idle()
        self.cfg = load()
        self.mtime = self.config_mtime()
        self.holders: dict[int, tuple[str, str]] = {}  # cookie -> (sender, "sleep" or "screen")
        self.next_cookie = 1
        self.lid_fd = None
        self.key_fd = None
        self.slept_at = -1e9
        self.warned = self.nudged = False
        self.unplugged_at = None  # when the charger came out, while it stays out
        self.unplugs = load_unplugs()
        self.denied = False  # the charge limit may not be written here (said once)
        self.sensed_at = -1e9  # the calendar and the network, looked at every few minutes
        self.going = self.here = None  # a trip in the calendar; the network you are on
        self.places = load_json(PLACES, {})
        self.why = None
        self.system = Gio.bus_get_sync(Gio.BusType.SYSTEM)
        self.session = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.key_fd = self.inhibit("handle-power-key", "Kūhaku's power menu")
        self.apply()
        self.serve("org.freedesktop.PowerManagement", ["/org/freedesktop/PowerManagement/Inhibit"], PM_XML, "sleep")
        self.serve("org.freedesktop.ScreenSaver", ["/org/freedesktop/ScreenSaver", "/ScreenSaver"], SS_XML, "screen")
        self.session.signal_subscribe(
            "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged", None, None, 0, self.gone
        )
        self.system.signal_subscribe(
            UPOWER, "org.freedesktop.DBus.Properties", "PropertiesChanged", "/org/freedesktop/UPower", None, 0,
            lambda *_: self.on_lid(),
        )  # fmt: skip
        self.lid_was = self.lid_closed()
        GLib.timeout_add_seconds(15, self.tick)

    # --- the system: logind and UPower ---
    def inhibit(self, what, why):
        try:
            r, fds = self.system.call_with_unix_fd_list_sync(
                LOGIN1, "/org/freedesktop/login1", f"{LOGIN1}.Manager", "Inhibit",
                GLib.Variant("(ssss)", (what, "Kūhaku", why, "block")), GLib.VariantType("(h)"),
                Gio.DBusCallFlags.NONE, -1, None, None,
            )  # fmt: skip
            return fds.get(r.unpack()[0])
        except Exception:
            report()
            return None

    def login1(self, method, *args):
        try:
            sig = "(" + "b" * len(args) + ")"
            self.system.call_sync(
                LOGIN1, "/org/freedesktop/login1", f"{LOGIN1}.Manager", method,
                GLib.Variant(sig, args) if args else None, None, Gio.DBusCallFlags.NONE, 10000, None,
            )  # fmt: skip
        except Exception:
            report()

    def can(self, what):
        try:
            r = self.system.call_sync(
                LOGIN1, "/org/freedesktop/login1", f"{LOGIN1}.Manager", "Can" + what, None,
                GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 5000, None,
            )  # fmt: skip
            return r.unpack()[0] == "yes"
        except Exception:
            report()
            return False

    def sleep(self):
        self.slept_at = time.monotonic()
        self.login1("Suspend", False)

    def upower(self, path, prop, default=None):
        try:
            r = self.system.call_sync(
                UPOWER, path, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (UPOWER if path == "/org/freedesktop/UPower" else f"{UPOWER}.Device", prop)),
                GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 5000, None,
            )  # fmt: skip
            return r.unpack()[0]
        except GLib.Error:
            return default  # no UPower or no such device (a desktop without a battery)

    def lid_closed(self):
        return bool(self.upower("/org/freedesktop/UPower", "LidIsClosed", False))

    # --- what holds things off ---
    def held(self, kind=None):
        return any(k == kind or kind is None for _, k in self.holders.values())

    def serve(self, name, paths, xml, kind):
        iface = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
        for path in paths:
            self.session.register_object(path, iface, lambda *a, kind=kind: self.call(kind, *a), None, None)
        Gio.bus_own_name_on_connection(self.session, name, Gio.BusNameOwnerFlags.REPLACE, None, None)

    def call(self, kind, _conn, sender, _path, _iface, method, params, inv):
        try:
            if method == "Inhibit":
                cookie = self.next_cookie
                self.next_cookie += 1
                self.holders[cookie] = (sender, kind)
                inv.return_value(GLib.Variant("(u)", (cookie,)))
                self.apply()
            elif method == "UnInhibit":
                self.holders.pop(params.unpack()[0], None)
                inv.return_value(None)
                self.apply()
                self.after_work()
            elif method == "HasInhibit":
                inv.return_value(GLib.Variant("(b)", (self.held(),)))
            elif method == "GetActive":
                inv.return_value(GLib.Variant("(b)", (False,)))
            elif method == "SimulateUserActivity":
                xset("dpms", "force", "on")
                inv.return_value(None)
            else:
                inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except Exception as e:
            report()
            inv.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(e))

    def gone(self, _c, _s, _p, _i, _sig, params):
        """A program that held something off ended without letting go: its holds end too."""
        name, _old, new = params.unpack()
        if new or not name.startswith(":"):
            return
        mine = [c for c, (s, _k) in self.holders.items() if s == name]
        for c in mine:
            self.holders.pop(c)
        if mine:
            self.apply()
            self.after_work()

    # --- applying the choices ---
    def apply(self):
        c = self.cfg
        off = int(c.get("screen_off") or 0) * 60
        if off and not self.held("screen"):
            xset("s", "off")
            xset("dpms", str(off), str(off), str(off))
            xset("+dpms")
        else:
            xset("s", "off")
            xset("-dpms")
        # The lid: the system sleeps on its own, unless Kūhaku answers it (lock, screen off, or
        # Claude still working).
        want_lid = int(c.get("lid", 1)) != 1 or self.held("sleep")
        if want_lid and self.lid_fd is None:
            self.lid_fd = self.inhibit("handle-lid-switch", "The lid as chosen in Kūhaku")
        elif not want_lid and self.lid_fd is not None:
            os.close(self.lid_fd)
            self.lid_fd = None

    def on_lid(self):
        closed = self.lid_closed()
        if closed == self.lid_was:
            return
        self.lid_was = closed
        if not closed:
            xset("dpms", "force", "on")
            return
        if self.lid_fd is None:
            return  # the system puts it to sleep
        if int(self.cfg.get("lid", 1)) == 3:
            subprocess.Popen(["loginctl", "lock-session"])
        xset("dpms", "force", "off")

    def after_work(self):
        """The work that kept the lid from sleeping ended: if the lid is shut and sleep is the
        choice, sleep now."""
        if not self.held("sleep") and int(self.cfg.get("lid", 1)) == 1 and self.lid_closed():
            GLib.timeout_add_seconds(
                3, lambda: (self.lid_closed() and not self.held("sleep") and self.sleep(), False)[1]
            )

    def config_mtime(self):
        try:
            return CONFIG.stat().st_mtime
        except FileNotFoundError:
            return None

    def tick(self):
        try:
            m = self.config_mtime()
            if m != self.mtime:
                self.mtime, self.cfg = m, load()
                self.apply()
            after = int(self.cfg.get("sleep_after") or 0) * 60
            now = time.monotonic()
            if after and not self.held() and self.idle.seconds() >= after and now - self.slept_at > after:
                self.sleep()
            self.battery()
            self.charging()
        except Exception:
            report()
        return True

    def battery(self):
        dev = "/org/freedesktop/UPower/devices/DisplayDevice"
        if not self.upower(dev, "IsPresent", False):
            return
        pct = self.upower(dev, "Percentage", 100.0)
        discharging = self.upower(dev, "State", 0) == 2
        if not discharging:
            self.warned = self.nudged = False
            return
        if LOW < pct <= NUDGE and not self.nudged:
            self.nudged = True
            notify(
                "Battery at 20%", "A good moment to plug in. The battery likes it best between 20 and 80%.", "normal"
            )
        if pct > LOW:
            self.warned = False
            return
        if pct <= CRITICAL and time.monotonic() - self.slept_at > 120:
            notify("Battery empty", "Going to sleep now. Plug in to carry on.")
            if self.can("Hibernate"):
                self.slept_at = time.monotonic()
                self.login1("Hibernate", False)
            else:
                self.sleep()
        elif not self.warned:
            self.warned = True
            notify("Battery low", f"{round(pct)}% left. Plug in soon.")

    def charging(self, now=None):
        """Learn when you head off, and keep the limit the firmware holds to what you need."""
        bats = batteries()
        if not bats:
            return
        now = now or time.time()
        away = bool(self.upower("/org/freedesktop/UPower", "OnBattery", False))
        if not away:
            self.unplugged_at = None
        elif self.unplugged_at is None:
            self.unplugged_at = now
        elif self.unplugged_at > 0 and now - self.unplugged_at >= AWAY:
            # Really headed off: remember when, and a "Full once" has done its work.
            self.unplugs = [t for t in self.unplugs if t > now - 30 * 86400] + [self.unplugged_at]
            save_unplugs(self.unplugs)
            self.unplugged_at = -1  # counted; wait for the next plug in
            if self.cfg.get("full_once"):
                self.cfg["full_once"] = 0
                write_power("full_once", 0)
                self.mtime = self.config_mtime()
        if now - self.sensed_at >= SENSE:
            self.sense(now, plugged=not away)
        why = (
            "always" if int(self.cfg.get("charge") or 80) >= 100
            else "once" if self.cfg.get("full_once")
            else "trip" if self.going
            else "travelling" if not away and away_from_usual(self.places, self.here, date.fromtimestamp(now))
            else "habit" if likely_away(now, self.unplugs)
            else None
        )  # fmt: skip
        if why != self.why:
            self.why = why
            save_json(WHY, {"why": why, "trip": self.going} if why else {})
        start, end = FULL if why else CARE
        for bat in bats:
            if not set_limit(bat, start, end) and not self.denied:
                self.denied = True
                print(
                    "kuhaku-powerd: the charge limit is not writable here (dev/system adds the rule)", file=sys.stderr
                )

    def sense(self, now, plugged):
        """Every few minutes: a trip coming up in the calendar, and the network you charge on."""
        self.sensed_at = now
        self.going = trip(calendar_events(now), now)
        self.here = network()
        if plugged and self.here:
            today = date.fromtimestamp(now).isoformat()
            self.places.setdefault("first", self.here)
            days = self.places.setdefault("seen", {}).setdefault(self.here, [])
            if today not in days:
                days.append(today)
                del days[:-60]
                save_json(PLACES, self.places)


def main():
    if "--help" in sys.argv:
        print(__doc__)
        return
    Power()
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
