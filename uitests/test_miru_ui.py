"""Miru's page, used like a person would: choose, open, right-click, rename, and the cloud chooser."""

import json

from uitests.harness import ROOT, Page, PageCase

PLACES = [
    {"name": "Home", "path": "/h"},
    {"name": "Downloads", "path": "/h/Downloads"},
    {"name": "Dropbox", "cloud": "dropbox", "what": "Connect", "path": "cloud:dropbox"},
    {"name": "Trash", "path": "/h/.local/share/Trash/files"},
]
FOLDER = {
    "cloud": None,
    "path": "/h",
    "crumbs": [{"name": "Home", "path": "/h"}],
    "entries": [
        {
            "name": "Projects",
            "path": "/h/Projects",
            "dir": True,
            "when": "3 Nov",
            "t": 0,
            "size": "4 items",
            "kind": "Folder",
        },
        {"name": "Work", "path": "/h/Work", "dir": True, "when": "today", "t": 0, "size": "2 items", "kind": "Folder"},
        {
            "name": "notes.txt",
            "path": "/h/notes.txt",
            "dir": False,
            "when": "today",
            "t": 0,
            "size": "2 KB",
            "kind": "Text",
        },
        {
            "name": "plan.pdf",
            "path": "/h/plan.pdf",
            "dir": False,
            "when": "1 Oct",
            "t": 0,
            "size": "1.2 MB",
            "kind": "PDF document",
        },
    ],
    "places": PLACES,
    "select": None,
    "hidden": False,
    "trash": False,
}


def miru_page():
    html = (ROOT / "miru" / "miru.html").read_text(encoding="utf-8").replace("{THEME}", "day")
    page = Page("miru/miru.html", "miru", html=html)
    page.js(f"browse({json.dumps(FOLDER)}); return true;")
    page.wait(150)
    return page


class Folder(PageCase):
    def setUp(self):
        self.page = miru_page()

    def selected(self):
        return self.page.js("return [...document.querySelectorAll('.row.sel .nm')].map(e => e.textContent);")

    def test_the_folder_as_a_list(self):
        names = self.page.js("return [...document.querySelectorAll('.row .nm')].map(e => e.textContent);")
        self.assertEqual(names, ["Projects", "Work", "notes.txt", "plan.pdf"])
        self.assertEqual(self.page.js("return document.getElementById('count').textContent;"), "4 items")
        self.assertLooksLike("miru-folder")

    def test_click_shift_click_and_cmd_click_choose(self):
        self.page.click('.row[data-i="1"]')
        self.assertEqual(self.selected(), ["Work"])
        self.page.click('.row[data-i="3"]', shiftKey=True)
        self.assertEqual(self.selected(), ["Work", "notes.txt", "plan.pdf"])
        self.page.click('.row[data-i="2"]', ctrlKey=True)
        self.assertEqual(self.selected(), ["Work", "plan.pdf"])
        self.assertIn("2 of 4 chosen", self.page.js("return document.getElementById('count').textContent;"))

    def test_keys_move_and_open(self):
        self.page.key("ArrowDown")
        self.page.key("ArrowDown")
        self.assertEqual(self.selected(), ["notes.txt"])
        self.page.key("Enter")
        self.assertEqual(self.page.messages[-1], {"type": "preview", "path": "/h/notes.txt"})
        self.page.key("ArrowUp")
        self.page.key("Enter")
        self.assertEqual(self.page.messages[-1], {"type": "nav", "path": "/h/Work"})

    def test_double_click_opens_a_folder(self):
        self.page.click('.row[data-i="0"]', kind="dblclick")
        self.assertEqual(self.page.messages[-1], {"type": "nav", "path": "/h/Projects"})

    def test_right_click_offers_what_can_be_done(self):
        self.page.click('.row[data-i="2"]', kind="contextmenu")
        items = self.page.js("return [...document.querySelectorAll('#ctx a')].map(a => a.textContent);")
        self.assertIn("Rename", items)
        self.assertIn("Move to Trash", items)
        self.page.click('#ctx a[data-k="trash"]')
        self.assertEqual(self.page.messages[-1]["type"], "trash")
        self.assertEqual(self.page.messages[-1]["paths"], ["/h/notes.txt"])

    def test_rename_in_place(self):
        self.page.click('.row[data-i="2"]')
        self.page.key("F2")
        self.page.js(
            "const i = document.querySelector('.row input'); i.value = 'ideas.txt';"
            "i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); return true;"
        )
        self.page.wait(100)
        self.assertEqual(self.page.messages[-1], {"type": "rename", "path": "/h/notes.txt", "name": "ideas.txt"})

    def test_typing_jumps_to_a_name(self):
        self.page.key("p")
        self.page.key("l")
        self.assertEqual(self.selected(), ["plan.pdf"])

    def test_places_navigate(self):
        self.page.click('#side a[data-path="/h/Downloads"]')
        self.assertEqual(self.page.messages[-1], {"type": "nav", "path": "/h/Downloads"})


class CloudChooser(PageCase):
    def setUp(self):
        self.page = miru_page()
        self.page.js(
            "cloud("
            + json.dumps(
                {
                    "key": "drive",
                    "name": "Google Drive",
                    "kind": "Google Drive",
                    "phase": "choose",
                    "error": None,
                    "local": "~/Google Drive",
                    "folders": [],
                    "places": PLACES,
                    "gdocs": True,
                }
            )
            + "); return true;"
        )
        self.page.js("cloudList({key: 'drive', path: '', dirs: ['Photos', 'Projects', 'Work']}); return true;")
        self.page.wait(100)

    def test_asks_for_the_folders_list(self):
        self.assertIn({"type": "cloud-list", "key": "drive", "path": ""}, self.page.messages)

    def test_ticking_and_saving(self):
        button = "return document.getElementById('csave').textContent;"
        self.assertEqual(self.page.js(button), "Stop syncing")
        self.page.click('.crow[data-p="Photos"] .cn')
        self.page.click('.crow[data-p="Work"] .cn')
        self.assertEqual(self.page.js(button), "Keep 2 folders in sync")
        self.assertLooksLike("miru-cloud-choose")
        self.page.click("#csave")
        saved = self.page.messages[-1]
        self.assertEqual(saved["type"], "cloud-save")
        self.assertEqual(sorted(saved["folders"]), ["Photos", "Work"])

    def test_opening_a_folder_asks_for_what_is_inside(self):
        self.page.click('.crow[data-p="Work"] .car')
        self.assertIn({"type": "cloud-list", "key": "drive", "path": "Work"}, self.page.messages)

    def test_sign_out_asks_twice(self):
        self.page.click("#cout")
        self.assertIn("Click again", self.page.js("return document.getElementById('cout').textContent;"))
        self.assertNotIn("cloud-disconnect", [m.get("type") for m in self.page.messages])
        self.page.click("#cout")
        self.assertEqual(self.page.messages[-1], {"type": "cloud-disconnect", "key": "drive"})
