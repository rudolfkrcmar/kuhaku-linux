"""Whole-project checks: everything compiles, nothing runs a shell from a string, every
report() call has its import, and the pages moved out of Python are where their apps look."""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted(ROOT.glob("*.py"))


class Static(unittest.TestCase):
    def test_everything_compiles(self):
        for f in SOURCES:
            with self.subTest(file=f.name):
                compile(f.read_text(encoding="utf-8"), str(f), "exec")

    def test_no_shell_strings(self):
        for f in SOURCES:
            text = f.read_text(encoding="utf-8")
            with self.subTest(file=f.name):
                self.assertNotRegex(text, r"shell\s*=\s*True")
                self.assertNotRegex(text, r"\bos\.system\(")

    def test_report_is_imported_where_used(self):
        for f in SOURCES:
            if f.name == "kuhaku_log.py":
                continue
            tree = ast.parse(f.read_text(encoding="utf-8"))
            calls = any(isinstance(n, ast.Call) and getattr(n.func, "id", None) == "report" for n in ast.walk(tree))
            if calls:
                with self.subTest(file=f.name):
                    self.assertRegex(f.read_text(encoding="utf-8"), r"from kuhaku_log import report")

    def test_no_bare_except(self):
        for f in SOURCES:
            tree = ast.parse(f.read_text(encoding="utf-8"))
            bare = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and n.type is None]
            with self.subTest(file=f.name):
                self.assertEqual(bare, [])

    def test_no_silent_broad_except(self):
        """A broad except that only passes, continues or returns must at least report()."""
        for f in SOURCES:
            if f.name in ("kuhaku_log.py", "transcribe.py"):
                continue
            tree = ast.parse(f.read_text(encoding="utf-8"))
            silent = [h.lineno for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler)
                      and isinstance(h.type, ast.Name) and h.type.id in ("Exception", "BaseException")
                      and all(isinstance(b, (ast.Pass, ast.Continue, ast.Return)) for b in h.body)]
            with self.subTest(file=f.name):
                self.assertEqual(silent, [])

    def test_no_undefined_names(self):
        """Every name read somewhere is defined somewhere in its file (catches a deleted helper still in use)."""
        import builtins
        for f in SOURCES:
            tree = ast.parse(f.read_text(encoding="utf-8"))
            known = set(dir(builtins)) | {"__file__"}
            for n in ast.walk(tree):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    known.add(n.name)
                elif isinstance(n, (ast.Import, ast.ImportFrom)):
                    known.update((a.asname or a.name).split(".")[0] for a in n.names)
                elif isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load):
                    known.add(n.id)
                elif isinstance(n, ast.arg):
                    known.add(n.arg)
                elif isinstance(n, ast.ExceptHandler) and n.name:
                    known.add(n.name)
            used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            with self.subTest(file=f.name):
                self.assertEqual(sorted(used - known), [])

    def test_pages_exist_and_are_used(self):
        for page, module in (("miru/miru.html", "miru.py"), ("browser/tls.html", "browser.py"),
                             ("browser/tls-webkit.html", "browser_webkit.py"),
                             ("mail/google-signed-in.html", "tegami_google.py")):
            with self.subTest(page=page):
                self.assertTrue((ROOT / page).is_file())
                self.assertIn(page, (ROOT / module).read_text(encoding="utf-8"))

    def test_no_personal_paths(self):
        """The repository is public: no one's home folder is written into the code."""
        for f in SOURCES:
            with self.subTest(file=f.name):
                self.assertIsNone(re.search(r"/home/[a-z][a-z0-9_-]+/", f.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
