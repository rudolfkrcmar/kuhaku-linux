#!/usr/bin/env python3
"""Miru (見る, "to look"): Kūhaku's files and previewer.

Given a folder (or nothing: your home), Miru is the Files app: places on the
left, the folder as a calm list. Given a file, or when you open one, it shows
it: pictures, video, sound, PDFs and text as they are; anything else shows
what it is and offers its own app.

Folder keys: Up/Down choose (Shift adds the rows passed), Cmd+click picks
several, Shift+click a range, Cmd+A all, Enter opens, Space looks, Backspace goes up,
Delete moves to the Trash, F2 renames, Cmd+Shift+N new folder, Cmd+H hidden
files, typing jumps to a name.
Preview keys: Left/Right previous or next file, Enter opens in its app,
Space or Esc goes back (or closes), Cmd+plus/minus/0 zoom pictures.

    miru [FOLDER | FILE]
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import urllib.parse
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, WebKit2

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kumo  # Dropbox and Google Drive, only the folders you choose
from kuhaku_log import report  # faults go to the log instead of vanishing
from miru_files import (
    MESH_LIMIT,
    MESH_TYPES,
    WEB_IMAGES,
    human_size,
    kind_of,
    load_mesh,
    mime_of,
    natural,
    pdf_pages,
    theme,
    usb_drives,
)

HOME = Path.home()
TEXT_LIMIT = 1_000_000


class Miru:
    held: tuple[str, list[str]] | None = None  # copied or cut paths waiting to be pasted

    def __init__(self, path):
        self.night = theme() == "night"
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", self.night)
        self.win = Gtk.Window(title="Miru")
        self.win.set_decorated(False)
        self.win.set_icon_name("miru")
        self.win.maximize()
        self.win.connect("destroy", Gtk.main_quit)

        ctx = WebKit2.WebContext.get_default()
        ctx.register_uri_scheme("miru", self.on_scheme)
        ctx.get_security_manager().register_uri_scheme_as_local("miru")
        ucm = WebKit2.UserContentManager()
        ucm.register_script_message_handler("miru")
        ucm.connect("script-message-received::miru", self.on_message)
        self.view = WebKit2.WebView.new_with_user_content_manager(ucm)
        self.view.set_zoom_level(1.12)  # app type a little larger than the page's own, for reading
        s = self.view.get_settings()
        s.set_allow_file_access_from_file_urls(True)
        s.set_enable_media(True)
        s.set_enable_mediasource(True)
        s.set_media_playback_requires_user_gesture(False)
        s.set_enable_smooth_scrolling(True)
        s.set_enable_write_console_messages_to_stdout(bool(os.environ.get("MIRU_DEBUG")))
        bg = (0.071, 0.071, 0.071) if self.night else (0.969, 0.957, 0.945)
        self.view.set_background_color(Gdk.RGBA(*bg, 1))
        self.view.connect("context-menu", lambda *a: True)
        self.view.connect("decide-policy", self.on_policy)
        self.win.add(self.view)

        self.path = Path(path).expanduser().resolve()
        self.files = []
        self.folder: Path | None = None  # the folder being browsed, if Miru is the Files app
        self.hidden = False
        self.cloud_mode = None  # the cloud whose setup page is showing
        self.drives = [d["path"] for d in usb_drives()]
        self.clouds = self.cloud_places()
        self.cloud_last = self.cloud_syncs()
        self.watch()
        if any(kumo.folders(k) for k in kumo.accounts()):
            kumo.ensure_daemon()
        self.view.load_html(PAGE.replace("{THEME}", "night" if self.night else "day"), PAGE_URI)
        self.view.connect("load-changed", lambda v, e: e == WebKit2.LoadEvent.FINISHED and self.start())
        self.win.show_all()

    def start(self):
        if self.path.is_dir():
            self.browse(self.path)
        else:
            self.show(self.path)

    def js(self, fn, payload):
        self.view.evaluate_javascript(f"{fn}({json.dumps(payload)})", -1, None, None, None, None, None)

    # --- folders -------------------------------------------------------------
    def places(self):
        out = [("Home", HOME)]
        for name in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music"):
            special = {
                "Desktop": GLib.UserDirectory.DIRECTORY_DESKTOP,
                "Documents": GLib.UserDirectory.DIRECTORY_DOCUMENTS,
                "Downloads": GLib.UserDirectory.DIRECTORY_DOWNLOAD,
                "Pictures": GLib.UserDirectory.DIRECTORY_PICTURES,
                "Videos": GLib.UserDirectory.DIRECTORY_VIDEOS,
                "Music": GLib.UserDirectory.DIRECTORY_MUSIC,
            }[name]
            d = GLib.get_user_special_dir(special)
            if d and Path(d).is_dir() and Path(d) != HOME:
                out.append((name, Path(d)))
        for mount in Gio.VolumeMonitor.get().get_mounts():
            root = mount.get_root().get_path()
            if root and not mount.is_shadowed():
                out.append((mount.get_name(), Path(root)))
        res = [{"name": n, "path": str(p)} for n, p in out]
        seen = {r["path"] for r in res}
        for d in usb_drives():
            if d["path"] not in seen:
                res.append(d)
        res += self.cloud_places()
        res.append({"name": "Trash", "path": str(HOME / ".local/share/Trash/files")})
        return res

    def cloud_places(self):
        """Every Dropbox and Google Drive account: the local copy once folders are chosen, else the setup page."""
        out = []
        for key, p in kumo.accounts().items():
            ready = kumo.connected(key) and kumo.folders(key) and p["local"].is_dir()
            out.append(
                {
                    "name": p["name"],
                    "cloud": key,
                    "what": kumo.describe(key),
                    "path": str(p["local"]) if ready else "cloud:" + key,
                }
            )
        return out

    def cloud_syncs(self):
        return {c["cloud"]: kumo.status(c["cloud"]).get("last") for c in self.clouds}

    def watch(self):
        """Told, not asking: the kernel says when a disk appears or goes (/dev), the system when
        something is mounted, and Kumo's status file when a sync starts or ends. Nothing is
        checked on a timer except the "synced 3 min ago" words, twice a minute."""
        self.watch_due = 0
        self.monitors = []

        def soon(*_):
            if not self.watch_due:
                self.watch_due = GLib.timeout_add(400, fire)

        def fire():
            self.watch_due = 0
            self.watch_drives()
            return False

        dev = Gio.File.new_for_path("/dev").monitor_directory(Gio.FileMonitorFlags.NONE, None)
        dev.connect("changed", lambda _m, f, _o, _e: f.get_basename().startswith(("sd", "nvme", "mmcblk")) and soon())
        self.monitors.append(dev)
        mounts = Gio.UnixMountMonitor.get()
        mounts.connect("mounts-changed", soon)
        self.monitors.append(mounts)
        for f in (kumo.STATUS, kumo.CHOICES):
            m = Gio.File.new_for_path(str(f)).monitor_file(Gio.FileMonitorFlags.NONE, None)
            m.connect("changed", soon)
            self.monitors.append(m)
        GLib.timeout_add_seconds(30, lambda: self.watch_drives() or True)

    def watch_drives(self):
        """Plugging a stick in or out shows up in the places without a reload."""
        clouds = self.cloud_places()
        if clouds != self.clouds:
            # A sync finished (or started): new words in the side bar, and new files if we are in there.
            was, self.clouds = self.cloud_last, clouds
            self.cloud_last = self.cloud_syncs()
            if self.folder is not None and self.cloud_mode is None:
                key, _ = kumo.which_cloud(self.folder)
                self.js(
                    "cloudTick",
                    {"places": self.places(), "reload": bool(key) and self.cloud_last.get(key) != was.get(key)},
                )
        now = [d["path"] for d in usb_drives()]
        if now != self.drives:
            self.drives = now
            if self.folder is not None and self.folder.is_dir():
                self.browse(self.folder)
            elif self.folder is not None:
                self.browse(HOME)
        return True

    def mount(self, dev):
        r = subprocess.run(["udisksctl", "mount", "-b", dev, "--no-user-interaction"], capture_output=True, text=True)
        point = next((d["path"] for d in usb_drives() if d.get("dev") == dev and not d.get("mount")), None)
        if point:
            self.drives = [d["path"] for d in usb_drives()]
            self.browse(point)
        else:
            self.js("toast", "Could not open the drive: " + (r.stderr.strip() or r.stdout.strip() or "unknown error"))

    def eject(self, dev):
        here = self.folder
        mp = next((d["path"] for d in usb_drives() if d.get("dev") == dev), "")
        subprocess.run(["sync"])
        r = subprocess.run(["udisksctl", "unmount", "-b", dev, "--no-user-interaction"], capture_output=True, text=True)
        if r.returncode != 0:
            self.js("toast", "Could not eject: close anything using the drive. " + r.stderr.strip())
            return
        self.drives = [d["path"] for d in usb_drives()]
        self.js("toast", "Safe to remove")
        if here is not None and mp and str(here).startswith(mp):
            self.browse(HOME)
        elif here is not None:
            self.browse(here)

    def browse(self, folder, select=None):
        folder = Path(folder)
        self.cloud_mode = None
        entries = []
        try:
            items = list(folder.iterdir())
        except OSError as e:
            self.js("toast", f"Cannot open {folder.name or folder}: {e.strerror or e}")
            return
        self.folder = folder
        for p in items:
            if p.name.startswith(".") and not self.hidden:
                continue
            try:
                st = p.stat()
                is_dir = p.is_dir()
            except OSError:
                continue
            mtime = datetime.fromtimestamp(st.st_mtime)
            when = mtime.strftime("%-d %b %Y") if mtime.year != datetime.now().year else mtime.strftime("%-d %b, %H:%M")
            entry = {"name": p.name, "path": str(p), "dir": is_dir, "when": when, "t": st.st_mtime}
            if is_dir:
                try:
                    entry["size"] = f"{sum(1 for _ in p.iterdir())} items"
                except OSError:
                    entry["size"] = ""
                entry["kind"] = "Folder"
            else:
                mime, desc = mime_of(p)
                entry["size"] = human_size(st.st_size)
                entry["kind"] = (desc[:1].upper() + desc[1:]) if desc else mime
                if mime in WEB_IMAGES and st.st_size < 15_000_000:
                    entry["thumb"] = p.as_uri()
            entries.append(entry)
        entries.sort(key=lambda e: (not e["dir"], natural(e["name"])))
        crumbs, p = [], folder
        while True:
            crumbs.append({"name": "Home" if p == HOME else (p.name or "Computer"), "path": str(p)})
            if p == HOME or p.parent == p:
                break
            p = p.parent
        self.win.set_title(f"{folder.name or folder} — Miru")
        key, _ = kumo.which_cloud(folder)
        cloud = (
            {"key": key, "name": (kumo.account(key) or {}).get("name", key), "status": kumo.describe(key)}
            if key
            else None
        )
        self.js(
            "browse",
            {
                "cloud": cloud,
                "path": str(folder),
                "crumbs": crumbs[::-1],
                "entries": entries,
                "places": self.places(),
                "select": select,
                "hidden": self.hidden,
                "trash": folder == HOME / ".local/share/Trash/files",
            },
        )

    def trash(self, paths):
        done = 0
        for path in paths:
            try:
                Gio.File.new_for_path(path).trash(None)
                done += 1
            except GLib.Error as e:
                self.js("toast", f"Could not move {Path(path).name} to the Trash: {e.message}")
        if done:
            kumo.nudge(paths[0])
            self.js("toast", f"Moved {Path(paths[0]).name if done == 1 else f'{done} items'} to the Trash")
        self.browse(self.folder)

    def rename(self, path, name):
        src = Path(path)
        name = name.strip()
        if not name or "/" in name or name == src.name:
            return
        dst = src.with_name(name)
        if dst.exists():
            self.js("toast", f"{name} already exists here")
            return
        try:
            src.rename(dst)
        except OSError as e:
            self.js("toast", f"Could not rename: {e.strerror}")
            return
        kumo.nudge(dst)
        self.browse(self.folder, select=name)

    def mkdir(self, name):
        if self.folder is None:
            return
        base, n = name.strip() or "New folder", 1
        target = self.folder / base
        while target.exists():
            n += 1
            target = self.folder / f"{base} {n}"
        try:
            target.mkdir()
        except OSError as e:
            self.js("toast", f"Could not make a folder: {e.strerror}")
            return
        kumo.nudge(target)
        self.browse(self.folder, select=target.name)

    # --- the right-click menu ----------------------------------------------------
    def unique(self, folder, name):
        target, stem, ext, n = folder / name, Path(name).stem, Path(name).suffix, 1
        if (folder / name).is_dir() or not ext:
            stem, ext = name, ""
        while target.exists():
            n += 1
            target = folder / f"{stem} {n}{ext}"
        return target

    def copy_into(self, src, folder, move=False):
        src = Path(src)
        dst = self.unique(folder, src.name if not (move and src.parent == folder) else src.name)
        if move:
            if src.parent == folder:
                return src
            shutil.move(str(src), str(dst))
        elif src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)
        return dst

    def file_op(self, msg):
        kind = msg["type"]
        paths = [Path(p) for p in (msg.get("paths") or [msg.get("path") or "."])]
        path = paths[0]
        what = path.name if len(paths) == 1 else f"{len(paths)} items"
        trash_files = HOME / ".local/share/Trash/files"
        try:
            if kind in ("duplicate", "paste", "restore"):
                kumo.nudge(self.folder)
                if kind == "paste" and self.held and self.held[0] == "cut":
                    kumo.nudge(self.held[1][0])
            if kind == "duplicate":
                made = [self.copy_into(p, p.parent).name for p in paths]
                return self.browse(self.folder, select=made)
            if kind in ("copy", "cut"):
                self.held = (kind, [str(p) for p in paths])
                Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text("\n".join(map(str, paths)), -1)
                return self.js(
                    "toast",
                    f"{'Copied' if kind == 'copy' else 'Ready to move'} {what}. "
                    f"Paste {'it' if len(paths) == 1 else 'them'} in any folder.",
                )
            if kind == "paste":
                if not self.held:
                    return self.js("toast", "Nothing to paste")
                how, held = self.held
                made = [self.copy_into(Path(p), self.folder, move=how == "cut").name for p in held]
                if how == "cut":
                    self.held = None
                return self.browse(self.folder, select=made)
            if kind == "copypath":
                Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text("\n".join(map(str, paths)), -1)
                return self.js("toast", "Path copied" if len(paths) == 1 else f"{len(paths)} paths copied")
            if kind == "restore":
                for path in paths:
                    info = HOME / ".local/share/Trash/info" / (path.name + ".trashinfo")
                    orig = re.search(r"^Path=(.*)$", info.read_text(), re.M) if info.exists() else None
                    dest = Path(urllib.parse.unquote(orig.group(1))) if orig else HOME / path.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if dest.exists():
                        dest = self.unique(dest.parent, dest.name)
                    shutil.move(str(path), str(dest))
                    info.unlink(missing_ok=True)
                self.js("toast", f"Put back in {dest.parent.name or '/'}" if len(paths) == 1 else f"Put back {what}")
                return self.browse(self.folder)
            if kind in ("forget", "emptytrash"):
                targets = paths if kind == "forget" else list(trash_files.iterdir())
                for t in targets:
                    if t.is_dir() and not t.is_symlink():
                        shutil.rmtree(t)
                    else:
                        t.unlink(missing_ok=True)
                    (HOME / ".local/share/Trash/info" / (t.name + ".trashinfo")).unlink(missing_ok=True)
                self.js("toast", "Deleted for good" if kind == "forget" else "The Trash is empty")
                return self.browse(self.folder)
            if kind == "markup":
                return subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve().parent / "kuhaku-shot.py"), "edit", str(path)],
                    start_new_session=True,
                )
        except Exception as e:
            self.js("toast", f"Could not do that: {getattr(e, 'strerror', None) or e}"[:140])

    # --- files -------------------------------------------------------------
    def siblings(self):
        try:
            names = [p for p in self.path.parent.iterdir() if p.is_file() and not p.name.startswith(".")]
        except OSError:
            names = [self.path]
        return sorted(names, key=lambda p: natural(p.name))

    def show(self, path):
        self.path = path
        self.files = self.siblings()
        try:
            i = self.files.index(path)
        except ValueError:
            self.files.insert(0, path)
            i = 0
        mime, desc = mime_of(path)
        kind = kind_of(path, mime)
        try:
            st = path.stat()
            size, modified = st.st_size, datetime.fromtimestamp(st.st_mtime)
        except OSError:
            size, modified = 0, None
        when = ""
        if modified:
            when = (
                modified.strftime("%-d %b %Y, %H:%M")
                if modified.year != datetime.now().year
                else modified.strftime("%-d %b, %H:%M")
            )
        item = {
            "name": path.name,
            "uri": path.as_uri(),
            "kind": kind,
            "mime": mime,
            "type": desc[:1].upper() + desc[1:] if desc else mime,
            "size": human_size(size),
            "when": when,
            "index": i + 1,
            "count": len(self.files),
            "inFolder": self.folder is not None,
        }
        if path.suffix.lower() in MESH_TYPES:
            kind = item["kind"] = "model"
            self.load_model(path)
        elif kind == "image" and mime not in WEB_IMAGES:
            item["src"] = "miru://image?" + urllib.parse.urlencode({"p": str(path)})
        elif kind in ("image", "video", "audio"):
            item["src"] = path.as_uri()
        elif kind == "pdf":
            pages, ratio = pdf_pages(path)
            item["pages"], item["ratio"] = pages, ratio
            item["src"] = "miru://pdf?" + urllib.parse.urlencode({"p": str(path)})
        elif kind == "text":
            try:
                with open(path, "rb") as f:
                    data = f.read(TEXT_LIMIT + 1)
                item["text"] = data[:TEXT_LIMIT].decode("utf-8", "replace")
                item["cut"] = len(data) > TEXT_LIMIT
            except OSError as e:
                item["kind"], item["error"] = "other", str(e)
        app = Gio.AppInfo.get_default_for_type(Gio.content_type_from_mime_type(mime) or mime, False)
        item["app"] = app.get_name() if app and app.get_id() != "miru.desktop" else ""
        self.win.set_title(f"{path.name} — Miru")
        self.view.evaluate_javascript(f"show({json.dumps(item)})", -1, None, None, None, None, None)

    def step(self, d):
        if len(self.files) > 1:
            i = self.files.index(self.path) if self.path in self.files else 0
            self.show(self.files[(i + d) % len(self.files)])

    def open_in_app(self, path=None):
        if path:
            self.path = Path(path)
        mime, _ = mime_of(self.path)
        for app in Gio.AppInfo.get_all_for_type(Gio.content_type_from_mime_type(mime) or mime):
            if app.get_id() != "miru.desktop":
                app.launch([Gio.File.new_for_path(str(self.path))], None)
                return
        Gio.AppInfo.launch_default_for_uri(self.path.as_uri(), None)

    # --- the page ------------------------------------------------------------
    def on_message(self, _m, result):
        try:
            msg = json.loads(result.get_js_value().to_string())
        except Exception:
            report()
            return
        kind = msg.get("type") or ""
        handler = MESSAGES.get(kind)
        if handler:
            handler(self, msg)
        elif kind.startswith("cloud-"):
            self.cloud_msg(kind[6:], msg)

    def nav(self, msg):
        target = msg.get("path", str(HOME))
        if target.startswith("mount:"):
            self.mount(target[6:])
        elif target.startswith("cloud:"):
            self.cloud_page(target[6:])
        else:
            self.browse(target, select=msg.get("select"))

    def toggle_hidden(self):
        self.hidden = not self.hidden
        self.browse(self.folder)

    def open_terminal(self):
        subprocess.Popen(
            [str(HOME / ".local/bin/kuhaku-terminal")], cwd=str(self.folder or HOME), start_new_session=True
        )

    # --- Dropbox and Google Drive ----------------------------------------------------
    def cloud_page(self, key, phase=None, error=None):
        """Sign in, then pick the folders to keep here; everything else stays in the cloud."""
        p = kumo.account(key)
        if not p:
            return
        self.cloud_mode = key
        self.folder = HOME
        phase = phase or ("choose" if kumo.connected(key) else "connect")
        self.win.set_title(f"{p['name']} — Miru")
        kind = kumo.KINDS[p["kind"]]["name"]
        self.js(
            "cloud",
            {
                "key": key,
                "name": p["name"] if p["first"] or kumo.connected(key) else f"Another {kind}",
                "kind": kind,
                "first": p["first"],
                "email": p["email"],
                "phase": phase,
                "error": error,
                "local": str(p["local"]).replace(str(HOME), "~", 1),
                "folders": kumo.folders(key),
                "places": self.places(),
                "gdocs": p["kind"] == "drive",
            },
        )

    def cloud_msg(self, what, msg):
        if what == "add":
            # One more account of a kind: its own sign-in page, its own folder.
            if msg.get("kind") in kumo.KINDS:
                self.cloud_page(kumo.new_key(msg["kind"]), "connect")
            return
        key = msg.get("key")
        acct = kumo.account(key)
        if not acct:
            return
        if what == "connect":

            def opened(url):
                # A further account signs in in a private Mado window: no cookies from the first
                # account, so the other one can be chosen.
                argv = [str(HOME / ".local/bin/mado")] + ([] if acct["first"] else ["--private"]) + [url]
                subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            def done(err):
                def back():
                    self.win.present()
                    if self.cloud_mode == key:
                        self.cloud_page(key, None if not err else "connect", err)
                    return False

                GLib.idle_add(back)

            self.cloud_page(key, "waiting")
            kumo.connect(key, opened, done, label=str(msg.get("label") or "")[:40])
        elif what == "list":
            path = msg.get("path", "")

            def work():
                try:
                    payload = {"key": key, "path": path, "dirs": kumo.list_folders(key, path)}
                except Exception as e:
                    payload = {"key": key, "path": path, "error": str(e)[:160]}
                GLib.idle_add(lambda: self.js("cloudList", payload) and False)

            threading.Thread(target=work, daemon=True).start()
        elif what == "save":
            chosen = kumo.tidy(msg.get("folders") or [])
            kumo.choose(key, chosen)
            if not chosen:
                self.js("toast", "Nothing will sync. Folders already here stay.")
                return self.cloud_page(key)
            base = kumo.local_base(key)
            for f in chosen:
                (base / f).mkdir(parents=True, exist_ok=True)
            kumo.ensure_daemon()
            self.clouds = self.cloud_places()
            self.js(
                "toast",
                f"Syncing {len(chosen)} folder{'s' if len(chosen) != 1 else ''} with {(kumo.account(key) or {}).get('name', key)}",
            )
            self.browse(base)
        elif what == "now":
            kumo.nudge()
            kumo.ensure_daemon()
            self.js("toast", "Syncing now")
        elif what == "disconnect":
            name = acct["name"]
            kumo.disconnect(key)
            self.clouds = self.cloud_places()
            self.js("toast", f"Signed out of {name}. Nothing was deleted, here or in the cloud.")
            if acct["first"]:
                self.cloud_page(key)
            else:
                self.cloud_mode = None
                self.browse(HOME)

    def on_policy(self, _v, decision, kind):
        if kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri.startswith(("http://", "https://")):
                subprocess.Popen(["xdg-open", uri], start_new_session=True)
                decision.ignore()
                return True
        return False

    def load_model(self, path):
        """Reads the mesh off the main thread, then hands it to the page's viewer."""

        def work():
            import base64

            try:
                arr = load_mesh(path)
                tris = len(arr) // 9
                if tris > MESH_LIMIT:
                    payload: dict = {"path": str(path), "error": f"{tris:,} triangles is too many to show here."}
                elif not tris:
                    payload = {"path": str(path), "error": "No triangles in this file."}
                else:
                    payload = {"path": str(path), "tris": tris, "data": base64.b64encode(arr.tobytes()).decode()}
            except Exception as e:
                payload = {"path": str(path), "error": f"This model cannot be shown here ({e})."}

            def done():
                if self.path == path:
                    self.js("showModel", payload)
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()

    def on_scheme(self, request):
        """miru://pdf?p=FILE&n=PAGE&w=WIDTH renders one PDF page;
        miru://image?p=FILE turns pictures WebKit cannot read (HEIC, AVIF, TIFF) into PNG."""
        url = urllib.parse.urlparse(request.get_uri())
        q = urllib.parse.parse_qs(url.query)
        path = q.get("p", [""])[0]

        def work():
            try:
                if url.netloc == "pdf":
                    n = int(q.get("n", ["1"])[0])
                    width = max(200, min(3000, int(q.get("w", ["1200"])[0])))
                    import tempfile

                    with tempfile.TemporaryDirectory() as tmp:
                        subprocess.run(
                            [
                                "pdftoppm",
                                "-f",
                                str(n),
                                "-l",
                                str(n),
                                "-scale-to-x",
                                str(width),
                                "-scale-to-y",
                                "-1",
                                "-png",
                                "-singlefile",
                                path,
                                f"{tmp}/page",
                            ],
                            capture_output=True,
                            timeout=60,
                        )
                        data = Path(f"{tmp}/page.png").read_bytes()
                    mime = "image/png"
                else:
                    pix = GdkPixbuf.Pixbuf.new_from_file(path)
                    pix = pix.apply_embedded_orientation() or pix
                    _ok, out = pix.save_to_bufferv("png", [], [])
                    data, mime = bytes(out), "image/png"
                result = (data, mime) if data else None
            except Exception:
                result = None

            def done():
                if result:
                    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(result[0]))
                    request.finish(stream, len(result[0]), result[1])
                else:
                    request.finish_error(
                        GLib.Error.new_literal(Gio.io_error_quark(), "cannot show", Gio.IOErrorEnum.FAILED)
                    )
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()


PAGE_URI = (Path(__file__).resolve().parent / "miru/miru.html").as_uri()  # its CSS and script sit beside it
PAGE = (Path(__file__).resolve().parent / "miru/miru.html").read_text(encoding="utf-8")


# What Miru's page may say, and who listens (cloud-* messages go to cloud_msg).
MESSAGES = {
    "close": lambda m, msg: m.win.destroy(),
    "next": lambda m, msg: m.step(1),
    "prev": lambda m, msg: m.step(-1),
    "open": lambda m, msg: m.open_in_app(msg.get("path")),
    "folder": lambda m, msg: m.browse(m.path.parent, select=m.path.name),  # from a preview: back, the file chosen
    "nav": lambda m, msg: m.nav(msg),
    "eject": lambda m, msg: m.eject(msg.get("dev", "")),
    "preview": lambda m, msg: m.show(Path(msg["path"])),
    "trash": lambda m, msg: m.trash(msg.get("paths") or [msg["path"]]),
    **{
        k: (lambda m, msg: m.file_op(msg))
        for k in ("duplicate", "copy", "cut", "paste", "copypath", "restore", "forget", "emptytrash", "markup")
    },
    "rename": lambda m, msg: m.rename(msg["path"], msg.get("name", "")),
    "mkdir": lambda m, msg: m.mkdir(msg.get("name", "")),
    "hidden": lambda m, msg: m.toggle_hidden(),
    "terminal": lambda m, msg: m.open_terminal(),
    "minimize": lambda m, msg: m.win.iconify(),
}


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    arg = sys.argv[1] if len(sys.argv) > 1 else str(HOME)
    if arg.startswith("file://"):
        arg = urllib.parse.unquote(urllib.parse.urlparse(arg).path)
    Miru(arg)
    Gtk.main()
