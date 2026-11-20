"""The home screen's page, used like a person would: ask, see the answer, tabs, day and night."""

import json
from datetime import datetime

from uitests.harness import ROOT, Page, PageCase

T = datetime(2026, 11, 12, 9, 41).timestamp()  # the moment the harness freezes the clock at


def home(theme="day"):
    page = Page("index.html", "kuhaku", query=f"?theme={theme}")
    page.js(
        "setTabs("
        + json.dumps(
            {
                "tabs": [{"id": "1", "title": "", "status": "idle", "place": None}],
                "current": "1",
            }
        )
        + "); return true;"
    )
    page.wait(150)
    return page


def ask_key(page, key, **opts):
    page.js(
        f"document.getElementById('ask').dispatchEvent(new KeyboardEvent('keydown', {{key: {json.dumps(key)},"
        f" bubbles: true, cancelable: true, ...{json.dumps(opts)}}})); return true;"
    )
    page.wait(150)


class Asking(PageCase):
    def setUp(self):
        self.page = home()

    def test_the_empty_home(self):
        self.assertEqual(self.page.js("return document.getElementById('ask').placeholder.startsWith('Ask');"), True)
        self.assertLooksLike("home-day", tolerance=0.004)

    def test_enter_asks_claude(self):
        self.page.type_into("#ask", "What is the weather tomorrow?")
        ask_key(self.page, "Enter")
        self.assertIn({"type": "ask", "text": "What is the weather tomorrow?"}, self.page.messages)
        self.assertEqual(self.page.js("return document.getElementById('ask').value;"), "")

    def test_shift_enter_is_a_new_line_not_a_question(self):
        self.page.type_into("#ask", "first line")
        ask_key(self.page, "Enter", shiftKey=True)
        self.assertNotIn("ask", [m.get("type") for m in self.page.messages if isinstance(m, dict)])

    def test_an_empty_line_asks_nothing(self):
        self.page.type_into("#ask", "   ")
        ask_key(self.page, "Enter")
        self.assertNotIn("ask", [m.get("type") for m in self.page.messages if isinstance(m, dict)])

    def test_up_brings_back_the_last_question(self):
        self.page.type_into("#ask", "Open Mado")
        ask_key(self.page, "Enter")
        ask_key(self.page, "ArrowUp")
        self.assertEqual(self.page.js("return document.getElementById('ask').value;"), "Open Mado")

    def test_the_answer_is_shown_plain(self):
        self.page.js(
            "showAnswer("
            + json.dumps(
                {
                    "question": "Is the backup done?",
                    "answer": "**Yes.** It finished at 7:02 and took four minutes.",
                    "error": False,
                    "time": 1794500000,
                    "tab": "1",
                }
            )
            + "); return true;"
        )
        self.page.wait(600)
        text = self.page.js("return document.body.innerText;")
        self.assertIn("Yes. It finished at 7:02", text)  # markdown that slips through is stripped
        self.assertNotIn("**", text)

    def test_the_history_button_shows_the_tab_as_a_chat(self):
        old = [
            {"question": "Is the backup done?", "answer": "Yes.", "error": False, "time": 1794500000, "tab": "1"},
            {"question": "Other tab", "answer": "Not here.", "error": False, "time": 1794500100, "tab": "2"},
            {"question": "And the photos?", "answer": "Those too.", "error": False, "time": 1794500200, "tab": "1"},
        ]
        self.page.js("setAnswers(" + json.dumps({"answers": old, "show": False}) + "); return true;")
        self.page.wait(150)
        self.assertEqual(self.page.js("return document.getElementById('histBtn').hidden;"), False)
        self.page.click("#histBtn")
        self.page.wait(300)
        chat = self.page.js("return document.getElementById('answerChat').innerText;")
        self.assertIn("Is the backup done?", chat)
        self.assertIn("Those too.", chat)
        self.assertNotIn("Not here.", chat)  # only this tab's conversation
        self.assertLess(chat.index("backup"), chat.index("photos"))  # oldest first, like a chat
        ask_key(self.page, "Escape")
        self.assertEqual(self.page.js("return document.getElementById('answerChat').hidden;"), True)
        self.assertEqual(self.page.js("return document.getElementById('answerText').hidden;"), False)

    def test_the_chat_while_claude_works_and_escape_keeps_the_work(self):
        old = [{"question": "Is the backup done?", "answer": "Yes.", "error": False, "time": 1794500000, "tab": "1"}]
        self.page.js(
            "setAnswers(" + json.dumps({"answers": old, "show": False}) + "); show('working', 'Next'); return true;"
        )
        self.page.wait(150)
        self.page.click("#peekBtn")
        self.page.wait(300)
        self.assertIn("Is the backup done?", self.page.js("return document.getElementById('answerChat').innerText;"))
        self.page.key("Escape")
        self.assertEqual(self.page.js("return [phase, document.getElementById('answer').hidden];"), ["working", True])
        self.assertNotIn("cancel", [m.get("type") for m in self.page.messages if isinstance(m, dict)])


class Night(PageCase):
    def setUp(self):
        self.page = home("night")

    def test_night_colours(self):
        bg = self.page.js("return getComputedStyle(document.body).backgroundColor;")
        self.assertLess(max(int(x) for x in bg[4:-1].split(",")), 40, f"night is dark, not {bg}")
        self.assertLooksLike("home-night", tolerance=0.004)


WELCOME = {
    "ai": {"claude": "ready", "codex": "get", "gemini": "ready", "grok": "get", "copilot": "signin"},
    "home": "",
    "ready": [
        {"id": "private", "label": "Personal Claude", "kind": "claude"},
        {"id": "personal-gemini", "label": "Personal Gemini", "kind": "gemini"},
    ],
    "local": {"installed": False, "running": False, "models": []},
    "permission_mode": "bypassPermissions",
    "codecs": {"added": False, "running": False},
}


class FirstStart(PageCase):
    """The welcome page, for people who added Kūhaku without its installer."""

    def setUp(self):
        self.page = home()
        self.page.js("openPage('welcome'); setWelcome(" + json.dumps(WELCOME) + "); return true;")
        self.page.wait(1500)  # the page has faded in

    def text(self):
        return self.page.js("return document.getElementById('page').innerText;")

    def sent(self, key):
        return [m for m in self.page.messages if isinstance(m, dict) and m.get("key") == key]

    def test_the_choices_are_there(self):
        t = self.text()
        for words in (
            "Welcome to Kūhaku",
            "YOUR AI",
            "OpenAI",
            "Gemini",
            "Get and sign in",
            "Grok",
            "Copilot",
            "Home uses",
            "Signed in",
            "Anything, without asking",
            "patents",
            "Add them",
            "Just ask",
        ):
            self.assertIn(words, t)
        self.assertLooksLike("welcome-day", tolerance=0.004)

    def test_getting_another_ai(self):
        self.page.click("[data-wel='ai'][data-v='codex']")
        self.assertEqual(self.sent("welcome-ai")[-1]["value"], "codex")

    def test_done_closes_and_remembers(self):
        self.page.click("[data-wel='done']")
        self.assertTrue(self.sent("welcome-done"))
        self.assertEqual(self.page.js("return document.getElementById('page').hidden;"), True)


class Pictures(PageCase):
    """The picture button opens Kūhaku's own picker: choose, look closer with Space, add with Enter."""

    def setUp(self):
        self.page = home()
        shots = ROOT / "uitests" / "shots"
        self.items = [
            {
                "path": f"/tmp/p{i}.png",
                "uri": (shots / "home-day.png").as_uri(),
                "name": f"Screenshot {i}.png",
                "t": T - i * 5000,
            }
            for i in range(7)
        ]
        self.page.js("document.getElementById('pic').click(); return true;")
        self.page.js(
            "setPictures("
            + json.dumps(
                {
                    "folder": "Screenshots",
                    "folders": ["Screenshots", "Pictures", "Downloads", "Other"],
                    "items": self.items,
                }
            )
            + "); return true;"
        )
        self.page.wait(300)

    def key(self, key):
        self.page.js(
            f"window.dispatchEvent(new KeyboardEvent('keydown', {{key: {json.dumps(key)}, bubbles: true, cancelable: true}})); return true;"
        )
        self.page.wait(150)

    def test_opening_asks_for_recent_pictures(self):
        self.assertIn({"type": "picture", "action": "list", "folder": "Screenshots", "path": ""}, self.page.messages)
        self.assertEqual(self.page.js("return document.querySelectorAll('#picks .pk').length;"), 7)

    def test_choose_two_and_add(self):
        self.key("x")
        self.key("ArrowRight")
        self.key("x")
        self.assertLooksLike("pictures-day", tolerance=0.01)
        self.key("Enter")
        self.assertIn({"type": "picture", "action": "add", "paths": ["/tmp/p0.png", "/tmp/p1.png"]}, self.page.messages)
        self.assertEqual(self.page.js("return document.getElementById('picks').hidden;"), True)

    def test_space_looks_closer_and_escape_steps_back(self):
        self.key(" ")
        self.assertEqual(self.page.js("return document.getElementById('peek').hidden;"), False)
        self.key("Escape")
        self.assertEqual(self.page.js("return document.getElementById('peek').hidden;"), True)
        self.assertEqual(self.page.js("return document.getElementById('picks').hidden;"), False)
        self.key("Escape")
        self.assertEqual(self.page.js("return document.getElementById('picks').hidden;"), True)

    def test_enter_without_choosing_adds_the_one_in_view(self):
        self.key("ArrowRight")
        self.key("Enter")
        self.assertIn({"type": "picture", "action": "add", "paths": ["/tmp/p1.png"]}, self.page.messages)

    def test_other_walks_folders_in_the_same_picker(self):
        self.page.click('#picks [data-folder="Other"]')
        self.assertIn({"type": "picture", "action": "list", "folder": "Other", "path": ""}, self.page.messages)
        home_dir = {
            "folder": "Other",
            "path": "/home/someone",
            "crumbs": [{"name": "Home", "path": "/home/someone"}],
            "folders": ["Screenshots", "Other"],
            "items": [{"path": "/home/someone/Documents", "name": "Documents", "dir": True}, *self.items[:2]],
        }
        self.page.js("setPictures(" + json.dumps(home_dir) + "); return true;")
        self.page.wait(200)
        self.assertLooksLike("pictures-other-day", tolerance=0.01)
        self.key("Enter")
        self.assertIn(
            {"type": "picture", "action": "list", "folder": "Other", "path": "/home/someone/Documents"},
            self.page.messages,
        )
        self.assertEqual(self.page.js("return document.getElementById('picks').hidden;"), False)

    def test_space_after_clicking_a_tab_still_looks_closer(self):
        self.page.click('#picks [data-folder="Pictures"]')
        self.page.js(
            "setPictures("
            + json.dumps({"folder": "Pictures", "folders": ["Pictures"], "items": self.items})
            + "); return true;"
        )
        self.page.wait(200)
        self.key(" ")
        self.assertEqual(self.page.js("return document.getElementById('peek').hidden;"), False)
        self.key("Enter")
        self.assertIn({"type": "picture", "action": "add", "paths": ["/tmp/p0.png"]}, self.page.messages)


class NetworkCard(PageCase):
    """The Wi‑Fi mark's card: Wi‑Fi, VPN and Bluetooth as tabs, one at a time."""

    def setUp(self):
        self.page = home()
        self.page.js("openPanel('wifi'); return true;")
        for action, data in [
            (
                "wifi",
                {
                    "enabled": True,
                    "networks": [
                        {"ssid": "Office", "active": True, "signal": 80, "band": "5 GHz", "secure": True, "known": True}
                    ],
                },
            ),
            ("vpn", {"vpns": [{"id": "nm:x", "name": "Work", "on": True, "kind": "nm", "note": ""}]}),
            (
                "bt",
                {
                    "present": True,
                    "on": True,
                    "devices": [{"mac": "AA", "name": "Headphones", "paired": True, "connected": False}],
                },
            ),
        ]:
            self.page.js(f"panelData({json.dumps({'action': action, 'data': data})}); return true;")
        self.page.wait(150)

    def shown(self):
        return self.page.js("return document.getElementById('panel').innerText;")

    def test_one_tab_at_a_time(self):
        self.assertEqual(
            self.page.js("return [...document.querySelectorAll('.ntab')].map(t => t.textContent);"),
            ["Wi‑Fi", "VPN", "Bluetooth"],
        )
        self.assertIn("Office", self.shown())
        self.assertNotIn("Headphones", self.shown())
        self.page.click('.ntab[data-v="bt"]')
        self.assertIn("Headphones", self.shown())
        self.assertNotIn("Office", self.shown())
        self.page.click('.ntab[data-v="vpn"]')
        self.assertIn("Work", self.shown())

    def test_the_vpn_is_named_on_the_connections_mark(self):
        self.page.js("closePanel(); setStatus({wifi: 'on', signal: 80, vpn: {name: 'Work', app: ''}}); return true;")
        self.assertIn("VPN Work", self.page.js("return document.getElementById('wifiBtn').title;"))
