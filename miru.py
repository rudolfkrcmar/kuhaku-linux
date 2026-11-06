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
import html
import json
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, WebKit2

HOME = Path.home()
TEXT_LIMIT = 1_000_000
WEB_IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml", "image/bmp", "image/x-icon",
              "image/vnd.microsoft.icon"}
TEXT_TYPES = {"application/json", "application/xml", "application/javascript", "application/x-shellscript",
              "application/x-yaml", "application/toml", "application/x-desktop", "application/sql",
              "application/x-python", "application/x-perl", "application/x-ruby", "application/x-php"}


def theme():
    """Follow Kūhaku: its saved choice, else the clock."""
    def load(p):
        try:
            return json.loads(p.read_text())
        except Exception:
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
        pass
    return "other"


def pdf_pages(path):
    try:
        out = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"^Pages:\s+(\d+)", out, re.M)
        size = re.search(r"^Page size:\s+([\d.]+) x ([\d.]+)", out, re.M)
        ratio = float(size.group(2)) / float(size.group(1)) if size else 1.414
        return (int(m.group(1)) if m else 0), ratio
    except Exception:
        return 0, 1.414


class Miru:
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
        self.folder = None          # the folder being browsed, if Miru is the Files app
        self.hidden = False
        self.view.load_html(PAGE.replace("{THEME}", "night" if self.night else "day"), "file:///")
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
            special = {"Desktop": GLib.UserDirectory.DIRECTORY_DESKTOP,
                       "Documents": GLib.UserDirectory.DIRECTORY_DOCUMENTS,
                       "Downloads": GLib.UserDirectory.DIRECTORY_DOWNLOAD,
                       "Pictures": GLib.UserDirectory.DIRECTORY_PICTURES,
                       "Videos": GLib.UserDirectory.DIRECTORY_VIDEOS,
                       "Music": GLib.UserDirectory.DIRECTORY_MUSIC}[name]
            d = GLib.get_user_special_dir(special)
            if d and Path(d).is_dir() and Path(d) != HOME:
                out.append((name, Path(d)))
        for mount in Gio.VolumeMonitor.get().get_mounts():
            root = mount.get_root().get_path()
            if root and not mount.is_shadowed():
                out.append((mount.get_name(), Path(root)))
        trash = HOME / ".local/share/Trash/files"
        out.append(("Trash", trash))
        return [{"name": n, "path": str(p)} for n, p in out]

    def browse(self, folder, select=None):
        folder = Path(folder)
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
            e = {"name": p.name, "path": str(p), "dir": is_dir, "when": when, "t": st.st_mtime}
            if is_dir:
                try:
                    e["size"] = f"{sum(1 for _ in p.iterdir())} items"
                except OSError:
                    e["size"] = ""
                e["kind"] = "Folder"
            else:
                mime, desc = mime_of(p)
                e["size"] = human_size(st.st_size)
                e["kind"] = (desc[:1].upper() + desc[1:]) if desc else mime
                if mime in WEB_IMAGES and st.st_size < 15_000_000:
                    e["thumb"] = p.as_uri()
            entries.append(e)
        entries.sort(key=lambda e: (not e["dir"], natural(e["name"])))
        crumbs, p = [], folder
        while True:
            crumbs.append({"name": "Home" if p == HOME else (p.name or "Computer"), "path": str(p)})
            if p == HOME or p.parent == p:
                break
            p = p.parent
        self.win.set_title(f"{folder.name or folder} — Miru")
        self.js("browse", {"path": str(folder), "crumbs": crumbs[::-1], "entries": entries,
                           "places": self.places(), "select": select, "hidden": self.hidden,
                           "trash": folder == HOME / ".local/share/Trash/files"})

    def trash(self, paths):
        done = 0
        for path in paths:
            try:
                Gio.File.new_for_path(path).trash(None)
                done += 1
            except GLib.Error as e:
                self.js("toast", f"Could not move {Path(path).name} to the Trash: {e.message}")
        if done:
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
        self.browse(self.folder, select=name)

    def mkdir(self, name):
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
        self.browse(self.folder, select=target.name)

    # --- the right-click menu ----------------------------------------------------
    def unique(self, folder, name):
        target, stem, ext, n = folder / name, Path(name).stem, Path(name).suffix, 1
        if Path(name).is_dir() or not ext:
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
            if kind == "duplicate":
                made = [self.copy_into(p, p.parent).name for p in paths]
                return self.browse(self.folder, select=made)
            if kind in ("copy", "cut"):
                self.held = (kind, [str(p) for p in paths])
                Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text("\n".join(map(str, paths)), -1)
                return self.js("toast", f"{'Copied' if kind == 'copy' else 'Ready to move'} {what}. "
                                        f"Paste {'it' if len(paths) == 1 else 'them'} in any folder.")
            if kind == "paste":
                if not getattr(self, "held", None):
                    return self.js("toast", "Nothing to paste")
                how, paths = self.held
                made = [self.copy_into(p, self.folder, move=how == "cut").name for p in paths]
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
                return subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "kuhaku-shot.py"), "edit", str(path)],
                                        start_new_session=True)
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
            when = modified.strftime("%-d %b %Y, %H:%M") if modified.year != datetime.now().year \
                else modified.strftime("%-d %b, %H:%M")
        item = {"name": path.name, "uri": path.as_uri(), "kind": kind, "mime": mime,
                "type": desc[:1].upper() + desc[1:] if desc else mime, "size": human_size(size),
                "when": when, "index": i + 1, "count": len(self.files), "inFolder": self.folder is not None}
        if kind == "image" and mime not in WEB_IMAGES:
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
            return
        kind = msg.get("type")
        if kind == "close":
            self.win.destroy()
        elif kind == "next":
            self.step(1)
        elif kind == "prev":
            self.step(-1)
        elif kind == "open":
            self.open_in_app(msg.get("path"))
        elif kind == "folder":
            # From a preview: back to its folder, with the file chosen.
            self.browse(self.path.parent, select=self.path.name)
        elif kind == "nav":
            self.browse(msg.get("path", str(HOME)), select=msg.get("select"))
        elif kind == "preview":
            self.show(Path(msg["path"]))
        elif kind == "trash":
            self.trash(msg.get("paths") or [msg["path"]])
        elif kind in ("duplicate", "copy", "cut", "paste", "copypath", "restore", "forget", "emptytrash", "markup"):
            self.file_op(msg)
        elif kind == "rename":
            self.rename(msg["path"], msg.get("name", ""))
        elif kind == "mkdir":
            self.mkdir(msg.get("name", ""))
        elif kind == "hidden":
            self.hidden = not self.hidden
            self.browse(self.folder)
        elif kind == "terminal":
            subprocess.Popen([str(HOME / ".local/bin/kuhaku-terminal")], cwd=str(self.folder or HOME),
                             start_new_session=True)
        elif kind == "minimize":
            self.win.iconify()

    def on_policy(self, _v, decision, kind):
        if kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri.startswith(("http://", "https://")):
                subprocess.Popen(["xdg-open", uri], start_new_session=True)
                decision.ignore()
                return True
        return False

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
                        subprocess.run(["pdftoppm", "-f", str(n), "-l", str(n), "-scale-to-x", str(width),
                                        "-scale-to-y", "-1", "-png", "-singlefile", path, f"{tmp}/page"],
                                       capture_output=True, timeout=60)
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
                    request.finish_error(GLib.Error.new_literal(Gio.io_error_quark(), "cannot show",
                                                                Gio.IOErrorEnum.FAILED))
                return False
            GLib.idle_add(done)
        threading.Thread(target=work, daemon=True).start()


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8">
<style>
  :root { --bg:#f7f4f1; --fg:#111111; --muted:#666666; --faint:rgba(17,17,17,.07); --accent:#e0301e; --sheet:#ffffff; }
  body.night { --bg:#121212; --fg:#ececec; --muted:#9a9a9a; --faint:rgba(236,236,236,.07); --accent:#f0705a; --sheet:#1b1b1b; }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--fg); overflow:hidden;
               font-family:'Inter Variable','Inter',system-ui,sans-serif; -webkit-font-smoothing:antialiased; }
  .bar { position:fixed; top:0; left:0; right:0; height:52px; display:flex; align-items:center; gap:14px;
         padding:0 20px 0 32px; z-index:3; background:var(--bg); }
  .bar::after { content:''; position:absolute; left:0; right:0; bottom:0; height:1px; background:var(--faint); }
  .line { width:2px; height:20px; background:var(--accent); flex-shrink:0; }
  .name { font-size:14px; font-weight:400; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0; }
  .count { font-size:13px; font-weight:300; color:var(--muted); white-space:nowrap; }
  .grow { flex:1; }
  .bar button { border:0; background:none; color:var(--muted); font:inherit; font-size:13px; font-weight:300;
                cursor:pointer; padding:6px 10px; border-radius:0; transition:color .15s, background .15s; outline:none; }
  .bar button:hover { color:var(--fg); background:var(--faint); }
  .bar button.icon { width:32px; height:32px; padding:0; display:grid; place-items:center; }
  .stage { position:fixed; top:53px; left:0; right:0; bottom:0; overflow:auto; scrollbar-width:none; }
  .stage::-webkit-scrollbar { display:none; }
  .center { min-height:100%; display:flex; align-items:center; justify-content:center; box-sizing:border-box; padding:40px; }
  .center img.fit { max-width:100%; max-height:calc(100vh - 133px); object-fit:contain; cursor:zoom-in; }
  .center img.full { max-width:none; max-height:none; cursor:zoom-out; }
  video { max-width:100%; max-height:calc(100vh - 133px); outline:none; background:#000; }
  .audio { text-align:center; }
  .audio .big { font-size:30px; font-weight:200; letter-spacing:-.01em; margin-bottom:36px; }
  audio { width:min(520px, 80vw); }
  .pages { display:flex; flex-direction:column; align-items:center; gap:28px; padding:40px 0 80px; }
  .pages .pg { background:var(--sheet); box-shadow:0 0 0 1px var(--faint), 0 12px 40px rgba(0,0,0,.10); }
  .pages img { display:block; width:100%; height:100%; }
  pre { margin:0; padding:40px 48px 80px; font-family:'JetBrains Mono','DejaVu Sans Mono',monospace; font-size:13.5px;
        line-height:1.65; white-space:pre-wrap; word-break:break-word; tab-size:4; counter-reset:ln; }
  pre .l { display:block; padding-left:4.2em; position:relative; }
  pre .l::before { counter-increment:ln; content:counter(ln); position:absolute; left:0; width:3em; text-align:right;
                   color:var(--muted); opacity:.45; }
  .note { padding:0 48px 60px; font-size:13px; color:var(--muted); font-weight:300; }
  .other { text-align:center; }
  .other .big { font-size:30px; font-weight:200; letter-spacing:-.01em; margin:0 0 12px; }
  .other .sub { font-size:14px; font-weight:300; color:var(--muted); margin-bottom:36px; }
  .other button { border:0; background:none; font:inherit; font-size:15px; font-weight:300; color:var(--fg); cursor:pointer;
                  padding:0; border-bottom:1.5px solid var(--accent); padding-bottom:3px; }
  .mark { width:72px; height:72px; margin:0 auto 32px; position:relative; }
  .mark i { position:absolute; left:4px; right:4px; top:0; height:3px; background:var(--accent); }
  .mark b { position:absolute; left:8px; right:8px; top:16px; bottom:0; border:2.5px solid var(--fg); }
  .meta { position:fixed; left:0; right:0; bottom:18px; text-align:center; font-size:12px; font-weight:300; color:var(--muted);
          letter-spacing:.02em; pointer-events:none; opacity:.9; z-index:2; }
  .meta span { background:var(--bg); padding:4px 10px; }
  .edge { position:fixed; top:53px; bottom:0; width:80px; z-index:2; cursor:pointer; display:grid; place-items:center;
          color:var(--muted); opacity:0; transition:opacity .2s; }
  .edge:hover { opacity:1; }
  .edge.l { left:0; } .edge.r { right:0; }
  /* Files: places on the left, the folder as a calm list. */
  .side { position:fixed; top:53px; left:0; bottom:0; width:220px; padding:28px 0 0 32px; box-sizing:border-box;
          display:none; overflow:auto; scrollbar-width:none; }
  body.folder .side { display:block; }
  body.folder .stage { left:220px; }
  body.folder .edge, body.folder #open, body.folder #folder { display:none; }
  #newf, #hid { display:none; }
  body.folder #newf, body.folder #hid { display:inline-block; }
  .side a { display:block; padding:7px 0 7px 14px; margin-left:-14px; font-size:14px; font-weight:300; color:var(--muted);
            cursor:pointer; position:relative; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .side a:hover { color:var(--fg); }
  .side a.on { color:var(--fg); }
  .side a.on::before { content:''; position:absolute; left:0; top:9px; bottom:9px; width:2px; background:var(--accent); }
  .side .gap { height:18px; }
  .crumbs { display:flex; align-items:center; gap:6px; font-size:14px; min-width:0; overflow:hidden; white-space:nowrap; }
  .crumbs span { color:var(--muted); font-weight:300; cursor:pointer; }
  .crumbs span:hover { color:var(--fg); }
  .crumbs span.last { color:var(--fg); font-weight:400; cursor:default; }
  .crumbs i { color:var(--muted); opacity:.5; font-style:normal; }
  .list { padding:18px 40px 80px 12px; }
  .row { display:grid; grid-template-columns:30px minmax(0,1fr) 190px 100px 130px; align-items:center; gap:14px;
         height:40px; padding:0 14px; font-size:14px; font-weight:300; position:relative; cursor:default; user-select:none; }
  .row:hover { background:var(--faint); }
  .row.sel { background:var(--faint); }
  .row.sel::before { content:''; position:absolute; left:0; top:10px; bottom:10px; width:2px; background:var(--accent); }
  .row .nm { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .row .k, .row .z, .row .w { color:var(--muted); font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .row .z { text-align:right; }
  .row .ic { width:24px; height:24px; display:grid; place-items:center; color:var(--muted); }
  .row .ic img { width:24px; height:24px; object-fit:cover; }
  .row.sel .ic { color:var(--fg); }
  .row input { font:inherit; color:var(--fg); background:var(--bg); border:0; outline:none; padding:4px 0; width:100%;
               box-shadow:inset 0 -1.5px 0 var(--accent); caret-color:var(--accent); }
  .head { display:grid; grid-template-columns:30px minmax(0,1fr) 190px 100px 130px; gap:14px; padding:0 14px 8px;
          font-size:11px; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); font-weight:400; }
  .head .z { text-align:right; }
  .empty { padding:120px 0; text-align:center; color:var(--muted); font-size:15px; font-weight:300; }
  .toast { position:fixed; left:50%; bottom:28px; transform:translateX(-50%); background:var(--fg); color:var(--bg);
           font-size:13px; font-weight:300; padding:9px 16px; z-index:9; opacity:0; transition:opacity .25s; pointer-events:none; }
  .toast.show { opacity:1; }
  /* Right click: a small card of what you can do here. */
  .ctx { position:fixed; z-index:9; min-width:210px; padding:8px; background:var(--sheet); border-radius:12px;
         box-shadow:0 0 0 1px var(--faint), 0 18px 48px rgba(0,0,0,.18); font-size:14px; font-weight:300; }
  .ctx a { display:block; padding:7px 12px; border-radius:8px; cursor:pointer; color:var(--fg); white-space:nowrap; }
  .ctx a:hover { background:var(--faint); }
  .ctx a.bad:hover { color:#e5484d; }
  .ctx hr { border:0; height:1px; background:var(--faint); margin:6px 4px; }
  /* Minimise and close live in Kūhaku's bar at the top of the screen; one of each is enough. */
  #min, #close { display:none !important; }
</style></head>
<body class="{THEME}">
<div class="bar">
  <span class="line"></span>
  <span class="name" id="name"></span>
  <span class="count" id="count"></span>
  <span class="grow"></span>
  <button id="hid" title="Show hidden files (Cmd+H)">Hidden</button>
  <button id="newf" title="New folder (Cmd+Shift+N)">New folder</button>
  <button id="open" title="Open in its app (Enter)"></button>
  <button id="folder" title="Show in its folder">Folder</button>
  <button class="icon" id="min" title="Minimise"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M5 12h14"/></svg></button>
  <button class="icon" id="close" title="Close (Esc)"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
</div>
<div class="side" id="side"></div>
<div class="stage" id="stage"></div>
<div class="toast" id="toast"></div>
<div class="ctx" id="ctx" hidden></div>
<div class="edge l" id="prev" title="Previous (Left)"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg></div>
<div class="edge r" id="next" title="Next (Right)"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg></div>
<div class="meta" id="meta"></div>
<script>
  const $ = (id) => document.getElementById(id);
  const send = (m) => window.webkit.messageHandlers.miru.postMessage(JSON.stringify(m));
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  let item = null, zoom = 1, mode = 'file', dir = null, sel = 0, picked = new Set(), anchor = 0, renaming = false, renameNext = false, find = '', findT;
  const FOLDER = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M3.5 6.5h6l2 2h9v10h-17z"/></svg>';
  const FILE = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M6.5 3.5h7.5l4 4v13h-11.5z"/><path d="M14 3.5v4h4"/></svg>';

  function toast(text) {
    const t = $('toast'); t.textContent = text; t.classList.add('show');
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 2600);
  }

  window.browse = (d) => {
    const same = dir && dir.path === d.path;
    const keep = same ? $('stage').scrollTop : 0;
    mode = 'folder'; dir = d; renaming = false;
    document.body.classList.add('folder');
    $('meta').innerHTML = '';
    $('name').innerHTML = '<span class="crumbs">' + d.crumbs.map((c, i) => (i ? '<i>/</i>' : '') +
      `<span class="${i === d.crumbs.length - 1 ? 'last' : ''}" data-path="${esc(c.path)}">${esc(c.name)}</span>`).join('') + '</span>';
    $('count').textContent = d.entries.length ? `${d.entries.length} ${d.entries.length === 1 ? 'item' : 'items'}` : '';
    $('hid').textContent = d.hidden ? 'Hide hidden' : 'Hidden';
    $('side').innerHTML = d.places.map((p, i) => (p.name === 'Trash' ? '<div class="gap"></div>' : '') +
      `<a data-path="${esc(p.path)}" class="${d.path === p.path ? 'on' : ''}">${esc(p.name)}</a>`).join('');
    const want = d.select == null ? [] : [].concat(d.select);
    const found = want.map((n) => d.entries.findIndex((e) => e.name === n)).filter((i) => i >= 0);
    sel = found.length ? found[found.length - 1] : Math.max(0, want.length ? -1 : same ? Math.min(sel, d.entries.length - 1) : 0);
    picked = new Set(found.length ? found : d.entries.length ? [sel] : []); anchor = found.length ? found[0] : sel;
    const st = $('stage');
    st.innerHTML = d.entries.length ? '<div class="list"><div class="head"><span></span><span>Name</span><span>Kind</span><span class="z">Size</span><span>Modified</span></div>' +
      d.entries.map((e, i) => `<div class="row${picked.has(i) ? ' sel' : ''}" data-i="${i}">` +
        `<span class="ic">${e.dir ? FOLDER : e.thumb ? `<img loading="lazy" src="${esc(e.thumb)}" alt="">` : FILE}</span>` +
        `<span class="nm">${esc(e.name)}</span><span class="k">${esc(e.kind)}</span><span class="z">${esc(e.size)}</span><span class="w">${esc(e.when)}</span></div>`).join('') + '</div>'
      : `<div class="empty">${d.trash ? 'The Trash is empty' : 'Nothing here yet'}</div>`;
    st.scrollTop = keep;
    if (d.select != null) reveal();
    paintCount();
    if (renameNext) { renameNext = false; startRename(); }
  };
  const rows = () => [...document.querySelectorAll('.row')];
  // Like Finder: a click picks one, Cmd+click adds or drops one, Shift+click (or Shift+arrows)
  // picks everything from the anchor to here.
  function choose(i, how) {
    if (!dir || !dir.entries.length) return;
    sel = Math.max(0, Math.min(dir.entries.length - 1, i));
    if (how === 'toggle') {
      if (picked.has(sel) && picked.size > 1) picked.delete(sel); else picked.add(sel);
      anchor = sel;
    } else if (how === 'range') {
      picked = new Set();
      for (let k = Math.min(anchor, sel); k <= Math.max(anchor, sel); k++) picked.add(k);
    } else {
      picked = new Set([sel]); anchor = sel;
    }
    rows().forEach((r, k) => r.classList.toggle('sel', picked.has(k)));
    paintCount();
    reveal();
  }
  function chooseAll() {
    if (!dir || !dir.entries.length) return;
    picked = new Set(dir.entries.map((_, k) => k));
    rows().forEach((r) => r.classList.add('sel'));
    paintCount();
  }
  const pickedPaths = () => [...picked].sort((a, b) => a - b).map((k) => dir.entries[k]).filter(Boolean).map((x) => x.path);
  function paintCount() {
    if (!dir) return;
    const n = dir.entries.length;
    $('count').textContent = picked.size > 1 ? `${picked.size} of ${n} chosen` : n ? `${n} ${n === 1 ? 'item' : 'items'}` : '';
  }
  function reveal() { const r = rows()[sel]; if (r) r.scrollIntoView({ block: 'nearest' }); }
  function enter(i = sel) {
    const e = dir && dir.entries[i]; if (!e) return;
    send(e.dir ? { type: 'nav', path: e.path } : { type: 'preview', path: e.path });
  }
  function up() {
    if (dir && dir.crumbs.length > 1) send({ type: 'nav', path: dir.crumbs[dir.crumbs.length - 2].path, select: dir.crumbs[dir.crumbs.length - 1].name });
  }
  function startRename() {
    const e = dir && dir.entries[sel], r = rows()[sel]; if (!e || !r) return;
    renaming = true;
    const nm = r.querySelector('.nm');
    nm.innerHTML = `<input value="${esc(e.name)}" spellcheck="false">`;
    const input = nm.querySelector('input'); input.focus();
    const dot = e.dir ? -1 : e.name.lastIndexOf('.');
    input.setSelectionRange(0, dot > 0 ? dot : e.name.length);
    const finish = (ok) => { if (!renaming) return; renaming = false;
      if (ok && input.value.trim() && input.value !== e.name) send({ type: 'rename', path: e.path, name: input.value });
      else nm.textContent = e.name; };
    input.addEventListener('keydown', (k) => { k.stopPropagation(); if (k.key === 'Enter') finish(true); if (k.key === 'Escape') finish(false); });
    input.addEventListener('blur', () => finish(true));
  }
  $('side').addEventListener('click', (e) => { const a = e.target.closest('a'); if (a) send({ type: 'nav', path: a.dataset.path }); });
  $('name').addEventListener('click', (e) => { const c = e.target.closest('.crumbs span:not(.last)'); if (c) send({ type: 'nav', path: c.dataset.path }); });
  $('stage').addEventListener('click', (e) => { const r = e.target.closest('.row'); if (r && mode === 'folder' && !renaming) choose(+r.dataset.i, e.shiftKey ? 'range' : (e.ctrlKey || e.metaKey) ? 'toggle' : null); });
  // Shift+click would otherwise also select the names' text.
  $('stage').addEventListener('mousedown', (e) => { if (e.shiftKey && e.target.closest('.row')) e.preventDefault(); });
  $('stage').addEventListener('dblclick', (e) => { const r = e.target.closest('.row'); if (r && mode === 'folder' && !renaming && !e.shiftKey && !e.ctrlKey && !e.metaKey) enter(+r.dataset.i); });
  // Right click on a file, a folder, or the empty space around them.
  function menu(x, y, list) {
    const c = $('ctx');
    c.innerHTML = list.map((it) => it === '-' ? '<hr>' : `<a data-k="${it[0]}" class="${it[2] || ''}">${esc(it[1])}</a>`).join('');
    c.hidden = false;
    c.style.left = Math.min(x, innerWidth - c.offsetWidth - 12) + 'px';
    c.style.top = Math.min(y, innerHeight - c.offsetHeight - 12) + 'px';
  }
  const closeMenu = () => { $('ctx').hidden = true; };
  addEventListener('click', (e) => { if (!e.target.closest('#ctx')) closeMenu(); });
  addEventListener('blur', closeMenu);
  $('stage').addEventListener('contextmenu', (e) => {
    if (mode !== 'folder' || renaming || !dir) return;
    e.preventDefault();
    const r = e.target.closest('.row');
    if (!r) {
      return menu(e.clientX, e.clientY, dir.trash ? [['emptytrash', 'Empty the Trash', 'bad']] :
        [['newfolder', 'New folder'], ['paste', 'Paste'], '-', ['terminal', 'Open Terminal here'], ['hidden', dir.hidden ? 'Hide hidden files' : 'Show hidden files']]);
    }
    if (!picked.has(+r.dataset.i)) choose(+r.dataset.i); else sel = +r.dataset.i;
    const x = dir.entries[sel];
    const img = !x.dir && /\.(png|jpe?g|webp|gif|bmp)$/i.test(x.name);
    if (picked.size > 1) {
      const n = picked.size;
      return menu(e.clientX, e.clientY, dir.trash ? [['restore', `Put back ${n} items`], '-', ['forget', `Delete ${n} items for good`, 'bad']] : [
        ['duplicate', 'Duplicate'], ['copy', `Copy ${n} items`], ['cut', 'Move…'], ['copypath', 'Copy paths'], '-',
        ['trash', `Move ${n} items to Trash`, 'bad']]);
    }
    menu(e.clientX, e.clientY, dir.trash ? [['restore', 'Put back'], '-', ['forget', 'Delete for good', 'bad']] : [
      ['open', x.dir ? 'Open' : 'Open'], ...(x.dir ? [] : [['openapp', 'Open in app']]), ...(img ? [['markup', 'Mark up']] : []), '-',
      ['rename', 'Rename'], ['duplicate', 'Duplicate'], ['copy', 'Copy'], ['cut', 'Move…'], ['copypath', 'Copy path'], '-',
      ['trash', 'Move to Trash', 'bad']]);
  });
  $('ctx').addEventListener('click', (e) => {
    const a = e.target.closest('[data-k]'); if (!a) return;
    const k = a.dataset.k, x = dir && dir.entries[sel];
    closeMenu();
    if (k === 'open') return enter();
    if (k === 'openapp') return send({ type: 'open', path: x.path });
    if (k === 'rename') return startRename();
    if (k === 'newfolder') return $('newf').onclick();
    if (k === 'terminal' || k === 'hidden') return send({ type: k });
    if (k === 'paste' || k === 'emptytrash') return send({ type: k });
    if (x) send({ type: k, path: x.path, paths: pickedPaths() });
  });

  $('newf').onclick = () => { renameNext = true; send({ type: 'mkdir', name: 'New folder' }); };
  $('hid').onclick = () => send({ type: 'hidden' });

  function show(it) {
    mode = 'file'; document.body.classList.remove('folder');
    item = it; zoom = 1;
    $('name').textContent = it.name;
    $('folder').textContent = it.inFolder ? 'Back' : 'Folder';
    $('count').textContent = it.count > 1 ? `${it.index} of ${it.count}` : '';
    $('open').textContent = it.app ? `Open in ${it.app}` : 'Open';
    $('prev').hidden = $('next').hidden = it.count < 2;
    const st = $('stage'); st.scrollTop = 0;
    let meta = [it.type, it.size, it.when].filter(Boolean);
    if (it.kind === 'image') {
      st.innerHTML = `<div class="center"><img class="fit" id="img" src="${esc(it.src)}" alt=""></div>`;
      $('img').onload = () => { const i = $('img'); $('meta').innerHTML = `<span>${esc([it.type, i.naturalWidth + ' × ' + i.naturalHeight, it.size, it.when].join('  ·  '))}</span>`; };
      $('img').onclick = () => { const i = $('img'), f = i.classList.toggle('full'); i.classList.toggle('fit', !f); i.style.width = ''; };
      $('img').onerror = () => other(it, 'This picture cannot be shown here.');
    } else if (it.kind === 'video') {
      st.innerHTML = `<div class="center"><video src="${esc(it.src)}" controls autoplay></video></div>`;
    } else if (it.kind === 'audio') {
      st.innerHTML = `<div class="center"><div class="audio"><div class="big">${esc(it.name.replace(/\.[^.]+$/, ''))}</div><audio src="${esc(it.src)}" controls autoplay></audio></div></div>`;
    } else if (it.kind === 'pdf') {
      const w = Math.min(980, innerWidth - 160);
      const px = Math.round(w * devicePixelRatio);
      st.innerHTML = `<div class="pages">` + Array.from({ length: it.pages }, (_, n) =>
        `<div class="pg" style="width:${w}px;height:${Math.round(w * it.ratio)}px"><img loading="lazy" src="${esc(it.src)}&n=${n + 1}&w=${px}" alt=""></div>`).join('') + '</div>';
      meta = [it.type, it.pages + (it.pages === 1 ? ' page' : ' pages'), it.size, it.when];
    } else if (it.kind === 'text') {
      st.innerHTML = '<pre>' + it.text.split('\n').map((l) => `<span class="l">${esc(l) || ' '}</span>`).join('') + '</pre>' +
        (it.cut ? '<div class="note">Only the first megabyte is shown.</div>' : '');
    } else {
      other(it);
    }
    $('meta').innerHTML = it.kind === 'text' || it.kind === 'other' ? '' : `<span>${esc(meta.join('  ·  '))}</span>`;
  }
  function other(it, why) {
    $('stage').innerHTML = `<div class="center"><div class="other"><div class="mark"><i></i><b></b></div>
      <div class="big">${esc(it.name)}</div><div class="sub">${esc(why || [it.type, it.size, it.when].filter(Boolean).join('  ·  '))}</div>
      <button onclick="send({type:'open'})">${esc(it.app ? 'Open in ' + it.app : 'Open')}</button></div></div>`;
  }
  $('close').onclick = () => send({ type: 'close' });
  $('min').onclick = () => send({ type: 'minimize' });
  $('open').onclick = () => send({ type: 'open' });
  $('folder').onclick = () => send({ type: 'folder' });
  $('prev').onclick = () => send({ type: 'prev' });
  $('next').onclick = () => send({ type: 'next' });
  document.addEventListener('keydown', (e) => {
    if (mode === 'folder') {
      if (renaming) return;
      const cmd = e.ctrlKey || e.metaKey;
      const how = e.shiftKey ? 'range' : null;
      if (e.key === 'ArrowDown' && !cmd) { e.preventDefault(); return choose(sel + 1, how); }
      if (e.key === 'ArrowUp' && !cmd) { e.preventDefault(); return choose(sel - 1, how); }
      if (e.key === 'Home') return choose(0, how);
      if (e.key === 'End') return choose(1e9, how);
      if (e.key === 'PageDown') return choose(sel + 12, how);
      if (e.key === 'PageUp') return choose(sel - 12, how);
      if (cmd && /^a$/i.test(e.key)) { e.preventDefault(); return chooseAll(); }
      if (e.key === 'Enter' || e.key === ' ' || (cmd && e.key === 'ArrowDown')) { e.preventDefault(); return enter(); }
      if (e.key === 'Backspace' || (cmd && e.key === 'ArrowUp') || (e.altKey && e.key === 'ArrowLeft')) { e.preventDefault(); return up(); }
      if (e.key === 'Delete' || (cmd && e.key === 'Backspace')) { if (picked.size && !dir.trash) send({ type: 'trash', paths: pickedPaths() }); return; }
      if (e.key === 'F2') { e.preventDefault(); return startRename(); }
      if (cmd && e.shiftKey && /^n$/i.test(e.key)) { e.preventDefault(); return $('newf').onclick(); }
      if (cmd && /^h$/i.test(e.key)) { e.preventDefault(); return send({ type: 'hidden' }); }
      if (cmd && /^o$/i.test(e.key)) { const x = dir.entries[sel]; if (x) send(x.dir ? { type: 'nav', path: x.path } : { type: 'open', path: x.path }); return; }
      if (cmd && e.key === '`') return send({ type: 'terminal' });
      if (cmd && /^[cxd]$/i.test(e.key) && dir.entries[sel] && !dir.trash) { e.preventDefault(); return send({ type: { c: 'copy', x: 'cut', d: 'duplicate' }[e.key.toLowerCase()], paths: pickedPaths() }); }
      if (cmd && /^v$/i.test(e.key) && !dir.trash) { e.preventDefault(); return send({ type: 'paste' }); }
      if (e.key === 'Escape' && !$('ctx').hidden) return closeMenu();
      if (e.key === 'Escape' && picked.size > 1) return choose(sel);
      if (e.key === 'Escape' || (cmd && /^[wq]$/i.test(e.key))) { e.preventDefault(); return send({ type: 'close' }); }
      if (e.key.length === 1 && !cmd && !e.altKey) {
        find += e.key.toLowerCase(); clearTimeout(findT); findT = setTimeout(() => { find = ''; }, 900);
        const i = dir.entries.findIndex((x) => x.name.toLowerCase().startsWith(find));
        if (i >= 0) choose(i);
      }
      return;
    }
    if (item && item.inFolder && (e.key === 'Escape' || ((e.key === ' ' || e.key === 'Backspace') && !document.querySelector('video, audio')))) {
      e.preventDefault(); return send({ type: 'folder' });
    }
    const media = document.querySelector('video, audio');
    if (e.key === 'Escape' || (e.key === ' ' && !media) || ((e.ctrlKey || e.metaKey) && /^[wq]$/i.test(e.key))) { e.preventDefault(); return send({ type: 'close' }); }
    if (e.key === 'ArrowRight' && !media) return send({ type: 'next' });
    if (e.key === 'ArrowLeft' && !media) return send({ type: 'prev' });
    if (e.key === 'Enter') return send({ type: 'open' });
    const img = $('img');
    if (img && (e.ctrlKey || e.metaKey) && /^[=+\-0]$/.test(e.key)) {
      e.preventDefault();
      zoom = e.key === '0' ? 1 : Math.max(.2, Math.min(8, zoom * (e.key === '-' ? 0.8 : 1.25)));
      img.classList.toggle('fit', zoom === 1); img.classList.toggle('full', zoom !== 1);
      img.style.width = zoom === 1 ? '' : Math.round(img.naturalWidth * zoom / devicePixelRatio) + 'px';
    }
  });
</script>
</body></html>
"""


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    arg = sys.argv[1] if len(sys.argv) > 1 else str(HOME)
    if arg.startswith("file://"):
        arg = urllib.parse.unquote(urllib.parse.urlparse(arg).path)
    Miru(arg)
    Gtk.main()
