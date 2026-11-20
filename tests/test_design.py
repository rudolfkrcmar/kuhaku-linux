"""The icon library in design/ is built from the pages: it must match them."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Design(unittest.TestCase):
    def test_icon_library_is_current(self):
        run = subprocess.run(
            [sys.executable, str(ROOT / "dev" / "design.py"), "--check"], capture_output=True, text=True
        )
        self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
