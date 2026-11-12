"""kuhaku_log: report() writes each fault once per place and kind, into LOG_DIR, and never raises."""

import logging
import sys
import types
import unittest
from unittest import mock

import kuhaku_log
from tests.util import TempDirCase


def reset_log_module():
    lg = logging.getLogger("kuhaku")
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()
    kuhaku_log._seen.clear()
    kuhaku_log._logger = None


class LogCase(TempDirCase):
    def setUp(self):
        super().setUp()
        reset_log_module()
        self.addCleanup(reset_log_module)
        self.dir = self.tmp / "logs"
        p = mock.patch.object(kuhaku_log, "LOG_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)
        a = mock.patch.object(kuhaku_log, "app_name", return_value="testapp")
        a.start()
        self.addCleanup(a.stop)

    def text(self):
        for h in logging.getLogger("kuhaku").handlers:
            h.flush()
        f = self.dir / "testapp.log"
        return f.read_text() if f.exists() else ""


class Report(LogCase):
    def fail_here(self, exc):
        try:
            raise exc
        except Exception:
            kuhaku_log.report()

    def test_writes_to_patched_dir(self):
        self.fail_here(ValueError("bad value"))
        text = self.text()
        self.assertIn("ValueError: bad value", text)
        self.assertIn("test_log.py:", text)
        self.assertIn("fail_here", text)
        self.assertIn("WARNING", text)

    def test_dedups_per_place_and_kind(self):
        for i in range(5):
            self.fail_here(ValueError(f"v{i}"))  # same line, same kind: once
        self.fail_here(KeyError("k"))  # same line, other kind: again
        text = self.text()
        self.assertEqual(text.count("ValueError"), 1)
        self.assertIn("v0", text)
        self.assertNotIn("v1", text)
        self.assertEqual(text.count("KeyError"), 1)

    def test_other_place_is_reported_again(self):
        self.fail_here(ValueError("one"))
        try:
            raise ValueError("two")
        except ValueError:
            kuhaku_log.report()
        self.assertEqual(self.text().count("ValueError"), 2)

    def test_what_is_included(self):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            kuhaku_log.report("reading the battery")
        self.assertIn("reading the battery: RuntimeError: boom", self.text())

    def test_traceback_only_for_unexpected_errors(self):
        self.fail_here(OSError("gone"))
        self.assertNotIn("Traceback", self.text())
        self.assertNotIn('File "', self.text())
        self.fail_here(ZeroDivisionError("zero"))
        self.assertIn('File "', self.text())  # a short traceback for real bugs

    def test_outside_except_block(self):
        kuhaku_log.report()
        self.assertIn("error: None", self.text())

    def test_never_raises(self):
        with mock.patch.object(kuhaku_log, "logger", side_effect=RuntimeError("log broken")):
            try:
                raise ValueError("x")
            except ValueError:
                kuhaku_log.report()  # must not raise
        with mock.patch.object(kuhaku_log, "logger", side_effect=RuntimeError("log broken")):
            kuhaku_log.note("hello")

    def test_unwritable_dir_falls_back_quietly(self):
        blocker = self.tmp / "file"
        blocker.write_text("x")
        with mock.patch.object(kuhaku_log, "LOG_DIR", blocker / "logs"):
            self.fail_here(ValueError("x"))  # mkdir fails: a NullHandler, no exception
        self.assertIsInstance(logging.getLogger("kuhaku").handlers[0], logging.NullHandler)

    def test_note(self):
        kuhaku_log.note("something worth knowing")
        self.assertIn("INFO something worth knowing", self.text())

    def test_does_not_propagate_to_root(self):
        self.fail_here(ValueError("x"))
        self.assertFalse(logging.getLogger("kuhaku").propagate)


class AppName(unittest.TestCase):
    def test_from_main_file(self):
        fake = types.SimpleNamespace(__file__="/opt/kuhaku/kuhaku_system.py")
        with mock.patch.dict(sys.modules, {"__main__": fake}):
            self.assertEqual(kuhaku_log.app_name(), "kuhaku-system")

    def test_without_main_file(self):
        with mock.patch.dict(sys.modules, {"__main__": types.SimpleNamespace()}), mock.patch.object(sys, "argv", [""]):
            self.assertEqual(kuhaku_log.app_name(), "kuhaku")


if __name__ == "__main__":
    unittest.main()
