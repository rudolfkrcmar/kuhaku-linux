"""miru.py helpers: sizes, natural sort, kinds of file, USB drives from lsblk, meshes, and the
file operations of the Miru class (run on a bare instance: no window is ever made)."""
import json
import struct
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tests.util import TempDirCase, fake_completed

import miru


class HumanSize(unittest.TestCase):
    def test_units(self):
        cases = [(0, "0 bytes"), (999, "999 bytes"), (1000, "1.0 KB"), (1500, "1.5 KB"), (999_999, "1000.0 KB"),
                 (1_000_000, "1.0 MB"), (2_500_000_000, "2.5 GB"), (3e12, "3.0 TB"), (5e15, "5000.0 TB")]
        for n, want in cases:
            with self.subTest(n=n):
                self.assertEqual(miru.human_size(n), want)


class Natural(unittest.TestCase):
    def test_numbers_sort_by_value(self):
        names = ["file10.txt", "File2.txt", "file1.txt", "a", "B"]
        self.assertEqual(sorted(names, key=miru.natural), ["a", "B", "file1.txt", "File2.txt", "file10.txt"])
        # Names starting with a digit and with a letter compare without a TypeError.
        self.assertEqual(sorted(["b", "10", "2", "a1"], key=miru.natural), ["2", "10", "a1", "b"])
        self.assertLess(miru.natural("IMG_9.jpg"), miru.natural("IMG_10.jpg"))
        self.assertEqual(miru.natural("Abc"), miru.natural("aBC"))


class KindOf(TempDirCase):
    def test_by_mime(self):
        f = self.tmp / "x"
        f.write_bytes(b"\0\1")
        for mime, kind in [("image/png", "image"), ("video/mp4", "video"), ("audio/ogg", "audio"),
                           ("application/pdf", "pdf"), ("text/plain", "text"), ("application/json", "text"),
                           ("application/x-shellscript", "text"), ("application/octet-stream", "other")]:
            with self.subTest(mime=mime):
                self.assertEqual(miru.kind_of(f, mime), kind)

    def test_sniffs_unknown_files(self):
        text = self.tmp / "notes"
        text.write_text("plain words, ünïcode too\n")
        binary = self.tmp / "blob"
        binary.write_bytes(b"abc\0def")
        latin = self.tmp / "latin"
        latin.write_bytes("caf\xe9".encode("latin-1"))
        empty = self.tmp / "empty"
        empty.write_bytes(b"")
        self.assertEqual(miru.kind_of(text, "application/octet-stream"), "text")
        self.assertEqual(miru.kind_of(binary, "application/octet-stream"), "other")
        self.assertEqual(miru.kind_of(latin, "application/octet-stream"), "other")
        self.assertEqual(miru.kind_of(empty, "application/octet-stream"), "other")
        self.assertEqual(miru.kind_of(self.tmp / "missing", "application/octet-stream"), "other")

    def test_mime_of_real_files(self):
        f = self.tmp / "page.html"
        f.write_text("<html><body>hi</body></html>")
        mime, desc = miru.mime_of(f)
        self.assertEqual(mime, "text/html")
        self.assertTrue(desc)


LSBLK = {"blockdevices": [
    {"name": "/dev/nvme0n1", "rm": False, "hotplug": False, "type": "disk", "fstype": None, "size": 512e9,
     "tran": "nvme", "rota": False, "model": "Internal", "children": [
         {"name": "/dev/nvme0n1p1", "rm": False, "hotplug": False, "type": "part", "fstype": "vfat",
          "mountpoint": "/boot/efi", "size": 5e8}]},
    {"name": "/dev/sda", "rm": True, "hotplug": True, "type": "disk", "fstype": None, "size": 32e9,
     "tran": "usb", "rota": False, "model": "Flash", "children": [
         {"name": "/dev/sda1", "label": "STICK", "rm": True, "hotplug": True, "type": "part",
          "fstype": "exfat", "mountpoint": None, "size": 32e9, "tran": None, "rota": None},
         {"name": "/dev/sda2", "rm": True, "type": "part", "fstype": "swap", "size": 1e9}]},
    {"name": "/dev/sdb", "rm": False, "hotplug": True, "type": "disk", "fstype": None, "size": 2e12,
     "tran": "usb", "rota": True, "model": "Backup", "children": [
         {"name": "/dev/sdb1", "type": "part", "fstype": "ext4", "mountpoint": "MOUNTED", "size": 2e12}]},
    {"name": "/dev/sdc", "rm": False, "hotplug": True, "type": "disk", "fstype": None, "size": 1e12,
     "tran": "usb", "rota": False, "model": "Portable", "children": [
         {"name": "/dev/sdc1", "type": "part", "fstype": "ntfs", "size": 1e12}]},
    {"name": "/dev/mmcblk0", "rm": True, "type": "disk", "fstype": None, "size": 64e9, "tran": None,
     "children": [{"name": "/dev/mmcblk0p1", "type": "part", "fstype": "vfat", "size": 64e9}]},
    {"name": "/dev/sdd", "rm": True, "hotplug": True, "type": "disk", "fstype": "vfat", "size": 8e9,
     "tran": "usb", "rota": False, "label": None},
]}


class UsbDrives(TempDirCase):
    def drives(self, payload):
        out = payload if isinstance(payload, str) else json.dumps(LSBLK).replace("MOUNTED", str(self.tmp))
        with mock.patch.object(miru.subprocess, "run", return_value=fake_completed(out)) as run:
            found = miru.usb_drives()
        self.assertEqual(run.call_args.args[0][:2], ["lsblk", "-J"])
        return found

    def test_removable_only(self):
        found = {d["dev"]: d for d in self.drives(None)}
        self.assertEqual(sorted(found), ["/dev/mmcblk0p1", "/dev/sda1", "/dev/sdb1", "/dev/sdc1", "/dev/sdd"])

    def test_unmounted_stick(self):
        d = {d["dev"]: d for d in self.drives(None)}["/dev/sda1"]
        self.assertEqual(d["name"], "STICK")
        self.assertEqual(d["drive"], "stick")
        self.assertEqual(d["path"], "mount:/dev/sda1")
        self.assertTrue(d["mount"])
        self.assertEqual(d["what"], "USB stick · 32 GB")

    def test_mounted_hard_drive_reports_use(self):
        d = {d["dev"]: d for d in self.drives(None)}["/dev/sdb1"]
        self.assertEqual(d["drive"], "hdd")
        self.assertEqual(d["name"], "Hard drive 2.0 TB")
        self.assertEqual(d["path"], str(self.tmp))
        self.assertTrue(d["eject"])
        self.assertIn("free of", d["what"])
        self.assertTrue(0 <= d["used"] <= 1)

    def test_kinds(self):
        d = {d["dev"]: d for d in self.drives(None)}
        self.assertEqual(d["/dev/sdc1"]["drive"], "ssd")       # big, USB, not spinning
        self.assertEqual(d["/dev/mmcblk0p1"]["drive"], "sd")
        self.assertEqual(d["/dev/mmcblk0p1"]["name"], "SD card 64 GB")
        self.assertEqual(d["/dev/sdd"]["drive"], "stick")       # a whole-disk filesystem
        self.assertEqual(d["/dev/sdd"]["what"], "USB stick · 8 GB")

    def test_bad_output(self):
        self.assertEqual(self.drives("not json"), [])
        self.assertEqual(self.drives("{}"), [])
        with mock.patch.object(miru.subprocess, "run", side_effect=FileNotFoundError("lsblk")):
            self.assertEqual(miru.usb_drives(), [])


class PdfPages(unittest.TestCase):
    def test_parse(self):
        out = "Title: x\nPages:          12\nPage size:      612 x 792 pts (letter)\n"
        with mock.patch.object(miru.subprocess, "run", return_value=fake_completed(out)):
            pages, ratio = miru.pdf_pages("/x.pdf")
        self.assertEqual(pages, 12)
        self.assertAlmostEqual(ratio, 792 / 612)
        with mock.patch.object(miru.subprocess, "run", return_value=fake_completed("")):
            self.assertEqual(miru.pdf_pages("/x.pdf"), (0, 1.414))
        with mock.patch.object(miru.subprocess, "run", side_effect=OSError):
            self.assertEqual(miru.pdf_pages("/x.pdf"), (0, 1.414))


class Mesh(TempDirCase):
    TRI = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]

    def test_binary_stl(self):
        f = self.tmp / "a.stl"
        rec = struct.pack("<3f", 0, 0, 1) + b"".join(struct.pack("<3f", *v) for v in self.TRI) + b"\0\0"
        f.write_bytes(b"\0" * 80 + struct.pack("<I", 2) + rec * 2)
        out = miru.load_mesh(f)
        self.assertEqual(out.shape, (18,))
        self.assertEqual(list(out[:9]), [0, 0, 0, 1, 0, 0, 0, 1, 0])

    def test_ascii_stl(self):
        f = self.tmp / "a.STL"
        f.write_text("solid x\nfacet normal 0 0 1\nouter loop\n" +
                     "".join(f"vertex {x} {y} {z}\n" for x, y, z in self.TRI) + "endloop\nendfacet\nendsolid\n")
        self.assertEqual(list(miru.load_mesh(f)), [0, 0, 0, 1, 0, 0, 0, 1, 0])

    def test_obj_quads_and_negative_indices(self):
        f = self.tmp / "a.obj"
        f.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1/1 2/2 3/3 4/4\nf -4 -3 -2\n")
        out = miru.load_mesh(f)
        self.assertEqual(out.shape, (27,))          # a quad is two triangles, plus one more
        self.assertEqual(list(out[9:18]), [0, 0, 0, 1, 1, 0, 0, 1, 0])

    def test_3mf_with_transform(self):
        f = self.tmp / "a.3mf"
        model = ('<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>'
                 '<object id="1"><mesh><vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/>'
                 '<vertex x="0" y="1" z="0"/></vertices><triangles><triangle v1="0" v2="1" v3="2"/></triangles>'
                 '</mesh></object></resources><build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 10 0 5"/>'
                 '</build></model>')
        with zipfile.ZipFile(f, "w") as z:
            z.writestr("3D/3dmodel.model", model)
        self.assertEqual(list(miru.load_mesh(f)), [10, 0, 5, 11, 0, 5, 10, 1, 5])


class FileOps(TempDirCase):
    """Miru's own file operations on a bare instance (no window, no web view)."""

    def setUp(self):
        super().setUp()
        self.m = object.__new__(miru.Miru)
        self.m.js = mock.Mock()
        self.m.browse = mock.Mock()
        self.m.folder = self.tmp
        p = mock.patch.object(miru.kumo, "nudge")
        self.nudge = p.start()
        self.addCleanup(p.stop)

    def test_unique(self):
        self.assertEqual(self.m.unique(self.tmp, "a.txt"), self.tmp / "a.txt")
        (self.tmp / "a.txt").write_text("x")
        (self.tmp / "a 2.txt").write_text("x")
        self.assertEqual(self.m.unique(self.tmp, "a.txt"), self.tmp / "a 3.txt")
        (self.tmp / "Makefile").write_text("x")
        self.assertEqual(self.m.unique(self.tmp, "Makefile"), self.tmp / "Makefile 2")

    def test_fixed_unique_folder_with_a_dot(self):
        # unique() checks Path(name).is_dir(), i.e. relative to the process's working directory,
        # not folder / name: a folder "v1.0" already there becomes "v1 2.0" instead of "v1.0 2".
        (self.tmp / "v1.0").mkdir()
        self.assertEqual(self.m.unique(self.tmp, "v1.0"), self.tmp / "v1.0 2")

    def test_copy_and_move(self):
        src_dir = self.tmp / "src"
        (src_dir / "tree").mkdir(parents=True)
        (src_dir / "tree/x.txt").write_text("x")
        (src_dir / "f.txt").write_text("f")
        dest = self.tmp / "dest"
        dest.mkdir()
        self.assertEqual(self.m.copy_into(src_dir / "f.txt", dest), dest / "f.txt")
        self.assertEqual(self.m.copy_into(src_dir / "f.txt", dest), dest / "f 2.txt")
        self.assertEqual((dest / "tree").exists(), False)
        self.assertEqual(self.m.copy_into(src_dir / "tree", dest), dest / "tree")
        self.assertEqual((dest / "tree/x.txt").read_text(), "x")
        self.assertTrue((src_dir / "f.txt").exists())
        moved = self.m.copy_into(src_dir / "f.txt", dest, move=True)
        self.assertEqual(moved, dest / "f 3.txt")
        self.assertFalse((src_dir / "f.txt").exists())
        # Moving into its own folder does nothing.
        self.assertEqual(self.m.copy_into(dest / "f.txt", dest, move=True), dest / "f.txt")
        self.assertTrue((dest / "f.txt").exists())

    def test_rename(self):
        f = self.tmp / "old.txt"
        f.write_text("x")
        for bad in ["", "  ", "a/b", "old.txt"]:
            self.m.rename(str(f), bad)
            self.assertTrue(f.exists(), bad)
        (self.tmp / "taken.txt").write_text("y")
        self.m.rename(str(f), "taken.txt")
        self.assertTrue(f.exists())
        self.assertIn("already exists", self.m.js.call_args.args[1])
        self.m.rename(str(f), " new.txt ")
        self.assertFalse(f.exists())
        self.assertEqual((self.tmp / "new.txt").read_text(), "x")
        self.m.browse.assert_called_with(self.tmp, select="new.txt")
        self.nudge.assert_called_with(self.tmp / "new.txt")

    def test_mkdir(self):
        self.m.mkdir("")
        self.m.mkdir("  ")
        self.m.mkdir("Photos")
        self.m.mkdir("Photos")
        names = sorted(p.name for p in self.tmp.iterdir())
        self.assertEqual(names, ["New folder", "New folder 2", "Photos", "Photos 2"])
        self.m.browse.assert_called_with(self.tmp, select="Photos 2")


class Page(unittest.TestCase):
    def test_page_loaded(self):
        self.assertIn("<html", miru.PAGE.lower())


if __name__ == "__main__":
    unittest.main()
