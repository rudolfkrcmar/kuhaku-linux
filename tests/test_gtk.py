import kuhaku_gtk as kg
from tests.util import TempDirCase


class GtkThemeTest(TempDirCase):
    NIGHT = "a { background: #121212; color: #ECECEC; border: 1px solid rgba(236,236,236,.08); caret-color: #f0705a; }\nb { color: #9a9a9a; } c { color: #666666; }"

    def test_night_is_the_source(self):
        self.assertEqual(kg.css_for("night", self.NIGHT), self.NIGHT)

    def test_day_swaps_every_token_once(self):
        day = kg.css_for("day", self.NIGHT)
        self.assertIn("background: #f7f4f1", day)
        self.assertIn("color: #111111", day)
        self.assertIn("rgba(17,17,17,.08)", day)
        self.assertIn("caret-color: #e0301e", day)
        self.assertIn("b { color: #666666; }", day)
        self.assertIn("c { color: #9a9a9a; }", day)

    def test_apply_writes_once_per_change(self):
        src, out = self.tmp / "src.css", self.tmp / "gtk-3.0" / "kuhaku.css"
        src.write_text(self.NIGHT)
        kg._written = None
        pairs = ((src, out),)
        self.assertTrue(kg.apply("day", pairs))
        self.assertIn("#f7f4f1", out.read_text())
        self.assertIn('@import url("kuhaku.css");', (out.parent / "gtk.css").read_text())
        self.assertFalse(kg.apply("day", pairs))
        self.assertTrue(kg.apply("night", pairs))
        self.assertEqual(out.read_text(), self.NIGHT)
        self.assertEqual((out.parent / "gtk.css").read_text().count("kuhaku.css"), 1)

    def test_the_real_stylesheets_have_a_day(self):
        for src in (kg.SOURCE, kg.SOURCE4):
            self.check_day(kg.css_for("day", src.read_text()))

    def check_day(self, day):
        for night in ("#121212", "#ececec", "#f0705a", "rgba(236,236,236,"):
            self.assertNotIn(night, day.lower())
