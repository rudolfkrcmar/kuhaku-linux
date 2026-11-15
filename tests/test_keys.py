"""kuhaku-keys.py: how keys are read, and the XSETTINGS every app is sent."""

import struct
import unittest

from tests.util import load_file

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

    def test_xsettings_round_trip(self):
        values = {"Xft/DPI": 120 * 1024, "Gtk/FontName": "Inter 10", "Net/ThemeName": "Adwaita", "Gtk/X": ""}
        serial, got = decode(kk.encode_xsettings(values, 7))
        self.assertEqual(serial, 7)
        self.assertEqual(got, values)


if __name__ == "__main__":
    unittest.main()
