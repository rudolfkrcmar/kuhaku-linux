"""What the Kūhaku file picker shows: a few folders' files, newest first, and any folder in home.

Plain functions with no window code, shared by the home screen's picture picker (kuhaku.py) and
the picker other apps open for a file (kuhaku-pick.py: Tegami's attachments, Mado's uploads).
Only quick work here (one folder listed, nothing read): it runs inside the home screen's process."""

import mimetypes
import os
import stat
import sys
from pathlib import Path

HOME = Path.home()
HERE = Path(__file__).resolve().parent
PICTURE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
SHOWN = 400  # the newest this many files of a folder; folders are always all there


def places(images):
    """The tabs, in their order: name -> folder ("Recent" looks into several)."""
    named: dict[str, Path | None]
    if images:
        named = {
            "Screenshots": HOME / "Pictures" / "Screenshots",
            "Pictures": HOME / "Pictures",
            "Downloads": HOME / "Downloads",
            "Desktop": HOME / "Desktop",
        }
    else:
        named = {
            "Recent": None,
            "Downloads": HOME / "Downloads",
            "Documents": HOME / "Documents",
            "Desktop": HOME / "Desktop",
            "Pictures": HOME / "Pictures",
        }
    return {k: v for k, v in named.items() if v is None or v.is_dir()}


RECENT = ("Downloads", "Desktop", "Documents", "Pictures", "Pictures/Screenshots")


def human_size(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" or n >= 10 else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def accepts(path, accept):
    """accept: what the page asked for ("image/*", "application/pdf", ".csv"); empty takes anything."""
    if not accept:
        return True
    mime = mimetypes.guess_type(path.name)[0] or ""
    for a in accept:
        a = a.strip().lower()
        if not a:
            continue
        if a.startswith(".") and path.suffix.lower() == a:
            return True
        if a.endswith("/*") and mime.startswith(a[:-1]):
            return True
        if a == mime:
            return True
    return False


def entry(p, st):
    e = {"path": str(p), "name": p.name, "t": st.st_mtime, "size": human_size(st.st_size)}
    if p.suffix.lower() in PICTURE_TYPES and st.st_size < 40_000_000:
        e["uri"] = p.as_uri()
    else:
        e["kind"] = (p.suffix[1:] or "file").upper()[:5]
    return e


def scan(folder, images, accept, with_dirs):
    dirs: list[dict] = []
    files: list[dict] = []
    try:
        children = list(folder.iterdir())
    except OSError:
        return dirs, files
    for p in children:
        if p.name.startswith("."):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode):
            if with_dirs:
                dirs.append({"path": str(p), "name": p.name, "dir": True})
        elif stat.S_ISREG(st.st_mode):
            if images and p.suffix.lower() not in PICTURE_TYPES:
                continue
            if not images and not accepts(p, accept):
                continue
            files.append(entry(p, st))
    return dirs, files


def listing(name, path="", images=False, accept=()):
    """One tab's contents for the page: {folder, path, crumbs, folders, items}."""
    tabs = places(images)
    crumbs, here = [], None
    if name == "Other":
        here = Path(path).resolve() if path else HOME
        if not (here == HOME or HOME in here.parents) or not here.is_dir():
            here = HOME
        dirs, files = scan(here, images, accept, True)
        q = here
        while True:
            crumbs.append({"name": "Home" if q == HOME else q.name, "path": str(q)})
            if q == HOME:
                break
            q = q.parent
    elif name == "Recent" and name in tabs:
        dirs, files = [], []
        for sub in RECENT:
            files += scan(HOME / sub, images, accept, False)[1]
    elif folder := tabs.get(name):
        dirs, files = scan(folder, images, accept, False)
    else:
        dirs, files = [], []
    files.sort(key=lambda e: -e["t"])
    dirs.sort(key=lambda e: e["name"].lower())
    return {
        "folder": name,
        "path": str(here) if here else "",
        "crumbs": crumbs[::-1],
        "folders": [*tabs, "Other"],
        "items": dirs + files[:SHOWN],
    }


def pick_command(words=("Choose a file", "Choose"), single=False, accept=()):
    """The command that opens the picker in its own window (kuhaku-pick.py), or None outside
    Kūhaku, where apps keep the desktop's own file dialog. It prints the chosen paths as JSON."""
    picker = HERE / "kuhaku-pick.py"
    inside = os.environ.get("KUHAKU_SESSION") == "1" or (HOME / ".config/kuhaku/config.json").exists()
    if not (inside and picker.exists()):
        return None
    args = [sys.executable, str(picker), "--title", words[0], "--verb", words[1]]
    if single:
        args.append("--single")
    if accept:
        args += ["--accept", ",".join(accept)]
    return args
