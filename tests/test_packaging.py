"""The package's own update source: it points at Kūhaku's key, and its updates install by themselves."""

import subprocess
import unittest

from tests.util import ROOT

PKG = ROOT / "os" / "package"


class UpdateSource(unittest.TestCase):
    def test_the_source_is_signed_by_the_shipped_key(self):
        src = (PKG / "etc/apt/sources.list.d/kuhaku.sources").read_text()
        self.assertIn("URIs: https://www.kuhaku.space/apt", src)
        key = next(line.split(":", 1)[1].strip() for line in src.splitlines() if line.startswith("Signed-By:"))
        shipped = PKG / key.lstrip("/")
        self.assertTrue(shipped.is_file(), f"{key} is not in the package")
        out = subprocess.run(
            ["gpg", "--batch", "--show-keys", "--with-colons", str(shipped)], capture_output=True, text=True
        ).stdout
        self.assertIn("<packages@kuhaku.space>", out)

    def test_kuhaku_updates_install_by_themselves(self):
        conf = (PKG / "etc/apt/apt.conf.d/52kuhaku-unattended").read_text()
        self.assertIn('"origin=Kuhaku,label=Kuhaku";', conf)
        repo = (ROOT / "os/apt-repo.sh").read_text()
        self.assertIn("Release::Origin=Kuhaku", repo)
        self.assertIn("Release::Label=Kuhaku", repo)


if __name__ == "__main__":
    unittest.main()
