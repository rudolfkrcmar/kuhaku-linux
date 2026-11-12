"""kiroku.py: the history of jobs, read from jobs.jsonl, the old claude.log, the tabs and Claude
Code's transcripts. No window is made; every path points into a temp folder."""
import csv
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from tests.util import TempDirCase

import kiroku


class Helpers(unittest.TestCase):
    def test_model_name(self):
        self.assertEqual(kiroku.model_name("claude-opus-5-5"), "Opus 5.5")
        self.assertEqual(kiroku.model_name("claude-haiku-4-5-20251001"), "Haiku 4.5")
        self.assertEqual(kiroku.model_name(None), "")
        self.assertEqual(kiroku.model_name("20251001"), "")

    def test_main_model(self):
        models = {"claude-haiku-4-5": {"costUSD": 0.01}, "claude-opus-5-5": {"costUSD": 0.5}, "x": {}}
        self.assertEqual(kiroku.main_model(models), "claude-opus-5-5")
        self.assertEqual(kiroku.main_model({}, "fallback"), "fallback")
        self.assertIsNone(kiroku.main_model(None))

    def test_tokens_of(self):
        models = {"a": {"inputTokens": 1, "outputTokens": 2, "cacheReadInputTokens": 3, "cacheCreationInputTokens": 4},
                  "b": {"inputTokens": 10}}
        self.assertEqual(kiroku.tokens_of(models), {"in": 11, "out": 2, "cache_read": 3, "cache_write": 4})
        self.assertEqual(kiroku.tokens_of(None), {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0})

    def test_per_run_cost(self):
        prev = {}
        runs = [{"session_id": "s", "cost": 1.0}, {"session_id": "s", "cost": 1.5}, {"session_id": "s", "cost": 0.2},
                {"session_id": "t", "cost": 0.3}, {"session_id": None, "cost": 9}, {"session_id": "s"}]
        for r in runs:
            kiroku.per_run_cost(r, prev)
        self.assertEqual([r.get("cost") for r in runs], [1.0, 0.5, 0.2, 0.3, 9, None])

    def test_text_of(self):
        self.assertEqual(kiroku.text_of("plain"), "plain")
        self.assertEqual(kiroku.text_of([{"type": "text", "text": "a"}, {"type": "image"}, "x",
                                         {"type": "text", "text": "b"}]), "a\nb")
        self.assertEqual(kiroku.text_of(None), "")

    def test_real_prompt(self):
        def user(content, **kw):
            return {"type": "user", "message": {"content": content}, **kw}
        self.assertEqual(kiroku.real_prompt(user("  fix the bug  ")), "fix the bug")
        self.assertEqual(kiroku.real_prompt(user([{"type": "text", "text": "hi"}])), "hi")
        for e in [user("x", isMeta=True), user("x", isSidechain=True), {"type": "assistant", "message": {}},
                  user([{"type": "tool_result", "content": "out"}]), user("<command-name>/clear</command-name>"),
                  user("<local-command-stdout>"), user("<system-reminder>x"), user("Caveat: the messages"),
                  user("   "), {"type": "user"}]:
            with self.subTest(e=e):
                self.assertIsNone(kiroku.real_prompt(e))


class KirokuCase(TempDirCase):
    def setUp(self):
        super().setUp()
        self.state = self.tmp / "state"
        self.state.mkdir()
        for obj, name, value in [(kiroku, "STATE", self.state), (kiroku, "LOG_FILE", self.state / "claude.log"),
                                 (kiroku, "FIFO", self.state / "kuhaku.fifo"), (kiroku, "HOME", self.tmp),
                                 (kiroku, "MAIL_DIR", self.tmp / "mail"),
                                 (kiroku.jobs, "RUNS_FILE", self.state / "jobs.jsonl")]:
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.projects = self.tmp / "claude/projects"
        p = mock.patch.object(kiroku, "transcript_dirs", side_effect=lambda: [(self.projects, None)])
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.dict(kiroku._sessions, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def transcript(self, sid, entries, project="p"):
        f = self.projects / project / f"{sid}.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(e if isinstance(e, str) else json.dumps(e) for e in entries) + "\n")
        return f


def user(text, ts="2026-11-10T10:00:00Z", **kw):
    return {"type": "user", "timestamp": ts, "message": {"content": text}, **kw}


def reply(text, mid, ts="2026-11-10T10:00:05Z", out=10, tool=False, model="claude-opus-5-5", **kw):
    content = [{"type": "text", "text": text}] if text else []
    if tool:
        content.append({"type": "tool_use", "name": "Bash", "input": {}})
    return {"type": "assistant", "timestamp": ts, "requestId": "r" + mid, **kw,
            "message": {"id": mid, "model": model, "content": content,
                        "usage": {"input_tokens": 1, "output_tokens": out, "cache_read_input_tokens": 2,
                                  "cache_creation_input_tokens": 3}}}


class Transcripts(KirokuCase):
    def test_conversation_summary(self):
        self.transcript("s1", [
            {"type": "summary", "entrypoint": "cli", "cwd": "/home/x/Projects"},
            user("<system-reminder>ignore"), user("make the tests"),
            reply("", "m1", tool=True), reply("", "m1", tool=True),           # the same reply twice: once
            {"type": "user", "timestamp": "2026-11-10T10:00:06Z",
             "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
            reply("Done.", "m2", ts="2026-11-10T10:01:00Z", out=30),
            reply("helper", "m3", model="claude-haiku-4-5", out=5),
            reply("side", "m4", isSidechain=True, out=999),
            {"type": "cost-state", "startTime": 1, "totalCostUSD": 0.12345, "totalDuration": 42000},
            {"type": "cost-state", "startTime": 2, "totalCostUSD": 0.2, "totalDuration": 50000},
            "not json", user("and again"),
        ])
        runs = kiroku.from_transcripts(set())
        self.assertEqual(len(runs), 1)
        r = runs[0]
        self.assertEqual(r["question"], "make the tests")
        self.assertEqual(r["turns_asked"], 2)
        self.assertEqual(r["answer"], "helper")
        self.assertEqual(r["client"], "Terminal")
        self.assertEqual(r["model"], "claude-opus-5-5")
        self.assertEqual(r["tokens"], {"in": 3, "out": 45, "cache_read": 6, "cache_write": 9})
        self.assertEqual(r["cost"], 0.2)
        self.assertEqual(r["seconds"], 50.0)
        self.assertEqual(r["start"], datetime.fromisoformat("2026-11-10T10:00:00+00:00").timestamp())

    def test_clients_known_and_empty(self):
        self.transcript("mail", [{"entrypoint": "sdk-py", "cwd": str(self.tmp / "mail/acct")}, user("sort my mail")])
        self.transcript("known", [user("already listed")])
        self.transcript("empty", [user("<command-name>/clear</command-name>")])
        runs = {r["session_id"]: r for r in kiroku.from_transcripts({"known"})}
        self.assertEqual(set(runs), {"mail"})
        self.assertEqual(runs["mail"]["client"], "Tegami")
        self.assertIsNone(runs["mail"]["cost"])
        self.assertEqual(runs["mail"]["seconds"], 0)

    def test_thread(self):
        self.transcript("t", [user("first question"), reply("", "a", tool=True), reply("", "b", tool=True),
                              reply("Here it is.", "c"), reply("And more.", "d"), user("thanks"),
                              reply("", "e", tool=True), user("bye")])
        items = kiroku.thread("t")
        self.assertEqual([i["who"] for i in items], ["you", "work", "ai", "you", "work", "you"])
        self.assertEqual(items[1]["steps"], 2)
        self.assertEqual(items[2]["text"], "Here it is.\n\nAnd more.")
        self.assertEqual(items[2]["model"], "Opus 5.5")
        self.assertIsNone(kiroku.thread("missing"))


class OldLogAndJobs(KirokuCase):
    def test_old_log(self):
        sid = "abc"
        lines = [
            "=== 2026-01-02 10:00:00  [tab 3]  Fix the",
            "printer please",
            json.dumps({"type": "system", "subtype": "init", "session_id": sid, "model": "claude-opus-5-5",
                        "cwd": "/w"}),
            json.dumps({"type": "assistant", "session_id": sid}),
            json.dumps({"type": "result", "session_id": sid, "total_cost_usd": 0.5, "num_turns": 3,
                        "duration_ms": 12000, "result": "Fixed.", "usage": {"input_tokens": 7, "output_tokens": 8},
                        "modelUsage": {"claude-opus-5-5": {"costUSD": 0.5}}}),
            "=== 2026-01-02 11:00:00  Never started",
            "{garbage",
            "=== 2030-01-01 00:00:00  after jobs.jsonl began",
        ]
        kiroku.LOG_FILE.write_text("\n".join(lines) + "\n")
        before = datetime(2029, 1, 1).timestamp()
        runs = kiroku.from_old_log(before, {})
        self.assertEqual(len(runs), 2)
        a, b = runs
        self.assertEqual(a["question"], "Fix the\nprinter please")
        self.assertEqual(a["tab"], "3")
        self.assertEqual((a["outcome"], a["cost"], a["turns"], a["seconds"]), ("done", 0.5, 3, 12.0))
        self.assertEqual(a["model"], "claude-opus-5-5")
        self.assertEqual(a["cwd"], "/w")
        self.assertEqual(a["tokens"]["out"], 8)
        self.assertNotIn("events", a)
        self.assertEqual((b["question"], b["outcome"], b["tab"]), ("Never started", "stopped", None))

    def test_jobs_file_and_start(self):
        self.assertGreater(kiroku.jobs_start(), datetime(2026, 1, 1).timestamp())    # no file: "from now"
        recs = [{"start": 100, "session_id": "s", "cost": 1.0, "models": {"m": {"costUSD": 1, "outputTokens": 4}}},
                {"start": 200, "session_id": "s", "cost": 1.25, "model": "fallback"}]
        kiroku.jobs.RUNS_FILE.write_text("\n".join(json.dumps(r) for r in recs) + "\nnot json\n")
        self.assertEqual(kiroku.jobs_start(), 100)
        runs = kiroku.from_jobs_file({})
        self.assertEqual([r["cost"] for r in runs], [1.0, 0.25])
        self.assertEqual([r["model"] for r in runs], ["m", "fallback"])
        self.assertEqual(runs[0]["tokens"]["out"], 4)

    def test_tabs_and_all_runs(self):
        (self.state / "tabs.json").write_text(json.dumps({"tabs": [
            {"id": "1", "status": "running", "title": "Printer", "question": "fix it"},
            {"id": "2", "status": "queued", "title": "Later", "question": "q", "queued": 50, "session_id": "s2"},
            {"id": "3", "status": "idle"},
            {"id": "4", "status": "queued", "anon": True, "session_id": "anon-s"}]}))
        runs = kiroku.from_tabs([{"start": 10, "tab": "1", "outcome": "stopped"}])
        self.assertEqual(runs[0]["outcome"], "running")
        self.assertEqual(runs[0]["title"], "Printer")
        self.assertEqual([(r["tab"], r["outcome"]) for r in runs[1:]], [("2", "waiting")])
        self.assertEqual(kiroku.anonymous_sessions(), {"anon-s"})

        self.transcript("anon-s", [user("secret")])
        self.transcript("s2", [user("dup")])
        self.transcript("s9", [user("from the terminal", ts="2026-11-10T10:00:00Z")])
        kiroku.jobs.RUNS_FILE.write_text(json.dumps({"start": 300, "session_id": "s9", "cost": 0.1}) + "\n")
        out = kiroku.all_runs()
        sids = [r["session_id"] for r in out]
        self.assertNotIn("anon-s", sids)
        self.assertEqual(sids.count("s2"), 1)
        self.assertEqual(sids.count("s9"), 1)       # the job line already has it: no second copy
        self.assertEqual([r["start"] for r in out], sorted((r["start"] for r in out), reverse=True))
        self.assertTrue(all(r["thread"] == 1 for r in out))

    def test_missing_tabs_file(self):
        self.assertEqual(kiroku.from_tabs([{"x": 1}]), [{"x": 1}])
        self.assertEqual(kiroku.anonymous_sessions(), set())


class ExportAndFifo(KirokuCase):
    def test_export_csv(self):
        path = kiroku.export_csv([{"start": 1_800_000_000, "seconds": 3, "client": "Kūhaku", "question": "a, \"b\"",
                                   "tokens": {"in": 1, "out": 2}, "session_id": "s"}])
        self.assertEqual(path.parent, self.tmp / "Downloads")
        with path.open() as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[0][0], "started")
        self.assertEqual(rows[1][6], 'a, "b"')
        self.assertEqual(rows[1][9:11], ["1", "2"])
        self.assertEqual(len(rows[0]), len(rows[1]))

    def test_send_home_without_listener(self):
        self.assertFalse(kiroku.send_home("hello"))


if __name__ == "__main__":
    unittest.main()
