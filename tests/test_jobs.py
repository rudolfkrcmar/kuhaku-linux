"""jobs.py: the tabs and their line, room to run, "at 7:00 …", labels, and what a job cost."""

import json
import os
import time
import unittest
from datetime import datetime
from unittest import mock

from tests.util import TempDirCase

import jobs


class PlanRoom(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(jobs.plan_room(None), ("open", None))
        self.assertEqual(jobs.plan_room({}), ("open", None))
        self.assertEqual(jobs.plan_room({"session": {"percent": 50, "resets": "s"}}), ("open", "s"))
        self.assertEqual(jobs.plan_room({"session": {"percent": 90, "resets": "s"}}), ("tight", "s"))
        self.assertEqual(
            jobs.plan_room({"session": {"percent": 99.5, "resets": "s"}, "week": {"percent": 10, "resets": "w"}}),
            ("closed", "s"),
        )
        self.assertEqual(
            jobs.plan_room({"session": {"percent": 20, "resets": "s"}, "week": {"percent": 100, "resets": "w"}}),
            ("closed", "w"),
        )
        self.assertEqual(jobs.plan_room({"session": {"percent": None}, "week": None}), ("open", None))


class Slots(unittest.TestCase):
    def slots(self, running, max_jobs, limits=None, mem=8000):
        with mock.patch.object(jobs, "mem_available_mb", return_value=mem):
            return jobs.slots(running, max_jobs, limits)

    def test_memory_bounds_room(self):
        # (8000 - 1200) // 450 = 15 by memory; the cap decides
        self.assertEqual(self.slots(0, 3), (3, None, None))
        self.assertEqual(self.slots(2, 3), (1, None, None))
        self.assertEqual(self.slots(3, 3), (0, "busy", None))
        self.assertEqual(self.slots(1, 10, mem=1200 + 450 * 2), (2, None, None))
        self.assertEqual(self.slots(2, 10, mem=1000), (0, "memory", None))

    def test_one_job_always_runs(self):
        self.assertEqual(self.slots(0, 3, mem=100), (1, None, None))

    def test_plan(self):
        tight = {"session": {"percent": 95, "resets": "r"}}
        self.assertEqual(self.slots(0, 4, tight), (1, None, "r"))
        self.assertEqual(self.slots(1, 4, tight), (0, "plan", "r"))
        closed = {"week": {"percent": 100, "resets": "w"}}
        self.assertEqual(self.slots(0, 4, closed), (0, "plan", "w"))

    def test_mem_available_reads_proc(self):
        self.assertIsInstance(jobs.mem_available_mb(), int)
        with mock.patch.object(jobs.Path, "read_text", side_effect=OSError):
            self.assertEqual(jobs.mem_available_mb(), 4096)


class ParseWhen(unittest.TestCase):
    def at(self, text):
        ts, rest = jobs.parse_when(text)
        return (datetime.fromtimestamp(ts).strftime("%H:%M") if ts else None), rest

    def test_times(self):
        cases = [
            ("at 7:00 sort my mail", ("07:00", "sort my mail")),
            ("at 7 sort", ("07:00", "sort")),
            ("@9.30 call", ("09:30", "call")),
            ("At 7pm, call mom", ("19:00", "call mom")),
            ("at 12am backup", ("00:00", "backup")),
            ("at 12pm lunch", ("12:00", "lunch")),
            ("at 11:05 AM: tea", ("11:05", "tea")),
            ("at 23:59 last", ("23:59", "last")),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(self.at(text), want)

    def test_not_a_time(self):
        for text in ["sort my mail at 7", "at 25:00 x", "at 7:75 x", "at seven x", "at 7:00", "", "attic 7 x"]:
            with self.subTest(text=text):
                self.assertEqual(jobs.parse_when(text), (None, text))

    def test_always_in_the_future(self):
        ts, _ = jobs.parse_when("at 0:00 x")
        self.assertGreater(ts, time.time())
        self.assertLessEqual(ts - time.time(), 86400 + 3600)


class Labels(unittest.TestCase):
    def test_model_label(self):
        self.assertEqual(jobs.model_label("claude-opus-5-5"), "Opus 5.5")
        self.assertEqual(jobs.model_label("claude-haiku-4-5-20251001"), "Haiku 4.5")
        self.assertEqual(jobs.model_label("claude-sonnet-4"), "Sonnet 4")
        self.assertEqual(jobs.model_label("opus"), "Opus")
        self.assertEqual(jobs.model_label(""), "")
        self.assertEqual(jobs.model_label(None), "")

    def test_title_of(self):
        self.assertEqual(jobs.title_of("Sort my mail"), "Sort my mail")
        self.assertEqual(jobs.title_of("  fix\n the   bug  "), "fix the bug")
        self.assertEqual(jobs.title_of("one two three four five"), "one two three four…")
        long = "a" * 40
        self.assertEqual(jobs.title_of(long), "a" * 32 + "…")


class TabsTest(TempDirCase):
    def tabs(self, data=None, first=None):
        f = self.tmp / "tabs.json"
        if data is not None:
            f.write_text(json.dumps(data))
        return jobs.Tabs(f, first)

    def test_fresh(self):
        t = self.tabs(first="sess-1")
        self.assertEqual(len(t.tabs), 1)
        self.assertEqual(t.tabs[0]["id"], "1")
        self.assertEqual(t.tabs[0]["session_id"], "sess-1")
        self.assertEqual(t.current, "1")

    def test_broken_file(self):
        (self.tmp / "tabs.json").write_text("{nope")
        self.assertEqual(len(jobs.Tabs(self.tmp / "tabs.json").tabs), 1)

    def test_new_close_and_current(self):
        t = self.tabs()
        a, b = t.new(), t.new()
        self.assertEqual([x["id"] for x in t.tabs], ["1", "2", "3"])
        self.assertEqual(t.current, "3")
        t.close("3")
        self.assertEqual(t.current, "2")  # the one to its left
        t.current = "1"
        t.close("2")
        self.assertEqual(t.current, "1")  # closing another keeps the current
        t.close("1")
        self.assertEqual(len(t.tabs), 1)  # never no tab at all
        self.assertEqual(t.current, t.tabs[0]["id"])
        t.close("nope")
        saved = json.loads((self.tmp / "tabs.json").read_text())
        self.assertEqual(saved["current"], t.current)
        self.assertEqual(len(saved["tabs"]), 1)

    def test_restart_puts_running_back_in_line(self):
        data = {
            "current": "9",
            "tabs": [
                {
                    **jobs.Tabs.blank(object.__new__(jobs.Tabs)),
                    "id": "1",
                    "status": "running",
                    "session_id": "s",
                    "question": "do it",
                },
                {
                    **jobs.Tabs.blank(object.__new__(jobs.Tabs)),
                    "id": "2",
                    "status": "running",
                    "session_id": None,
                    "question": "fresh",
                },
                {
                    **jobs.Tabs.blank(object.__new__(jobs.Tabs)),
                    "id": "3",
                    "status": "running",
                    "session_id": "s",
                    "question": "Kūhaku restarted while you were working on this. Continue where you stopped: x",
                },
            ],
        }
        t = self.tabs(data)
        self.assertEqual([x["status"] for x in t.tabs], ["queued"] * 3)
        self.assertTrue(t.tabs[0]["question"].startswith("Kūhaku restarted"))
        self.assertTrue(t.tabs[0]["question"].endswith("do it"))
        self.assertEqual(t.tabs[1]["question"], "fresh")
        self.assertEqual(t.tabs[2]["question"].count("Kūhaku restarted"), 1)
        self.assertEqual(t.current, "1")  # an unknown current falls back to the first

    def test_waiting_order_and_public(self):
        t = self.tabs()
        now = time.time()
        rows = [
            ("1", "queued", jobs.NORMAL, 10, None),
            ("2", "queued", jobs.FIRST, 30, None),
            ("3", "queued", jobs.LATER, 5, None),
            ("4", "queued", jobs.FIRST, 20, None),
            ("5", "queued", jobs.FIRST, 1, now + 3600),
            ("6", "running", jobs.FIRST, 0, None),
            ("7", "idle", jobs.FIRST, 0, None),
            ("8", "queued", jobs.NORMAL, 50, now - 5),
        ]
        t.tabs = [{**t.blank(), "id": i, "status": s, "priority": p, "queued": q, "at": at} for i, s, p, q, at in rows]
        self.assertEqual([x["id"] for x in t.waiting()], ["4", "2", "1", "8", "3"])
        self.assertEqual([x["id"] for x in t.running()], ["6"])
        pub = t.public()
        places = {x["id"]: x["place"] for x in pub["tabs"]}
        self.assertEqual(places, {"1": 3, "2": 2, "3": 5, "4": 1, "5": None, "6": None, "7": None, "8": 4})
        self.assertNotIn("question", pub["tabs"][0])
        self.assertNotIn("session_id", pub["tabs"][0])


class Costs(TempDirCase):
    def test_energy(self):
        self.assertEqual(jobs.energy_wh(3600, 10, [5, 7]), (6.0, "battery"))
        self.assertEqual(jobs.energy_wh(3600, 3600, []), (4.5, "estimate"))
        self.assertEqual(jobs.energy_wh(3600, None, None), (None, None))

    def test_cpu_seconds_of_this_process(self):
        secs = jobs.cpu_seconds(os.getpid())
        self.assertIsInstance(secs, float)
        self.assertGreaterEqual(secs, 0)
        self.assertIsNone(jobs.cpu_seconds(-1))

    def test_cpu_seconds_parse(self):
        stat = "123 (a (weird) name) S " + " ".join(str(i) for i in range(1, 30))
        with mock.patch.object(jobs.Path, "read_text", return_value=stat):
            # fields after the name: S then 1..; utime..cstime are 11..14 after the state (12, 13, 14, 15)
            self.assertEqual(jobs.cpu_seconds(123), (11 + 12 + 13 + 14) / 100)

    def test_battery_watts(self):
        bat = self.tmp / "BAT0"
        bat.mkdir()
        (bat / "status").write_text("Discharging\n")
        (bat / "current_now").write_text("1000000")
        (bat / "voltage_now").write_text("8000000")
        with mock.patch.object(jobs, "BATTERY", bat):
            self.assertEqual(jobs.battery_watts(), 8.0)
            (bat / "status").write_text("Charging\n")
            self.assertIsNone(jobs.battery_watts())
        with mock.patch.object(jobs, "BATTERY", self.tmp / "none"):
            self.assertIsNone(jobs.battery_watts())

    def test_erase_session(self):
        sid = "0123abcd-0000-1111-2222-333344445555"
        root = self.tmp / "claude"
        files = [root / f"projects/p/{sid}.jsonl", root / f"todos/{sid}-agent.json", root / f"debug/{sid}.txt"]
        dirs = [root / f"projects/p/{sid}", root / f"file-history/{sid}", root / f"session-env/{sid}"]
        keep = root / "projects/p/other.jsonl"
        for d in dirs:
            (d / "inner").mkdir(parents=True)
        for f in files + [keep]:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x")
        jobs.erase_session(root, "../../etc")  # not a session id: nothing happens
        jobs.erase_session(root, None)
        self.assertTrue(all(f.exists() for f in files))
        jobs.erase_session(root, sid)
        self.assertFalse(any(p.exists() for p in files + dirs))
        self.assertTrue(keep.exists())

    def test_log_run(self):
        with mock.patch.object(jobs, "RUNS_FILE", self.tmp / "jobs.jsonl"):
            jobs.log_run({"a": 1})
            jobs.log_run({"b": 2})
            lines = (self.tmp / "jobs.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(x) for x in lines], [{"a": 1}, {"b": 2}])
        with mock.patch.object(jobs, "RUNS_FILE", self.tmp / "missing/dir/jobs.jsonl"):
            jobs.log_run({"c": 3})  # quietly nothing


if __name__ == "__main__":
    unittest.main()
