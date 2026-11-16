"""kuhaku_pick: what the Kūhaku file picker lists, and when apps use it."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import kuhaku_pick


class Listing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        for sub in ("Downloads", "Pictures/Screenshots", "Documents/Taxes"):
            (self.home / sub).mkdir(parents=True)
        for i, rel in enumerate(
            ("Downloads/report.pdf", "Downloads/cat.png", "Pictures/Screenshots/shot.png", "Documents/notes.txt")
        ):
            f = self.home / rel
            f.write_bytes(b"x")
            os.utime(f, (1000 + i, 1000 + i))
        (self.home / "Downloads/.hidden.png").write_bytes(b"x")
        patch = mock.patch.object(kuhaku_pick, "HOME", self.home)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def names(self, listing):
        return [e["name"] for e in listing["items"]]

    def test_recent_is_newest_first_across_folders(self):
        got = kuhaku_pick.listing("Recent")
        self.assertEqual(self.names(got), ["notes.txt", "shot.png", "cat.png", "report.pdf"])
        self.assertEqual(got["folders"], ["Recent", "Downloads", "Documents", "Pictures", "Other"])

    def test_pictures_only_shows_pictures_with_a_thumbnail(self):
        got = kuhaku_pick.listing("Downloads", images=True)
        self.assertEqual(self.names(got), ["cat.png"])
        self.assertTrue(got["items"][0]["uri"].startswith("file://"))

    def test_other_walks_home_folders_first_and_never_leaves_home(self):
        got = kuhaku_pick.listing("Other", str(self.home / "Documents"))
        self.assertEqual(self.names(got), ["Taxes", "notes.txt"])
        self.assertEqual([c["name"] for c in got["crumbs"]], ["Home", "Documents"])
        outside = kuhaku_pick.listing("Other", "/etc")
        self.assertEqual(outside["path"], str(self.home))

    def test_what_the_page_accepts(self):
        self.assertTrue(kuhaku_pick.accepts(Path("a.PNG"), ["image/*"]))
        self.assertTrue(kuhaku_pick.accepts(Path("a.csv"), [".csv"]))
        self.assertTrue(kuhaku_pick.accepts(Path("a.pdf"), ["application/pdf"]))
        self.assertFalse(kuhaku_pick.accepts(Path("a.pdf"), ["image/*"]))
        self.assertTrue(kuhaku_pick.accepts(Path("a.pdf"), []))
        got = kuhaku_pick.listing("Downloads", accept=["application/pdf"])
        self.assertEqual(self.names(got), ["report.pdf"])

    def test_outside_kuhaku_apps_keep_the_desktops_dialog(self):
        with mock.patch.dict(os.environ, {"KUHAKU_SESSION": ""}):
            self.assertIsNone(kuhaku_pick.pick_command())
