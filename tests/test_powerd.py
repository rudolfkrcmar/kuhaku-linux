"""kuhaku-powerd.py's decisions: who holds sleep off, the lid, the screen, the battery.

The system (logind, UPower, X's DPMS) is replaced by a recorder, so nothing really sleeps.
"""

import time
import unittest
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
        self.warned = False
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
        return {"IsPresent": True, "Percentage": self.state[0], "State": self.state[1]}.get(prop, default)

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


if __name__ == "__main__":
    unittest.main()
