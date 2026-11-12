"""The home screen's page, used like a person would: ask, see the answer, tabs, day and night."""

import json

from uitests.harness import Page, PageCase


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


class Night(PageCase):
    def setUp(self):
        self.page = home("night")

    def test_night_colours(self):
        bg = self.page.js("return getComputedStyle(document.body).backgroundColor;")
        self.assertLess(max(int(x) for x in bg[4:-1].split(",")), 40, f"night is dark, not {bg}")
        self.assertLooksLike("home-night", tolerance=0.004)
