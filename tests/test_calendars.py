"""calendars.py: the saved feeds, fetching (urlopen mocked), events from a cached .ics, month_view."""

import unittest
from datetime import date
from unittest import mock

import calendars
from tests.util import TempDirCase

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
X-WR-CALNAME:Family
BEGIN:VEVENT
UID:1
SUMMARY:Dentist
LOCATION:Main St
DTSTART:20261110T090000Z
DTEND:20261110T100000Z
END:VEVENT
BEGIN:VEVENT
UID:2
SUMMARY:Holiday
DTSTART;VALUE=DATE:20261110
DTEND;VALUE=DATE:20261111
END:VEVENT
BEGIN:VEVENT
UID:3
SUMMARY:Standup
DTSTART:20261102T080000Z
DTEND:20261102T081500Z
RRULE:FREQ=WEEKLY;COUNT=4
END:VEVENT
BEGIN:VEVENT
UID:4
DTSTART:20261111T120000Z
END:VEVENT
BEGIN:VEVENT
UID:5
SUMMARY:Long ago
DTSTART:20200101T120000Z
END:VEVENT
END:VCALENDAR
"""


class CalCase(TempDirCase):
    def setUp(self):
        super().setUp()
        for name, value in {"CONFIG": self.tmp / "conf/calendars.json", "CACHE": self.tmp / "cache"}.items():
            p = mock.patch.object(calendars, name, value)
            p.start()
            self.addCleanup(p.stop)

    def response(self, data):
        r = mock.Mock()
        r.read.return_value = data
        return r


class Store(CalCase):
    def test_load_save(self):
        self.assertEqual(calendars.load(), [])
        calendars.save([{"url": "u", "name": "n"}])
        self.assertEqual(calendars.load(), [{"url": "u", "name": "n"}])
        self.assertEqual(oct(calendars.CONFIG.stat().st_mode & 0o777), "0o600")  # the secret address

    def test_add_and_webcal(self):
        with mock.patch.object(calendars.urllib.request, "urlopen", return_value=self.response(ICS)) as uo:
            self.assertEqual(calendars.add("  webcal://cal.example/secret.ics "), "Family")
        req = uo.call_args.args[0]
        self.assertEqual(req.full_url, "https://cal.example/secret.ics")
        self.assertEqual(calendars.load(), [{"url": "webcal://cal.example/secret.ics", "name": "Family"}])
        self.assertEqual((calendars.CACHE / "0.ics").read_bytes(), ICS)

    def test_not_a_calendar(self):
        with mock.patch.object(calendars.urllib.request, "urlopen", return_value=self.response(b"<html>")):
            with self.assertRaisesRegex(ValueError, "not a calendar"):
                calendars.add("https://example.com")
        self.assertEqual(calendars.load(), [])

    def test_name_of(self):
        self.assertEqual(calendars.name_of(ICS), "Family")
        self.assertEqual(calendars.name_of(b"BEGIN:VCALENDAR\nEND:VCALENDAR\n"), "Calendar")
        self.assertEqual(calendars.name_of(b"garbage"), "Calendar")

    def test_refresh_offline_keeps_copy(self):
        calendars.save([{"url": "https://x", "name": "A"}])
        calendars.CACHE.mkdir(parents=True)
        (calendars.CACHE / "0.ics").write_bytes(ICS)
        with mock.patch.object(calendars.urllib.request, "urlopen", side_effect=OSError("offline")):
            calendars.refresh(force=True)
        self.assertEqual((calendars.CACHE / "0.ics").read_bytes(), ICS)

    def test_refresh_skips_fresh_copies(self):
        calendars.save([{"url": "https://x", "name": "A"}])
        calendars.CACHE.mkdir(parents=True)
        (calendars.CACHE / "0.ics").write_bytes(ICS)
        with mock.patch.object(calendars, "fetch") as f:
            calendars.refresh()
        f.assert_not_called()

    def test_remove(self):
        calendars.save([{"url": "a", "name": "A"}, {"url": "b", "name": "B"}])
        calendars.CACHE.mkdir(parents=True)
        (calendars.CACHE / "0.ics").write_bytes(ICS)
        with mock.patch.object(calendars, "fetch") as f:
            calendars.remove(5)
            self.assertEqual(len(calendars.load()), 2)
            calendars.remove(0)
        self.assertEqual(calendars.load(), [{"url": "b", "name": "B"}])
        self.assertFalse((calendars.CACHE / "0.ics").exists())
        f.assert_called_once_with({"url": "b", "name": "B"}, 0)


class Events(CalCase):
    def setUp(self):
        super().setUp()
        calendars.save([{"url": "x", "name": "Family"}, {"url": "y", "name": "Missing"}])
        calendars.CACHE.mkdir(parents=True)
        (calendars.CACHE / "0.ics").write_bytes(ICS)

    def test_events_in_range(self):
        evs = calendars.events(date(2026, 11, 1), date(2026, 12, 1))
        titles = [e["title"] for e in evs]
        self.assertEqual(titles.count("Standup"), 4)  # the weekly repeat is unfolded
        self.assertIn("Dentist", titles)
        self.assertIn("(no title)", titles)
        self.assertNotIn("Long ago", titles)
        holiday = next(e for e in evs if e["title"] == "Holiday")
        self.assertTrue(holiday["allDay"])
        self.assertEqual(holiday["day"], "2026-11-10")
        dentist = next(e for e in evs if e["title"] == "Dentist")
        self.assertFalse(dentist["allDay"])
        self.assertEqual(dentist["where"], "Main St")
        self.assertEqual(dentist["cal"], "Family")
        # sorted by day, all-day first within a day
        keys = [(e["day"], not e["allDay"], e["start"]) for e in evs]
        self.assertEqual(keys, sorted(keys))

    def test_broken_cache_is_skipped(self):
        (calendars.CACHE / "0.ics").write_bytes(b"garbage")
        self.assertEqual(calendars.events(date(2026, 11, 1), date(2026, 12, 1)), [])

    def test_month_view_is_zero_based(self):
        view = calendars.month_view(2026, 10)  # JavaScript's month 10 is November
        start, end = (date.fromisoformat(x) for x in view["range"])
        self.assertEqual(start.weekday(), 0)
        self.assertLess(start, date(2026, 11, 1))
        self.assertEqual((end - start).days, 56)
        self.assertEqual(view["calendars"], [{"name": "Family"}, {"name": "Missing"}])
        self.assertIn("Dentist", [e["title"] for e in view["events"]])
        self.assertEqual(calendars.month_view(2026, 11)["range"][0][:7] in ("2026-11", "2026-10"), True)


if __name__ == "__main__":
    unittest.main()
