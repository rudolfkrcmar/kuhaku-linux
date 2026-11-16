"""Kūhaku's file picker in its own window (kuhaku-pick.py): Tegami's attachments, Mado's uploads."""

import json
from datetime import datetime

from uitests.harness import ROOT, Page, PageCase

T = datetime(2026, 11, 12, 9, 41).timestamp()  # the moment the harness freezes the clock at


class Picking(PageCase):
    def setUp(self):
        self.page = Page("pick/pick.html", "pick", query="?title=Attach%20files&verb=Attach")
        shot = (ROOT / "uitests" / "shots" / "home-day.png").as_uri()
        items = [
            {"path": "/tmp/report.pdf", "name": "Quarterly report.pdf", "t": T - 600, "size": "1.2 MB", "kind": "PDF"},
            {"path": "/tmp/shot.png", "name": "Screenshot.png", "t": T - 4000, "size": "80 KB", "uri": shot},
            {"path": "/tmp/notes.txt", "name": "notes.txt", "t": T - 90000, "size": "2 KB", "kind": "TXT"},
        ]
        listing = {
            "folder": "Recent",
            "path": "",
            "crumbs": [],
            "folders": ["Recent", "Downloads", "Other"],
            "items": items,
        }
        self.page.js(f"setListing({json.dumps(listing)}); return true;")
        self.page.wait(250)

    def key(self, key):
        self.page.js(
            f"window.dispatchEvent(new KeyboardEvent('keydown', {{key: {json.dumps(key)}, bubbles: true}})); return true;"
        )
        self.page.wait(150)

    def sent(self):
        return [json.loads(m) if isinstance(m, str) else m for m in self.page.messages]

    def test_asks_for_recent_files_and_shows_them(self):
        self.assertIn({"action": "list", "folder": "Recent", "path": ""}, self.sent())
        self.assertLooksLike("pick-day", tolerance=0.01)

    def test_looking_closer_at_a_document_asks_for_its_first_page(self):
        self.key(" ")
        self.assertIn({"action": "peek", "path": "/tmp/report.pdf"}, self.sent())
        self.page.js("setPeek({path: '/tmp/report.pdf', text: 'Hello'}); return true;")
        self.page.wait(100)
        self.assertEqual(self.page.js("return document.querySelector('#peek pre').textContent;"), "Hello")

    def test_choose_two_and_attach(self):
        self.key("x")
        self.key("ArrowRight")
        self.key("x")
        self.key("Enter")
        self.assertIn({"action": "add", "paths": ["/tmp/report.pdf", "/tmp/shot.png"]}, self.sent())
        self.assertIn({"action": "close"}, self.sent())

    def test_escape_closes_without_a_choice(self):
        self.key("Escape")
        self.assertEqual(self.sent()[-1], {"action": "close"})
