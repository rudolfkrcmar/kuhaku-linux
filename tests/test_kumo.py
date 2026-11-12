"""kumo.py: accounts and their folders, the chosen-folders and status files, describe(), and
(when rclone is installed) a real two-way sync against a local folder posing as Dropbox."""
import json
import os
import shutil
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.util import TempDirCase, fake_completed

import kumo

REAL_RCLONE = Path(os.environ.get("REAL_HOME_FOR_TESTS") or "/home/ruda") / ".local/bin/rclone"


class KumoCase(TempDirCase):
    """Every path kumo uses points into a fresh temp folder."""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        conf, state = self.home / ".config/kuhaku", self.home / ".local/state/kuhaku"
        values = {"HOME": self.home, "CONF_DIR": conf, "STATE_DIR": state, "RCLONE_CONF": conf / "rclone.conf",
                  "CHOICES": conf / "cloud.json", "STATUS": state / "cloud.json", "TRIGGER": state / "cloud.now",
                  "LOCK": state / "cloud.lock", "LOG": state / "cloud.log"}
        for name, value in values.items():
            p = mock.patch.object(kumo, name, value)
            p.start()
            self.addCleanup(p.stop)
        kinds = {k: {**v} for k, v in kumo.KINDS.items()}
        kinds["dropbox"]["local"] = self.home / "Dropbox"
        kinds["drive"]["local"] = self.home / "Google Drive"
        p = mock.patch.dict(kumo.KINDS, kinds)
        p.start()
        self.addCleanup(p.stop)

    def remotes(self, *keys, extra=""):
        kumo.RCLONE_CONF.parent.mkdir(parents=True, exist_ok=True)
        kumo.RCLONE_CONF.write_text("".join(f"[kuhaku-{k}]\ntype = dropbox\ntoken = x\n\n" for k in keys) + extra)

    def choices(self, data):
        kumo._save(kumo.CHOICES, data)

    def status_file(self, data):
        kumo._save(kumo.STATUS, data)


class Tidy(unittest.TestCase):
    def test_parent_brings_children(self):
        self.assertEqual(kumo.tidy(["Work", "Work/2024", "Work/2024/Jan", "Photos"]), ["Photos", "Work"])

    def test_slashes_blanks_and_duplicates(self):
        self.assertEqual(kumo.tidy(["/a/", "a", "", "/"]), ["a"])

    def test_similar_names_are_not_children(self):
        self.assertEqual(kumo.tidy(["Work", "Workshop", "Work Notes"]), ["Work", "Work Notes", "Workshop"])

    def test_sorted_and_empty(self):
        self.assertEqual(kumo.tidy([]), [])
        self.assertEqual(kumo.tidy(["b", "a/x", "c"]), ["a/x", "b", "c"])


class Accounts(KumoCase):
    def test_first_accounts_always_listed(self):
        accts = kumo.accounts()
        self.assertEqual(list(accts), ["dropbox", "drive"])
        self.assertEqual(accts["dropbox"]["name"], "Dropbox")
        self.assertEqual(accts["drive"]["name"], "Google Drive")
        self.assertEqual(accts["dropbox"]["remote"], "kuhaku-dropbox")
        self.assertEqual(accts["dropbox"]["local"], self.home / "Dropbox")
        self.assertEqual(accts["drive"]["flags"], ["--drive-skip-gdocs"])
        self.assertEqual(accts["drive"]["conf"], ["scope", "drive"])

    def test_further_accounts_in_order(self):
        self.remotes("drive-2", "dropbox-3", "dropbox", "dropbox-2", "kuhaku-other", "dropbox-1")
        self.assertEqual(list(kumo.accounts()), ["dropbox", "dropbox-2", "dropbox-3", "drive", "drive-2"])

    def test_bad_keys(self):
        for key in ["", None, "onedrive", "dropbox-1", "dropbox-0", "dropbox-01", "dropbox-x", "Dropbox", "drive-2-3"]:
            with self.subTest(key=key):
                self.assertIsNone(kumo.account(key))
        self.assertIsNotNone(kumo.account("dropbox-10"))

    def test_names_and_local_folders(self):
        self.remotes("dropbox", "dropbox-2", "dropbox-3")
        self.choices({"dropbox": {"label": "Personal"}, "dropbox-2": {"label": "Work"}})
        a = kumo.accounts()
        self.assertEqual(a["dropbox"]["name"], "Dropbox (Personal)")     # labelled once there are several
        self.assertEqual(a["dropbox-2"]["name"], "Dropbox (Work)")
        self.assertEqual(a["dropbox-2"]["local"], self.home / "Dropbox (Work)")
        self.assertEqual(a["dropbox-3"]["name"], "Dropbox 3")
        self.assertEqual(a["dropbox-3"]["local"], self.home / "Dropbox (account 3)")
        self.assertFalse(a["dropbox-2"]["first"])

    def test_single_first_account_ignores_label(self):
        self.remotes("dropbox")
        self.choices({"dropbox": {"label": "Personal", "email": "a@b.c"}})
        a = kumo.account("dropbox")
        self.assertEqual(a["name"], "Dropbox")
        self.assertEqual(a["email"], "a@b.c")

    def test_chosen_local_folder_wins(self):
        self.choices({"drive-2": {"local": str(self.tmp / "elsewhere")}})
        self.assertEqual(kumo.account("drive-2")["local"], self.tmp / "elsewhere")

    def test_new_key(self):
        self.assertEqual(kumo.new_key("dropbox"), "dropbox-2")
        self.remotes("dropbox", "dropbox-2")
        self.choices({"dropbox-3": {"folders": []}})
        self.assertEqual(kumo.new_key("dropbox"), "dropbox-4")
        self.assertEqual(kumo.new_key("drive"), "drive-2")

    def test_connected(self):
        self.assertFalse(kumo.connected("dropbox"))
        self.remotes("dropbox-2")
        self.assertFalse(kumo.connected("dropbox"))
        self.assertTrue(kumo.connected("dropbox-2"))

    def test_clean_label(self):
        self.assertEqual(kumo._clean_label("  Work/Home\\x\n "), "Work Home x")
        self.assertEqual(kumo._clean_label("..."), "")
        self.assertEqual(kumo._clean_label(None), "")
        self.assertEqual(len(kumo._clean_label("x" * 100)), 40)


class WhichCloud(KumoCase):
    def test_paths(self):
        self.remotes("dropbox", "dropbox-2")
        self.choices({"dropbox-2": {"label": "Work"}})
        h = self.home
        self.assertEqual(kumo.which_cloud(h / "Dropbox"), ("dropbox", ""))
        self.assertEqual(kumo.which_cloud(h / "Dropbox/Work/a.txt"), ("dropbox", "Work/a.txt"))
        self.assertEqual(kumo.which_cloud(str(h / "Google Drive/Notes")), ("drive", "Notes"))
        self.assertEqual(kumo.which_cloud(h / "Dropbox (Work)/x"), ("dropbox-2", "x"))
        self.assertEqual(kumo.which_cloud(h / "Dropboxes/x"), (None, None))
        self.assertEqual(kumo.which_cloud(h / "Documents"), (None, None))
        self.assertEqual(kumo.which_cloud(h), (None, None))

    def test_nudge(self):
        kumo.nudge(self.home / "Documents/x")
        self.assertFalse(kumo.TRIGGER.exists())
        kumo.nudge(self.home / "Dropbox/x")
        self.assertTrue(kumo.TRIGGER.exists())
        kumo.TRIGGER.unlink()
        kumo.nudge()
        self.assertTrue(kumo.TRIGGER.exists())


class ChooseAndStatus(KumoCase):
    def test_choose_writes_tidy_folders_and_nudges(self):
        kumo.choose("dropbox", ["Work/", "Work/2024", "Photos"])
        self.assertEqual(json.loads(kumo.CHOICES.read_text())["dropbox"]["folders"], ["Photos", "Work"])
        self.assertEqual(kumo.folders("dropbox"), ["Photos", "Work"])
        self.assertTrue(kumo.TRIGGER.exists())
        self.assertFalse(kumo.CHOICES.with_suffix(".tmp").exists())

    def test_choose_keeps_other_accounts_and_drops_unready(self):
        self.choices({"drive": {"folders": ["Notes"], "email": "x@y"}})
        self.status_file({"dropbox": {"ready": ["Old", "Work"], "state": "idle", "last": 5}})
        kumo.choose("dropbox", ["Work", "New"])
        self.assertEqual(kumo.folders("drive"), ["Notes"])
        self.assertEqual(json.loads(kumo.CHOICES.read_text())["drive"]["email"], "x@y")
        st = kumo.status("dropbox")
        self.assertEqual(st["ready"], ["Work"])        # "New" still needs its first (resync) run
        self.assertEqual(st["last"], 5)

    def test_load_of_missing_or_broken_file(self):
        self.assertEqual(kumo._load(self.tmp / "nope.json"), {})
        (self.tmp / "bad.json").write_text("{")
        self.assertEqual(kumo._load(self.tmp / "bad.json"), {})
        self.assertEqual(kumo.folders("dropbox"), [])
        self.assertEqual(kumo.status("dropbox"), {})


class Describe(KumoCase):
    def setUp(self):
        super().setUp()
        self.remotes("dropbox")

    def test_states(self):
        now = time.time()
        self.assertEqual(kumo.describe("drive"), "Connect")
        self.assertEqual(kumo.describe("dropbox"), "Choose folders")
        self.choices({"dropbox": {"folders": ["A"]}})
        self.assertEqual(kumo.describe("dropbox"), "1 folder · waiting to sync")
        self.choices({"dropbox": {"folders": ["A", "B"]}})
        cases = [({"state": "syncing", "last": now}, "2 folders · syncing…"),
                 ({"state": "error", "last": now}, "2 folders · not synced, will retry"),
                 ({"state": "idle"}, "2 folders · waiting to sync"),
                 ({"state": "idle", "last": now - 10}, "2 folders · synced just now"),
                 ({"state": "idle", "last": now - 5 * 60 - 5}, "2 folders · synced 5 min ago"),
                 ({"state": "idle", "last": now - 3 * 3600 - 5}, "2 folders · synced 3 h ago"),
                 ({"state": "idle", "last": now - 3 * 86400}, "2 folders · synced a while ago")]
        for st, want in cases:
            with self.subTest(st=st):
                self.status_file({"dropbox": st})
                self.assertEqual(kumo.describe("dropbox"), want)


class SyncLogic(KumoCase):
    """sync_folder and sync_all with rclone itself mocked."""

    def setUp(self):
        super().setUp()
        self.remotes("dropbox", "drive")

    def test_first_run_resyncs_and_creates_local(self):
        with mock.patch.object(kumo, "rclone", return_value=fake_completed(returncode=0)) as rc:
            kumo.sync_folder("dropbox", "Work", True)
        args = rc.call_args.args
        self.assertEqual(args[:3], ("bisync", "kuhaku-dropbox:Work", str(self.home / "Dropbox/Work")))
        self.assertIn("--resync", args)
        self.assertIn("--resilient", args)
        self.assertTrue((self.home / "Dropbox/Work").is_dir())
        self.assertIn("[dropbox] Work: exit 0", kumo.LOG.read_text())

    def test_later_run_without_resync_then_recovers(self):
        results = [fake_completed(stderr="ERROR : Bisync aborted. Must run --resync to recover.", returncode=2),
                   fake_completed(returncode=0)]
        with mock.patch.object(kumo, "rclone", side_effect=results) as rc:
            kumo.sync_folder("drive", "Notes", False)
        first, second = rc.call_args_list
        self.assertNotIn("--resync", first.args)
        self.assertIn("--drive-skip-gdocs", first.args)
        self.assertIn("--resync", second.args)

    def test_failure_raises_last_error_line(self):
        err = "INFO : x\nERROR : first\nNOTICE : something\nERROR : the real problem\nmore"
        with mock.patch.object(kumo, "rclone", return_value=fake_completed(stderr=err, returncode=1)):
            with self.assertRaisesRegex(RuntimeError, "the real problem"):
                kumo.sync_folder("dropbox", "Work", True)

    def test_sync_all_one_failure_does_not_stop_others(self):
        self.choices({"dropbox": {"folders": ["A", "B"]}, "drive": {"folders": ["N"]}})
        self.status_file({"dropbox": {"ready": ["A"]}})
        calls = []

        def fake(key, folder, first):
            calls.append((key, folder, first))
            if folder == "B":
                raise RuntimeError("offline")
        with mock.patch.object(kumo, "sync_folder", side_effect=fake):
            kumo.sync_all()
        self.assertEqual(calls, [("dropbox", "A", False), ("dropbox", "B", True), ("drive", "N", True)])
        st = json.loads(kumo.STATUS.read_text())
        self.assertEqual(st["dropbox"]["state"], "error")
        self.assertEqual(st["dropbox"]["error"], "B: offline")
        self.assertEqual(st["dropbox"]["ready"], ["A"])
        self.assertEqual(st["drive"]["state"], "idle")
        self.assertEqual(st["drive"]["ready"], ["N"])
        self.assertIn("last", st["drive"])

    def test_sync_all_skips_unconnected_and_unchosen(self):
        self.remotes("dropbox")
        self.choices({"drive": {"folders": ["N"]}})
        with mock.patch.object(kumo, "sync_folder") as sf:
            kumo.sync_all()
        sf.assert_not_called()

    def test_rclone_missing(self):
        with mock.patch.object(kumo, "rclone_path", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "not installed"):
                kumo.rclone("version")

    def test_rclone_version_parse(self):
        with mock.patch.object(kumo.subprocess, "run", return_value=fake_completed("rclone v1.66.0\n- os")):
            self.assertEqual(kumo.rclone_version("/x/rclone"), (1, 66))
        with mock.patch.object(kumo.subprocess, "run", return_value=fake_completed("garbage")):
            self.assertEqual(kumo.rclone_version("/x/rclone"), (0, 0))
        with mock.patch.object(kumo.subprocess, "run", side_effect=OSError("no")):
            self.assertEqual(kumo.rclone_version("/x/rclone"), (0, 0))

    def test_list_folders(self):
        out = json.dumps([{"Name": "beta"}, {"Name": "Alpha"}, {"Name": "gamma"}])
        with mock.patch.object(kumo, "rclone", return_value=fake_completed(out)) as rc:
            self.assertEqual(kumo.list_folders("drive", "Sub"), ["Alpha", "beta", "gamma"])
        self.assertEqual(rc.call_args.args[-1], "kuhaku-drive:Sub")
        with mock.patch.object(kumo, "rclone", return_value=fake_completed(stderr="x\nno access", returncode=3)):
            with self.assertRaisesRegex(RuntimeError, "no access"):
                kumo.list_folders("drive")

    def test_disconnect_forgets_but_keeps_copies(self):
        self.choices({"dropbox": {"folders": ["A"]}, "drive": {"folders": ["N"]}})
        self.status_file({"dropbox": {"state": "idle"}, "drive": {"state": "idle"}})
        (self.home / "Dropbox/A").mkdir(parents=True)
        with mock.patch.object(kumo, "rclone", return_value=fake_completed()) as rc:
            kumo.disconnect("dropbox")
        self.assertEqual(rc.call_args.args, ("config", "delete", "kuhaku-dropbox"))
        self.assertNotIn("dropbox", json.loads(kumo.CHOICES.read_text()))
        self.assertIn("drive", json.loads(kumo.STATUS.read_text()))
        self.assertTrue((self.home / "Dropbox/A").is_dir())

    def test_log_rotates(self):
        kumo.LOG.parent.mkdir(parents=True)
        kumo.LOG.write_text("x" * 2_000_001)
        kumo._log("fresh")
        self.assertTrue(kumo.LOG.with_suffix(".old").exists())
        self.assertTrue(kumo.LOG.read_text().endswith("fresh\n"))


@unittest.skipUnless(REAL_RCLONE.exists() and os.access(REAL_RCLONE, os.X_OK), "rclone is not installed")
class RealBisync(KumoCase):
    """A real rclone bisync, with the 'Dropbox' an alias remote for a local folder."""

    def setUp(self):
        super().setUp()
        self.cloud = self.tmp / "cloud"
        for rel, text in {"Work/a.txt": "a", "Work/b.txt": "b", "Work/c.txt": "c", "Work/d.txt": "d",
                          "Work/e.txt": "e", "Work/sub/f.txt": "f", "Work/sub/g.txt": "g", "Work/h.txt": "h",
                          "Private/secret.txt": "never copied", "top.txt": "top"}.items():
            (self.cloud / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.cloud / rel).write_text(text)
        kumo.RCLONE_CONF.parent.mkdir(parents=True)
        kumo.RCLONE_CONF.write_text(f"[kuhaku-dropbox]\ntype = alias\nremote = {self.cloud}\n")
        p = mock.patch.object(kumo, "rclone_path", return_value=str(REAL_RCLONE))
        p.start()
        self.addCleanup(p.stop)
        # rclone keeps bisync's listings under $XDG_CACHE_HOME (or ~/.cache): keep them in the temp folder.
        e = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.tmp / "cache"), "HOME": str(self.home)})
        e.start()
        self.addCleanup(e.stop)

    def sync(self):
        kumo.sync_all()
        st = kumo.status("dropbox")
        self.assertEqual(st.get("state"), "idle", (st, kumo.LOG.read_text() if kumo.LOG.exists() else ""))
        return st

    def test_two_way_sync(self):
        local = self.home / "Dropbox"
        kumo.choose("dropbox", ["Work"])
        st = self.sync()
        self.assertEqual(st["ready"], ["Work"])
        self.assertEqual((local / "Work/a.txt").read_text(), "a")
        self.assertEqual((local / "Work/sub/g.txt").read_text(), "g")
        self.assertFalse((local / "Private").exists())
        self.assertFalse((local / "top.txt").exists())

        # Changes on both sides, then a normal (not resync) run.
        later = time.time() + 5
        (local / "Work/new-local.txt").write_text("from this computer")
        (self.cloud / "Work/new-cloud.txt").write_text("from the cloud")
        (local / "Work/a.txt").write_text("a, edited here")
        os.utime(local / "Work/a.txt", (later, later))
        (self.cloud / "Work/b.txt").write_text("b, edited in the cloud")
        os.utime(self.cloud / "Work/b.txt", (later, later))
        (local / "Work/c.txt").unlink()
        (self.cloud / "Work/d.txt").unlink()
        self.sync()

        self.assertEqual((self.cloud / "Work/new-local.txt").read_text(), "from this computer")
        self.assertEqual((local / "Work/new-cloud.txt").read_text(), "from the cloud")
        self.assertEqual((self.cloud / "Work/a.txt").read_text(), "a, edited here")
        self.assertEqual((local / "Work/b.txt").read_text(), "b, edited in the cloud")
        self.assertFalse((self.cloud / "Work/c.txt").exists(), "a local deletion reaches the cloud")
        self.assertFalse((local / "Work/d.txt").exists(), "a cloud deletion reaches this computer")
        self.assertTrue((self.cloud / "Work/e.txt").exists())
        self.assertFalse((local / "Private").exists(), "a folder not chosen is never copied")
        self.assertEqual((self.cloud / "Private/secret.txt").read_text(), "never copied")

    def test_choosing_another_folder_later(self):
        kumo.choose("dropbox", ["Work"])
        self.sync()
        kumo.choose("dropbox", ["Work", "Private"])
        st = self.sync()
        self.assertEqual(st["ready"], ["Private", "Work"])
        self.assertEqual((self.home / "Dropbox/Private/secret.txt").read_text(), "never copied")
        self.assertFalse((self.home / "Dropbox/top.txt").exists())


if __name__ == "__main__":
    unittest.main()
