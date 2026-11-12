"""claude_accounts.py: sets of accounts, the active one, per-purpose choices, env and commands.
Ollama is never asked (ollama_models is mocked): no network."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from tests.util import TempDirCase

import claude_accounts as acc


class AccountsCase(TempDirCase):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()
        for name, value in {"HOME": self.home, "FILE": self.home / ".config/kuhaku/claude-accounts.json",
                            "ROOT": self.home / ".claude-accounts"}.items():
            p = mock.patch.object(acc, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.models = []
        p = mock.patch.object(acc, "ollama_models", side_effect=lambda: self.models or None)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(acc, "exe", side_effect=lambda k: f"/bin/{acc.KINDS[k]['bin']}")
        p.start()
        self.addCleanup(p.stop)

    def sign_in(self, ident):
        a = next(a for a in acc.load()["accounts"] if a["id"] == ident)
        d = acc.config_dir(a)
        f = {"claude": d / ".credentials.json", "codex": d / "auth.json",
             "gemini": d / ".gemini/oauth_creds.json"}[acc.kind(a)]
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}")


class Load(AccountsCase):
    def test_default(self):
        data = acc.load()
        self.assertEqual([a["id"] for a in data["accounts"]], ["private", "local"])
        self.assertEqual(data["accounts"][0]["set"], "Personal")
        self.assertEqual(data["active"], "private")
        self.assertEqual(data["sets"], ["Personal", "Work"])
        self.assertEqual(acc.active()["id"], "private")
        self.assertEqual(acc.config_dir(), self.home / ".claude")

    def test_old_file_gets_sets_and_dedups(self):
        acc.save({"active": "b", "accounts": [
            {"id": "a", "name": "Me", "dir": None}, {"id": "b", "name": "Job", "dir": "/x/b"},
            {"id": "c", "name": "Job", "dir": "/x/c"}, {"id": "g", "name": "G", "kind": "gemini", "dir": "/x/g"},
            {"id": "l1", "kind": "local", "name": "Local", "set": ""}, {"id": "l2", "kind": "local", "name": "Local", "set": ""}]})
        data = acc.load()
        self.assertEqual([a["id"] for a in data["accounts"]], ["a", "b", "g", "l1"])
        sets = {a["id"]: a.get("set") for a in data["accounts"]}
        self.assertEqual(sets["a"], "Personal")
        self.assertEqual(sets["b"], "Job")           # an older second Claude becomes its own set
        self.assertEqual(sets["g"], "Personal")
        self.assertIn("Job", data["sets"])
        self.assertEqual(acc.active()["id"], "b")
        self.assertEqual(acc.config_dir(), Path("/x/b"))

    def test_broken_file(self):
        acc.FILE.parent.mkdir(parents=True)
        acc.FILE.write_text("{")
        self.assertEqual(acc.load()["active"], "private")


class AddAndRemove(AccountsCase):
    def test_add_makes_folder_and_copies_notes(self):
        (self.home / ".claude").mkdir()
        (self.home / ".claude/CLAUDE.md").write_text("notes about this computer")
        ident = acc.add("Work Stuff!")
        self.assertEqual(ident, "work-stuff")
        d = acc.ROOT / ident
        self.assertEqual(oct(d.stat().st_mode & 0o777), "0o700")
        self.assertEqual((d / "CLAUDE.md").read_text(), "notes about this computer")
        g = acc.add("Work Stuff!", "gemini")
        self.assertEqual(g, "work-stuff-gemini")
        self.assertEqual((acc.ROOT / g / ".gemini/GEMINI.md").read_text(), "notes about this computer")
        o = acc.add("Work Stuff!", "codex")
        self.assertTrue((acc.ROOT / o / "AGENTS.md").exists())
        self.assertEqual(acc.add("  ", "nonsense"), "work")     # blank name: Work; unknown kind: Claude
        self.assertIn("Work Stuff!", acc.load()["sets"])

    def test_ensure_finds_before_adding(self):
        a = acc.ensure("Work", "codex")
        self.assertEqual(acc.ensure("Work", "codex"), a)
        self.assertEqual(acc.ensure("Personal", "claude"), "private")

    def test_fixed_add_twice_returns_an_account_that_load_drops(self):
        # add() always appends a new account, but load() keeps only one per (set, kind): a second
        # add("Work") returns "work-2", which is saved yet never listed, so use("work-2") cannot work
        # (kuhaku.py's "account-add" action then switches to an account that does not exist).
        acc.add("Work")
        second = acc.add("Work")
        self.assertIn(second, [a["id"] for a in acc.load()["accounts"]])

    def test_remove(self):
        w = acc.add("Work")
        self.sign_in(w)
        self.assertTrue(acc.use(w))
        acc.set_for("mail", w)
        self.assertFalse(acc.remove("private"))       # the first account stays
        self.assertTrue(acc.remove(w))
        data = acc.load()
        self.assertEqual(data["active"], "private")
        self.assertEqual(acc.uses(), {"mail": "", "dev": ""})
        self.assertTrue((acc.ROOT / w).is_dir())       # its folder stays

    def test_sets(self):
        acc.add_set("  Side   project ")
        acc.add_set("side project")
        acc.add_set("")
        self.assertEqual(acc.load()["sets"], ["Personal", "Work", "Side project"])
        w = acc.ensure("Work", "claude")
        self.sign_in(w)
        acc.use(w)
        acc.set_for("dev", w)
        acc.remove_set("Personal")                     # the first set cannot go
        self.assertIn("Personal", acc.load()["sets"])
        acc.remove_set("Work")
        data = acc.load()
        self.assertNotIn("Work", data["sets"])
        self.assertNotIn(w, [a["id"] for a in data["accounts"]])
        self.assertEqual(data["active"], "private")
        self.assertEqual(acc.uses()["dev"], "")

    def test_sets_listing(self):
        self.sign_in(acc.ensure("Work", "gemini"))
        out = {s["name"]: s for s in acc.sets_listing()}
        self.assertFalse(out["Personal"]["removable"])
        self.assertTrue(out["Work"]["removable"])
        self.assertEqual(out["Personal"]["slots"]["claude"], {"id": "private", "signedIn": False})
        self.assertIsNone(out["Personal"]["slots"]["codex"])
        self.assertTrue(out["Work"]["slots"]["gemini"]["signedIn"])


class UseAndPick(AccountsCase):
    def test_use_needs_signed_in_claude(self):
        w = acc.add("Work")
        self.assertFalse(acc.use(w))
        self.sign_in(w)
        self.assertTrue(acc.use(w))
        self.assertEqual(acc.active()["id"], w)
        g = acc.ensure("Work", "gemini")
        self.sign_in(g)
        self.assertFalse(acc.use(g))
        self.assertFalse(acc.use("nope"))

    def test_cycle(self):
        self.assertEqual(acc.cycle()["id"], "private")      # none signed in: stays
        self.sign_in("private")
        w = acc.add("Work")
        self.sign_in(w)
        self.assertEqual(acc.cycle()["id"], w)
        self.assertEqual(acc.cycle()["id"], "private")

    def test_pick_for_purpose(self):
        w = acc.ensure("Work", "codex")
        acc.set_for("mail", w)
        self.assertEqual(acc.pick(purpose="mail")["id"], "private")    # not signed in: not picked
        self.sign_in(w)
        self.assertEqual(acc.pick(purpose="mail")["id"], w)
        self.assertEqual(acc.pick(purpose="dev")["id"], "private")
        self.assertEqual(acc.pick(ident=w)["id"], w)
        self.assertEqual(acc.command("mail"), "/bin/codex")
        acc.set_for("mail", "nope")
        self.assertEqual(acc.uses()["mail"], "")
        acc.set_for("other", w)
        self.assertNotIn("other", acc.load().get("for", {}))

    def test_env_and_prefix(self):
        base = {"PATH": "/usr/bin", "CLAUDE_CONFIG_DIR": "/stale", "CODEX_HOME": "/stale"}
        e = acc.env(base)
        self.assertNotIn("CLAUDE_CONFIG_DIR", e)
        self.assertNotIn("CODEX_HOME", e)
        self.assertEqual(e["PATH"], f"{self.home}/.local/bin:/usr/bin")
        self.assertEqual(acc.shell_prefix(), "")
        w = acc.add("My Work")
        self.sign_in(w)
        acc.use(w)
        e = acc.env(base)
        self.assertEqual(e["CLAUDE_CONFIG_DIR"], str(acc.ROOT / w))
        self.assertEqual(acc.shell_prefix(), f"CLAUDE_CONFIG_DIR={acc.ROOT / w} ")
        self.assertEqual(base["CLAUDE_CONFIG_DIR"], "/stale")       # the given env is not changed

    def test_login_commands(self):
        a = {"id": "x", "kind": "claude", "dir": "/a b/x"}
        self.assertEqual(acc.login_command(a), "CLAUDE_CONFIG_DIR='/a b/x' /bin/claude auth login")
        self.assertEqual(acc.login_command({"id": "p", "dir": None}), "/bin/claude auth login")
        self.assertEqual(acc.login_command({"kind": "codex", "dir": "/c"}), "CODEX_HOME=/c /bin/codex login")
        self.assertEqual(acc.login_command({"kind": "local", "dir": "/l"}), "")
        g = self.tmp / "gem"
        cmd = acc.login_command({"kind": "gemini", "dir": str(g)})
        self.assertTrue(cmd.startswith(f"GEMINI_CLI_HOME={g} /bin/gemini -p"))
        settings = json.loads((g / ".gemini/settings.json").read_text())
        self.assertEqual(settings["security"]["auth"]["selectedType"], "oauth-personal")


class Local(AccountsCase):
    def test_local_model_and_flags(self):
        local = next(a for a in acc.load()["accounts"] if a["id"] == "local")
        self.assertFalse(acc.signed_in(local))
        self.assertEqual(acc.label(local), "Local · Ollama")
        self.assertEqual(acc.local_model(), "")
        self.assertEqual(acc.flags(acct=local), " --oss --local-provider ollama")
        self.models = ["llama3", "qwen"]
        self.assertTrue(acc.signed_in(local))
        self.assertEqual(acc.local_model(), "llama3")
        acc.set_local_model("qwen")
        self.assertEqual(acc.local_model(), "qwen")
        self.assertEqual(acc.flags(acct=local), " --oss --local-provider ollama -m qwen")
        self.assertEqual(acc.flags(acct={"kind": "claude"}), "")
        self.models = []
        self.assertEqual(acc.local_model(), "qwen")         # the choice is kept while Ollama is off
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(acc.local_status()["running"], False)

    def test_labels_and_listing(self):
        self.assertEqual(acc.label({"set": "Work", "kind": "codex"}), "Work OpenAI")
        rows = {r["id"]: r for r in acc.listing()}
        self.assertTrue(rows["private"]["active"])
        self.assertEqual(rows["private"]["label"], "Personal Claude")
        self.assertEqual(rows["local"]["kind"], "local")


class Ask(AccountsCase):
    def test_codex_answer_with_schema(self):
        o = acc.ensure("Personal", "codex")
        self.sign_in(o)
        acc.set_for("mail", o)

        def fake_run(argv, **kw):
            Path(argv[argv.index("-o") + 1]).write_text('Here: {"ok": true}')
            self.assertIn("--output-schema", argv)
            self.assertIn("read-only", argv)
            self.assertEqual(kw["env"]["CODEX_HOME"], str(acc.ROOT / o))
            return mock.Mock(stdout="", stderr="")
        with mock.patch("subprocess.run", side_effect=fake_run):
            self.assertEqual(acc.ask("q", "sys", purpose="mail", schema={"type": "object"}), {"ok": True})

    def test_claude_is_refused_and_empty_answer_raises(self):
        with self.assertRaises(RuntimeError):
            acc.ask("q", "s")
        g = acc.ensure("Personal", "gemini")
        self.sign_in(g)
        acc.set_for("dev", g)
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout="  ", stderr="quota exceeded")):
            with self.assertRaisesRegex(RuntimeError, "quota exceeded"):
                acc.ask("q", "s", purpose="dev")


if __name__ == "__main__":
    unittest.main()
