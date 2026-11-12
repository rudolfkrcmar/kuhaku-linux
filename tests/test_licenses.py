"""Licences stay in order: every bundled component is listed in THIRD_PARTY_NOTICES.md with its
version, ships with its licence text, and is under a licence Kūhaku may redistribute alongside
its own MIT code. A new library under any other licence fails here until someone decides."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib"
NOTICES = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

# Licences that may ship inside Kūhaku. Adding one here is a deliberate decision.
ALLOWED = {"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "PSF-2.0", "LGPL-3.0-or-later", "OFL-1.1"}
# Shipped outside lib/: where it is, a word the notices name it by, and its licence file.
VENDORED = [
    ("ide/vendor/codemirror", "CodeMirror", "LICENSE"),
    ("wireplumber", "WirePlumber", "LICENSE"),
    ("login", "Inter typeface", "OFL.txt"),
]


def notice_row(name):
    """The THIRD_PARTY_NOTICES.md table row that names a component."""
    for line in NOTICES.splitlines():
        if line.startswith("|") and name.lower() in line.lower():
            return line
    return None


def dist_infos():
    return sorted(LIB.glob("*.dist-info"))


def metadata(info):
    fields: dict[str, list[str]] = {}
    for line in (info / "METADATA").read_text(encoding="utf-8").split("\n\n")[0].splitlines():
        key, _, value = line.partition(": ")
        fields.setdefault(key, []).append(value.strip())
    return fields


def licence_ids(fields):
    """The SPDX ids a package declares, or None when it only says something like 'Dual License'."""
    text = (fields.get("License-Expression") or fields.get("License") or [""])[0]
    ids = [x for x in re.split(r"\s+(?:OR|AND)\s+|[()]", text) if x.strip()]
    return ids if ids and all(re.fullmatch(r"[A-Za-z0-9.+-]+", x) for x in ids) else None


class Licenses(unittest.TestCase):
    def test_own_licence_is_mit(self):
        self.assertTrue((ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License"))

    def test_every_library_is_listed_with_its_version(self):
        for info in dist_infos():
            fields = metadata(info)
            name, version = fields["Name"][0], fields["Version"][0]
            with self.subTest(library=name):
                row = notice_row(name) or notice_row(name.replace("-", "_")) or notice_row(name.replace("_", "-"))
                self.assertIsNotNone(row, f"{name} is in lib/ but not in THIRD_PARTY_NOTICES.md")
                short = version.split(".post")[0]
                self.assertIn(short, row, f"THIRD_PARTY_NOTICES.md lists another version of {name} than {version}")

    def test_every_library_ships_its_licence_text(self):
        for info in dist_infos():
            files = [p for p in info.rglob("*") if p.is_file() and re.match(r"(LICEN[CS]E|COPYING)", p.name)]
            with self.subTest(library=info.name):
                self.assertTrue(files, f"{info.name} has no licence text")

    def test_every_library_licence_is_allowed_and_matches_the_notice(self):
        for info in dist_infos():
            fields = metadata(info)
            ids = licence_ids(fields)
            name = fields["Name"][0]
            with self.subTest(library=name):
                if ids is None:  # e.g. python-dateutil's "Dual License": the notice names both
                    continue
                for spdx in ids:
                    self.assertIn(spdx, ALLOWED, f"{name} is under {spdx}, which is not on the allowed list")
                    row = notice_row(name) or notice_row(name.replace("-", "_")) or ""
                    self.assertIn(spdx, row, f"THIRD_PARTY_NOTICES.md gives {name} another licence than {spdx}")

    def test_lgpl_comes_with_the_gpl_text_it_builds_on(self):
        if "LGPL" in NOTICES:
            self.assertTrue((LIB / "LICENSES" / "GPL-3.0.txt").exists())

    def test_every_module_in_lib_belongs_to_a_listed_library(self):
        owned = set()
        for info in dist_infos():
            for line in (info / "RECORD").read_text(encoding="utf-8").splitlines():
                owned.add(line.split("/")[0].split(",")[0])
        for p in LIB.iterdir():
            if p.name.endswith(".dist-info") or p.name in {"__pycache__", "LICENSES"}:
                continue
            with self.subTest(item=p.name):
                self.assertIn(p.name, owned, f"lib/{p.name} comes from no package in lib/*.dist-info")

    def test_vendored_code_is_listed_and_licensed(self):
        for where, name, licence in VENDORED:
            with self.subTest(component=name):
                self.assertTrue((ROOT / where / licence).exists(), f"{where}/{licence} is missing")
                self.assertIsNotNone(notice_row(name), f"{name} is not in THIRD_PARTY_NOTICES.md")

    def test_codemirror_version_matches_the_notice(self):
        js = (ROOT / "ide/vendor/codemirror/codemirror.js").read_text(encoding="utf-8")
        version = re.search(r'\.version="([\d.]+)"', js)
        self.assertIsNotNone(version)
        assert version
        self.assertIn(f"CodeMirror {version.group(1)}", NOTICES)

    def test_only_listed_folders_hold_others_code(self):
        # A new vendor folder must be added to VENDORED (and the notices) before it ships.
        vendor = ROOT / "ide/vendor"
        listed = {Path(w).name for w, _, _ in VENDORED if w.startswith("ide/vendor/")}
        self.assertEqual({p.name for p in vendor.iterdir() if p.is_dir()}, listed)


if __name__ == "__main__":
    unittest.main()
