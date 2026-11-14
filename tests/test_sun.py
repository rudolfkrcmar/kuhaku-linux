"""The auto theme follows the sun: dark after a November sunset, light at midday."""

import calendar
import unittest

import kuhaku_sun

BERLIN = {"latitude": 52.52, "longitude": 13.40}


def utc(*t):
    return calendar.timegm((*t, 0, 0, 0))


class Sun(unittest.TestCase):
    def test_november_evening_is_night(self):
        self.assertTrue(kuhaku_sun.is_night(BERLIN, utc(2026, 11, 13, 16, 50)))  # 17:50 in Berlin

    def test_midday_is_day(self):
        self.assertFalse(kuhaku_sun.is_night(BERLIN, utc(2026, 11, 13, 11, 0)))

    def test_june_evening_still_day(self):
        self.assertFalse(kuhaku_sun.is_night(BERLIN, utc(2026, 6, 21, 18, 30)))  # 20:30 in Berlin

    def test_place_from_time_zone(self):
        lat, lon = kuhaku_sun.place({}) or (None, None)
        if lat is not None:
            self.assertTrue(-90 <= lat <= 90 and -180 <= lon <= 180)


if __name__ == "__main__":
    unittest.main()
