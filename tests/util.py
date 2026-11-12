"""Shared helpers for Kūhaku's tests."""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401  (sets HOME to a throwaway folder before anything else is imported)

ROOT = Path(__file__).resolve().parent.parent
TEST_HOME = Path(os.environ["HOME"])


def load_file(path, name=None):
    """Import a .py file whose name is not a valid module name (kuhaku-guard.py)."""
    path = ROOT / path
    name = name or path.stem.replace("-", "_")
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TempDirCase(unittest.TestCase):
    """A test case with self.tmp, a fresh folder removed afterwards."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="kuhaku-test-")
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)


class Done(Exception):
    pass


def fake_completed(stdout="", stderr="", returncode=0):
    import subprocess
    return subprocess.CompletedProcess([], returncode, stdout, stderr)
