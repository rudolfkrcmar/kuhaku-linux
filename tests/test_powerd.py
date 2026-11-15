"""kuhaku-powerd.py's decisions: who holds sleep off, the lid, the screen, the battery.

The system (logind, UPower, X's DPMS) is replaced by a recorder, so nothing really sleeps.
"""

import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from tests.util import load_file

kp = load_file("kuhaku-powerd.py")


class Invocation:
    def __init__(self):
        self.value = self.error = None

    def return_value(self, v):
        self.value = v

    def return_dbus_error(self, name, _msg):
        self.error = name


class FakePower(kp.Power):
    """A Power with the system swapped out: everything it would do is written down."""

    def __init__(self, lid=1, lid_closed=False, battery=(100.0, 2)):
        self.cfg = {**kp.DEFAULTS, "lid": lid}
        self.holders = {}
        self.next_cookie = 1
        self.lid_fd = None
        self.slept_at = -1e9
        self.warned = self.nudged = False
        self.unplugged_at = None
        self.unplugs = []
        self.denied = False
        self.sensed_at = -1e9
        self.going = self.here = self.why = None
        self.places = {}
        self.mtime = None
        self.on_battery = False
        self.closed = lid_closed
        self.state = battery
        self.did = []

    def inhibit(self, what, _why):
        self.did.append("inhibit " + what)
        return 99

    def lid_closed(self):
        return self.closed

    def sleep(self):
        self.did.append("sleep")
        self.slept_at = time.monotonic()

    def can(self, _what):
        return False

    def upower(self, _path, prop, default=None):
        return {
            "IsPresent": True,
            "Percentage": self.state[0],
            "State": self.state[1],
            "OnBattery": self.on_battery,
        }.get(prop, default)

    def ask(self, kind, method, *args):
        inv = Invocation()
        params = mock.Mock(unpack=lambda: args)
        self.call(kind, None, ":1.42", None, None, method, params, inv)
        return inv


class Holds(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(kp, "xset"), mock.patch.object(kp.os, "close"), mock.patch.object(kp, "notify")):
            self.addCleanup(p.stop)
            setattr(self, p.attribute, p.start())

    def test_work_holds_the_lid_and_sleep_until_it_ends(self):
        p = FakePower(lid=1)
        p.apply()
        self.assertIsNone(p.lid_fd)  # the system sleeps on the lid by itself
        cookie = p.ask("sleep", "Inhibit", "Kūhaku", "Claude is working").value.unpack()[0]
        self.assertEqual(p.lid_fd, 99)
        self.assertTrue(p.held("sleep"))
        p.ask("sleep", "UnInhibit", cookie)
        self.assertIsNone(p.lid_fd)
        self.assertFalse(p.held())

    def test_a_program_that_ends_lets_go(self):
        p = FakePower()
        p.ask("screen", "Inhibit", "Mado", "Playing video")
        self.xset.assert_any_call("-dpms")
        p.gone(None, None, None, None, None, mock.Mock(unpack=lambda: (":1.42", ":1.42", "")))
        self.assertEqual(p.holders, {})
        self.xset.assert_any_call("+dpms")

    def test_lid_choices(self):
        for lid, keeps in ((1, False), (0, True), (3, True)):
            with self.subTest(lid=lid):
                p = FakePower(lid=lid)
                p.apply()
                self.assertEqual(p.lid_fd is not None, keeps)

    def test_screen_off_never(self):
        p = FakePower()
        p.cfg["screen_off"] = 0
        p.apply()
        self.xset.assert_any_call("-dpms")


class Battery(unittest.TestCase):
    def test_low_then_empty(self):
        with mock.patch.object(kp, "notify") as notify:
            p = FakePower()
            seen = []
            for pct, state in [(50, 2), (10, 2), (9, 2), (8, 1), (9, 2), (4, 2), (3, 2)]:
                p.state = (float(pct), state)
                n, d = notify.call_count, len(p.did)
                p.battery()
                seen.append((notify.call_count - n, p.did[d:]))
        self.assertEqual(
            seen,
            [(0, []), (1, []), (0, []), (0, []), (1, []), (1, ["sleep"]), (0, [])],
        )  # one card per discharge, sleep once at the end

    def test_a_gentle_nudge_at_twenty(self):
        with mock.patch.object(kp, "notify") as notify:
            p = FakePower()
            for pct, state in [(25, 2), (20, 2), (19, 2), (30, 1), (18, 2)]:
                p.state = (float(pct), state)
                p.battery()
        titles = [c.args[0] for c in notify.call_args_list]
        self.assertEqual(titles, ["Battery at 20%", "Battery at 20%"])  # once per discharge
        self.assertEqual(notify.call_args_list[0].args[2], "normal")


class Charging(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bat = Path(tmp.name) / "BAT0"
        self.bat.mkdir()
        self.limit(0, 100)
        self.events, self.net = [], "Home"
        t = Path(tmp.name)
        for name, value in (("SUPPLY", t), ("UNPLUGS", t / "unplugs.json"), ("CONFIG", t / "desktop.json"),
                            ("PLACES", t / "places.json"), ("WHY", t / "why.json"),
                            ("calendar_events", lambda _now: self.events), ("network", lambda: self.net)):  # fmt: skip
            patch = mock.patch.object(kp, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def limit(self, start=None, end=None):
        if start is not None:
            (self.bat / "charge_control_start_threshold").write_text(str(start))
            (self.bat / "charge_control_end_threshold").write_text(str(end))
        return (int((self.bat / "charge_control_start_threshold").read_text()),
                int((self.bat / "charge_control_end_threshold").read_text()))  # fmt: skip

    def test_care_holds_at_eighty(self):
        p = FakePower()
        p.charging(now=1e9)
        self.assertEqual(self.limit(), (75, 80))

    def test_always_full(self):
        p = FakePower()
        p.cfg["charge"] = 100
        p.charging(now=1e9)
        self.assertEqual(self.limit(), (0, 100))

    def test_learns_when_you_head_off_and_fills_before(self):
        day, morning = 86400, 1e9
        p = FakePower()
        p.unplugs = [morning - d * day + 3600 for d in range(1, 6)]  # off an hour later, five days running
        p.charging(now=morning)
        self.assertEqual(self.limit(), (0, 100))
        p.charging(now=morning + 6 * 3600)  # the afternoon: no habit then
        self.assertEqual(self.limit(), (75, 80))

    def test_full_once_ends_when_you_head_off(self):
        kp.CONFIG.write_text('{"power": {"full_once": 1}}')
        p = FakePower()
        p.cfg["full_once"] = 1
        p.charging(now=1e9)
        self.assertEqual(self.limit(), (0, 100))
        p.on_battery = True
        p.charging(now=1e9 + 60)
        p.charging(now=1e9 + 60 + kp.AWAY)
        self.assertEqual(p.cfg["full_once"], 0)
        self.assertEqual(json.loads(kp.CONFIG.read_text())["power"]["full_once"], 0)
        self.assertEqual(json.loads(kp.UNPLUGS.read_text()), [round(1e9 + 60)])
        self.assertEqual(self.limit(), (75, 80))

    def test_a_cable_moved_is_not_heading_off(self):
        p = FakePower()
        p.on_battery = True
        p.charging(now=1e9)
        p.on_battery = False
        p.charging(now=1e9 + 120)
        self.assertEqual(p.unplugs, [])

    def test_fills_before_a_trip_in_the_calendar(self):
        now = 1e9
        at = lambda h: datetime.fromtimestamp(now + h * 3600).astimezone().isoformat()
        self.events = [{"title": "Flight to Frankfurt (DE 4485)", "where": "Prague PRG", "allDay": False,
                        "start": at(17), "end": at(18)}]  # fmt: skip
        p = FakePower()
        p.charging(now=now)
        self.assertEqual(self.limit(), (0, 100))
        self.assertEqual(json.loads(kp.WHY.read_text()), {"why": "trip", "trip": "Flight to Frankfurt (DE 4485)"})
        self.events = []
        p.charging(now=now + 20 * 3600 + kp.SENSE)  # landed, home again
        self.assertEqual(self.limit(), (75, 80))
        self.assertEqual(json.loads(kp.WHY.read_text()), {})

    def test_what_counts_as_a_trip(self):
        now = 1e9
        at = lambda h: datetime.fromtimestamp(now + h * 3600).astimezone().isoformat()
        day = lambda d: (datetime.fromtimestamp(now).date() + timedelta(days=d)).isoformat()
        cases = [
            ({"title": "Weekly sync", "where": "Meeting room 923", "start": at(1), "end": at(5)}, None),
            ({"title": "Dinner", "where": "https://example.org", "start": at(1), "end": at(5)}, None),
            ({"title": "Standup", "where": "", "start": at(1), "end": at(2)}, None),
            ({"title": "Workshop day", "where": "Castle Hall, Town", "start": at(2), "end": at(8)}, "Workshop day"),
            (
                {"title": "Weekend away", "where": "Somewhere 1, Town", "allDay": True, "start": day(0), "end": day(3)},
                "Weekend away",
            ),
            ({"title": "Hotel check-in", "where": "", "start": at(30), "end": at(31)}, None),  # too far ahead
            ({"title": "Train to Vienna", "where": "", "start": at(-1), "end": at(2)}, "Train to Vienna"),  # under way
        ]
        for ev, want in cases:
            with self.subTest(ev["title"]):
                self.assertEqual(kp.trip([ev], now), want)

    def test_travelling_is_a_network_not_one_of_your_usual_places(self):
        p = FakePower()
        p.charging(now=1e9)  # first plugged in at home: home from the start
        self.assertEqual(self.limit(), (75, 80))
        self.net = "Hotel Guest"
        p.charging(now=1e9 + kp.SENSE)
        self.assertEqual(self.limit(), (0, 100))
        self.assertEqual(json.loads(kp.WHY.read_text())["why"], "travelling")
        today = datetime.fromtimestamp(1e9).date()
        p.places["seen"]["Office"] = [(today - timedelta(days=d)).isoformat() for d in range(5)]
        self.net = "Office"
        p.charging(now=1e9 + 2 * kp.SENSE)
        self.assertEqual(self.limit(), (75, 80))  # charged on five days: usual

    def test_order_keeps_start_below_end(self):
        writes = []
        real = Path.write_text

        def spy(path, text):
            if path.name.startswith("charge_control"):
                writes.append(path.name.split("_")[2])
            return real(path, text)

        with mock.patch.object(Path, "write_text", spy):
            kp.set_limit(self.bat, 75, 80)  # going down: start first
            kp.set_limit(self.bat, 0, 100)  # going up: end first
        self.assertEqual(writes, ["start", "end", "end", "start"])

    def test_not_allowed_yet(self):
        with mock.patch.object(Path, "write_text", side_effect=PermissionError):
            self.assertFalse(kp.set_limit(self.bat, 75, 80))


if __name__ == "__main__":
    unittest.main()
