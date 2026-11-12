"""Kūhaku's tests. Importing this package points HOME at a throwaway folder first, so no module
that reads Path.home() at import time can ever see (or touch) the real home folder."""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if not os.environ.get("KUHAKU_TEST_HOME"):
    _home = tempfile.mkdtemp(prefix="kuhaku-test-home-")
    atexit.register(shutil.rmtree, _home, True)
    os.environ["KUHAKU_TEST_HOME"] = _home
    os.environ["REAL_HOME_FOR_TESTS"] = os.environ.get("HOME", "")
    os.environ["HOME"] = _home
    for var in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        os.environ.pop(var, None)
    os.environ.pop("KUHAKU_DEBUG", None)
