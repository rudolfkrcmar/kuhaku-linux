"""kuhaku-keys.py: how keys are read, and the XSETTINGS every app is sent."""

import struct
import unittest

from tests.util import ROOT, load_file

kk = load_file("kuhaku-keys.py")


def decode(blob):
    """The XSETTINGS format read back, as GTK reads it."""
    _order, serial, count = struct.unpack_from("<B3xII", blob, 0)
    at, out = 12, {}
    for _ in range(count):
        typ, n = struct.unpack_from("<BxH", blob, at)
        at += 4
        name = blob[at : at + n].decode()
        at += n + (-n % 4) + 4  # the name, padded, then its last-change serial
        if typ == 0:
            out[name] = struct.unpack_from("<i", blob, at)[0]
            at += 4
        else:
            ln = struct.unpack_from("<I", blob, at)[0]
            at += 4
            out[name] = blob[at : at + ln].decode()
            at += ln + (-ln % 4)
    assert at == len(blob)
    return serial, out


class Keys(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(kk.parse("Print"), (0, "Print"))
        self.assertEqual(kk.parse("<Primary><Shift>4"), (5, "4"))
        self.assertEqual(kk.parse("<Primary><Shift><Super>numbersign"), (69, "numbersign"))
        self.assertEqual(kk.parse("<Alt>Print"), (8, "Print"))

    def test_every_key_runs_something_of_kuhaku(self):
        for spec, cmd in kk.KEYS.items():
            with self.subTest(spec=spec):
                self.assertTrue(kk.parse(spec)[1])
                self.assertTrue(cmd.split()[-1] and ("kuhaku" in cmd or "/.local/bin/" in cmd))

    def test_device_names_match_without_punctuation(self):
        self.assertEqual(kk.same("TPPS/2 Elan TrackPoint"), kk.same("TPPS2 Elan TrackPoint"))
        self.assertEqual(kk.same("ELAN0672:00 04F3:3187 Touchpad"), kk.same("ELAN067200 04F33187 Touchpad"))
        self.assertNotEqual(kk.same("ELAN0672:00 04F3:3187 Mouse"), kk.same("ELAN067200 04F33187 Touchpad"))

    def test_the_session_starts_kuhakus_own_pieces(self):
        session = (ROOT / "kuhaku-session").read_text()
        for piece in ("kuhaku-keys.py", "kuhaku-notify.py", "kuhaku-powerd.py"):
            self.assertIn(f'python3 "$DIR/{piece}"', session)
        for xfce in ("xfsettingsd", "xfce4-power-manager", "xfce4-notifyd"):
            self.assertNotIn(xfce + " &", (ROOT / "os/package/usr/bin/kuhaku-start").read_text())

    def test_xsettings_round_trip(self):
        values = {"Xft/DPI": 120 * 1024, "Gtk/FontName": "Inter 10", "Net/ThemeName": "Adwaita", "Gtk/X": ""}
        serial, got = decode(kk.encode_xsettings(values, 7))
        self.assertEqual(serial, 7)
        self.assertEqual(got, values)


if __name__ == "__main__":
    unittest.main()
