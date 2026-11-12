"""The home screen's message tables: every action has a handler, and replies go out the right way."""
import re
import unittest
from pathlib import Path
from unittest import mock

import kuhaku
import kuhaku_panels as panels

ROOT = Path(__file__).resolve().parent.parent


class Fake(panels.Panels):
    def __init__(self):
        self.sent, self.status = [], 0

    def send(self, fn, payload):
        self.sent.append((fn, payload))

    def push_status(self):
        self.status += 1

    def read_prefs(self):
        return {"prefs": True}

    def write_pref(self, key, value):
        self.written = (key, value)


def run_idle(fn, *args):
    fn(*args)
    return 0


class PanelTable(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(panels.GLib, "idle_add", side_effect=run_idle)
        p.start()
        self.addCleanup(p.stop)
        self.k = Fake()

    def test_every_action_the_page_sends_has_a_handler(self):
        js = "".join(f.read_text(encoding="utf-8") for f in (ROOT / "home").glob("*.js"))
        asked = set(re.findall(r"type:\s*'panel',\s*action:\s*'([a-z-]+)'", js))
        self.assertGreater(len(asked), 10)
        known = set(panels.PANEL)
        missing = asked - known
        self.assertEqual(missing, set(), "the page asks for actions nobody answers")

    def test_reply_under_its_own_action(self):
        with mock.patch.object(panels, "battery_details", return_value={"level": 50}):
            self.k.panel({"action": "battery"})
        self.assertEqual(self.k.sent, [("panelData", {"action": "battery", "data": {"level": 50}})])
        self.assertEqual(self.k.status, 1)

    def test_reply_under_another_action(self):
        self.k.panel({"action": "pref", "key": "theme", "value": "night"})
        self.assertEqual(self.k.written, ("theme", "night"))
        self.assertEqual(self.k.sent, [("panelData", {"action": "prefs", "data": {"prefs": True}})])

    def test_quiet_sends_nothing(self):
        self.k.panel({"action": "pref", "key": "x", "value": 1, "quiet": True})
        with mock.patch.object(panels, "spawn") as spawn:
            self.k.panel({"action": "focus", "xid": "0x1a2b"})
            self.k.panel({"action": "focus", "xid": "1; rm -rf ~"})
        spawn.assert_called_once_with(["wmctrl", "-i", "-a", "0x1a2b"])
        self.assertEqual(self.k.sent, [])

    def test_unknown_action_is_noted_not_answered(self):
        self.k.panel({"action": "no-such-thing"})
        self.assertEqual(self.k.sent, [])

    def test_bluetooth_only_takes_real_addresses(self):
        with mock.patch.object(panels, "run") as run, \
                mock.patch.object(panels, "bluetooth_state", return_value={}):
            self.k.panel({"action": "bt-forget", "mac": "$(reboot)"})
            run.assert_not_called()
            self.k.panel({"action": "bt-forget", "mac": "AA:BB:CC:DD:EE:FF"})
            run.assert_called_once_with(["bluetoothctl", "remove", "AA:BB:CC:DD:EE:FF"], timeout=10)


class MessageTable(unittest.TestCase):
    def test_every_message_the_page_posts_has_a_listener(self):
        js = "".join(f.read_text(encoding="utf-8") for f in (ROOT / "home").glob("*.js"))
        posted = set(re.findall(r"native\(\{\s*type:\s*'([A-Za-z]+)'", js))
        self.assertTrue(posted)
        self.assertEqual(posted - set(kuhaku.MESSAGES), set())

    def test_draft_is_kept_with_its_tab(self):
        tab = {}
        k = mock.Mock(tabs=mock.Mock(get=lambda t: tab if t == "t1" else None, current="t1"))
        kuhaku.Kuhaku.save_draft(k, {"tab": "t1", "text": "hello", "next": "then"})
        self.assertEqual(tab, {"draft": "hello", "next": "then"})
        k.tabs.save.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class MiruMessages(unittest.TestCase):
    def test_every_message_miru_posts_has_a_listener(self):
        import miru
        js = (ROOT / "miru" / "miru.js").read_text(encoding="utf-8")
        posted = set(re.findall(r"send\(\{\s*type:\s*'([A-Za-z-]+)'", js))
        menu = set(re.findall(r"\['([a-z]+)', '[^']*'(?:, 'bad')?\]", js)) - {"open", "openapp", "rename", "newfolder",
                                                                               "terminal", "hidden"}
        self.assertTrue(posted)
        missing = {p for p in posted | menu if p not in miru.MESSAGES and not p.startswith("cloud-")}
        self.assertEqual(missing, set())
