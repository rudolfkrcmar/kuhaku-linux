"""What Miru knows about files: their kinds, sizes and names, USB drives, PDF pages and 3D meshes.

Plain functions with no window code, used by miru.py and tested on their own."""

import json
import mimetypes
import re
import shutil
import subprocess
import time
from pathlib import Path

from gi.repository import Gio, GLib

from kuhaku_log import report  # faults go to the log instead of vanishing

HOME = Path.home()

WEB_IMAGES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "image/bmp",
    "image/x-icon",
    "image/vnd.microsoft.icon",
}
TEXT_TYPES = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-shellscript",
    "application/x-yaml",
    "application/toml",
    "application/x-desktop",
    "application/sql",
    "application/x-python",
    "application/x-perl",
    "application/x-ruby",
    "application/x-php",
}


def usb_drives():
    """Removable drives straight from the kernel (no GVfs here): mounted ones by their folder,
    others as mount:/dev/... so a click opens them through udisks, without a password.
    Each knows what it is (stick, hard drive, SSD, SD card) and, once open, how full it is."""
    try:
        out = subprocess.run(
            [
                "lsblk",
                "-J",
                "-p",
                "-b",
                "-o",
                "NAME,LABEL,MOUNTPOINT,RM,HOTPLUG,TYPE,FSTYPE,SIZE,TRAN,ROTA,MODEL,VENDOR",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
        tree = json.loads(out).get("blockdevices", [])
    except Exception:
        report()
        return []
    found = []

    def gb(n):
        n = float(n or 0)
        return f"{n / 1e12:.1f} TB" if n >= 1e12 else f"{n / 1e9:.0f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"

    def kind_of_disk(d, parent):
        tran = (d.get("tran") or parent.get("tran") or "").lower()
        name = d["name"]
        rota = d.get("rota") if d.get("rota") is not None else parent.get("rota")
        size = int(parent.get("size") or d.get("size") or 0)
        if "mmcblk" in name or tran == "mmc" or "card" in (parent.get("model") or "").lower():
            return "sd"
        if rota in (True, 1, "1"):
            return "hdd"
        if tran == "usb" and size >= 200e9:
            return "ssd"
        if tran in ("nvme", "sata") or "ssd" in (parent.get("model") or "").lower():
            return "ssd"
        return "stick"

    words = {"stick": "USB stick", "hdd": "Hard drive", "ssd": "SSD", "sd": "SD card"}

    def walk(devs, removable, parent):
        for d in devs:
            rem = removable or bool(d.get("rm")) or bool(d.get("hotplug"))
            top = parent or d
            if rem and d.get("fstype") and d.get("type") in ("part", "disk") and d.get("fstype") != "swap":
                kind = kind_of_disk(d, top)
                size = gb(d.get("size"))
                name = d.get("label") or f"{words[kind]} {size}"
                item = {"name": name, "dev": d["name"], "drive": kind, "what": f"{words[kind]} · {size}"}
                mp = d.get("mountpoint")
                if mp:
                    item.update(path=mp, eject=True)
                    try:
                        u = shutil.disk_usage(mp)
                        item["used"] = round(u.used / u.total, 3) if u.total else 0
                        item["what"] = f"{words[kind]} · {gb(u.free)} free of {gb(u.total)}"
                    except OSError:
                        pass
                else:
                    item.update(path="mount:" + d["name"], mount=True)
                found.append(item)
            walk(d.get("children") or [], rem, top)

    walk(tree, False, None)
    return found


def theme():
    """Follow Kūhaku: its saved choice, else the clock."""

    def load(p):
        try:
            return json.loads(p.read_text())
        except Exception:
            report()
            return {}

    cfg = load(HOME / ".config/kuhaku/config.json")
    st = load(HOME / ".local/state/kuhaku/state.json")
    choice = st.get("theme") or cfg.get("theme", "auto")
    if choice in ("day", "night"):
        return choice
    h = time.localtime().tm_hour
    return "night" if (h >= cfg.get("night_from", 20) or h < cfg.get("night_until", 7)) else "day"


def human_size(n):
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1000


def natural(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def mime_of(path):
    try:
        info = Gio.File.new_for_path(str(path)).query_info("standard::content-type,standard::description", 0)
        ctype = Gio.content_type_get_mime_type(info.get_content_type()) or ""
        return ctype, Gio.content_type_get_description(info.get_content_type()) or ""
    except GLib.Error:
        guess = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return guess, guess


def kind_of(path, mime):
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("text/") or mime in TEXT_TYPES:
        return "text"
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        if head and b"\0" not in head:
            head.decode("utf-8")
            return "text"
    except Exception:
        report()
    return "other"


def pdf_pages(path):
    try:
        out = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"^Pages:\s+(\d+)", out, re.M)
        size = re.search(r"^Page size:\s+([\d.]+) x ([\d.]+)", out, re.M)
        ratio = float(size.group(2)) / float(size.group(1)) if size else 1.414
        return (int(m.group(1)) if m else 0), ratio
    except Exception:
        report()
        return 0, 1.414


MESH_TYPES = {".stl", ".3mf", ".obj"}
MESH_LIMIT = 3_000_000  # triangles


def load_mesh(path):
    """Triangles of an STL, 3MF or OBJ file as a flat float32 array (x, y, z per corner, mm)."""
    import xml.etree.ElementTree as ET
    import zipfile

    import numpy as np

    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".stl":
        data = path.read_bytes()
        if len(data) >= 84:
            n = int.from_bytes(data[80:84], "little")
            if 84 + 50 * n == len(data):
                rec = np.frombuffer(data, dtype=np.uint8, offset=84, count=50 * n).reshape(n, 50)
                return np.ascontiguousarray(rec[:, 12:48]).view("<f4").reshape(-1).astype(np.float32)
        nums = re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data)
        return np.array(nums, dtype=np.float32).reshape(-1)
    if ext == ".obj":
        verts, tris = [], []
        for line in path.read_text(errors="replace").splitlines():
            if line.startswith("v "):
                verts.append([float(x) for x in line.split()[1:4]])
            elif line.startswith("f "):
                idx = [int(t.split("/")[0]) for t in line.split()[1:]]
                idx = [i - 1 if i > 0 else len(verts) + i for i in idx]
                tris += [(idx[0], idx[k], idx[k + 1]) for k in range(1, len(idx) - 1)]
        v = np.array(verts, dtype=np.float32)
        return v[np.array(tris, dtype=np.int64)].reshape(-1)
    # 3MF: every build item, with its transform and its components'
    with zipfile.ZipFile(path) as z:
        models = {}
        for name in z.namelist():
            if name.lower().endswith(".model"):
                models["/" + name.lstrip("/")] = ET.fromstring(z.read(name))
    ns = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"

    def mat(t):
        if not t:
            return np.eye(4)
        a = [float(x) for x in t.split()]
        m = np.eye(4)
        m[:3, :3] = np.array(a[:9]).reshape(3, 3).T
        m[:3, 3] = a[9:12]
        return m

    objects = {}
    for part, root in models.items():
        for obj in root.iter(ns + "object"):
            objects[(part, obj.get("id"))] = obj
    main = next((k for k in models if k.lower().endswith("3d/3dmodel.model")), next(iter(models)))
    chunks = []

    def emit(part, oid, m, depth=0):
        obj = objects.get((part, oid))
        if obj is None or depth > 8:
            return
        mesh = obj.find(ns + "mesh")
        if mesh is not None:
            v = np.array(
                [
                    [float(e.attrib["x"]), float(e.attrib["y"]), float(e.attrib["z"])]
                    for e in mesh.iterfind(ns + "vertices/*")
                ],
                dtype=np.float64,
            )
            t = np.array(
                [
                    [int(e.attrib["v1"]), int(e.attrib["v2"]), int(e.attrib["v3"])]
                    for e in mesh.iterfind(ns + "triangles/*")
                ],
                dtype=np.int64,
            )
            if len(v) and len(t):
                v = v @ m[:3, :3].T + m[:3, 3]
                chunks.append(v[t].reshape(-1).astype(np.float32))
        comps = obj.find(ns + "components")
        if comps is not None:
            for c in comps:
                p2 = c.get("{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path") or part
                emit(p2, c.get("objectid"), m @ mat(c.get("transform")), depth + 1)

    build = models[main].find(ns + "build")
    for item in build if build is not None else []:
        emit(main, item.get("objectid"), mat(item.get("transform")))
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
