#!/usr/bin/env python3
"""Tsukuru (作る, "to make"): Kūhaku's place to make things with Claude.

The v3 design from Claude Design: your projects and their Claude sessions on the
left, the conversation with Claude in the middle (Claude Code running headless in
the project, its edits shown as diffs and its commands with their output), Files
and Preview as other views of the same project, Changes on the right to review,
commit and push, and a real terminal underneath when you want one. Sessions are
Claude Code's own, so `claude --resume` in a terminal carries on the same ones.

Connections (GitHub with gh, GitLab with glab, Railway) are written into Claude's
own notes (~/.claude/CLAUDE.md), so Claude knows it can use them for you.

Keys (Cmd is Ctrl here): Cmd+N new session, Cmd+O open or clone a project,
Cmd+E files, Cmd+P find a file, Cmd+S save, Cmd+F find in the file, Cmd+B hide
the sidebar, Cmd+` terminal, Cmd+Shift+G changes, Cmd+Shift+K connections.

    tsukuru [FOLDER]
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte, WebKit2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_accounts as accounts  # noqa: E402
import tsukuru_chat as chat  # noqa: E402

HOME = Path.home()
STATE = HOME / ".local/state/kuhaku/tsukuru.json"
SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", "dist", "build",
        ".next", ".cache", "target", ".idea", ".vscode", ".tox", "browser-data", ".gradle"}
MAX_FILE = 4_000_000

sys.path.insert(0, str(HERE))
from terminal import THEMES, theme, rgba  # noqa: E402  (same colours as Kūhaku Terminal)


CLAUDE_NOTES = HOME / ".claude" / "CLAUDE.md"
NOTE_START, NOTE_END = "<!-- kuhaku:connections -->", "<!-- /kuhaku:connections -->"


def run(argv, cwd=None, timeout=20, strip=True):
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip() if strip else r.stdout.rstrip("\n"), r.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


RAILWAY = shutil.which("railway") or str(HOME / ".local/bin/railway")


def install_railway():
    """Railway's CLI, straight from its GitHub releases, into ~/.local/bin (no root needed)."""
    import tarfile, tempfile, urllib.request
    rel = json.loads(urllib.request.urlopen("https://api.github.com/repos/railwayapp/cli/releases/latest", timeout=20).read())
    url = next(a["browser_download_url"] for a in rel["assets"] if a["name"].endswith("x86_64-unknown-linux-musl.tar.gz"))
    with tempfile.TemporaryDirectory() as d:
        tgz = Path(d) / "railway.tgz"
        urllib.request.urlretrieve(url, tgz)
        with tarfile.open(tgz) as t:
            t.extract("railway", d)
        dest = HOME / ".local/bin/railway"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(d) / "railway", dest)
        dest.chmod(0o755)
    return str(dest)


GLAB = shutil.which("glab") or str(HOME / ".local/bin/glab")


def install_glab():
    """GitLab's CLI, straight from its releases, into ~/.local/bin (no root needed)."""
    import tarfile, tempfile, urllib.request
    req = urllib.request.Request("https://gitlab.com/gitlab-org/cli/-/releases/permalink/latest", method="HEAD")
    tag = urllib.request.urlopen(req, timeout=20).url.rstrip("/").split("/")[-1]
    v = tag.lstrip("v")
    url = f"https://gitlab.com/gitlab-org/cli/-/releases/{tag}/downloads/glab_{v}_linux_amd64.tar.gz"
    with tempfile.TemporaryDirectory() as d:
        tgz = Path(d) / "glab.tgz"
        urllib.request.urlretrieve(url, tgz)
        with tarfile.open(tgz) as t:
            member = next(m for m in t.getmembers() if m.name.endswith("bin/glab"))
            member.name = "glab"
            t.extract(member, d)
        dest = HOME / ".local/bin/glab"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(d) / "glab", dest)
        dest.chmod(0o755)
    return str(dest)


def gitlab_hosts():
    """GitLab servers glab knows: gitlab.com and any company GitLab the user signed in to."""
    try:
        text = (HOME / ".config/glab-cli/config.yml").read_text()
    except OSError:
        return ["gitlab.com"]
    hosts = re.findall(r"^ {4}([^\s#:][^:\s]*):\s*$", text.split("\nhosts:", 1)[-1], re.M)
    return hosts or ["gitlab.com"]


def connections():
    """Who is signed in to GitHub (gh), GitLab (glab) and Railway (railway)."""
    out = {}
    code, raw, _ = run([RAILWAY, "whoami", "--json"], timeout=15) if Path(RAILWAY).exists() else (1, "", "")
    try:
        me = json.loads(raw) if code == 0 else {}
        out["railway"] = me.get("email") or me.get("name") or None
    except Exception:
        out["railway"] = None
    code, user, _ = run(["gh", "api", "user", "--jq", ".login"], timeout=15)
    out["github"] = user if code == 0 and user else None
    out["gitlab"], out["gitlab_hosts"] = None, []
    for host in gitlab_hosts() if Path(GLAB).exists() else []:
        code, raw, _ = run([GLAB, "api", "user", "--hostname", host], timeout=15)
        try:
            if code == 0:
                out["gitlab"] = out["gitlab"] or json.loads(raw).get("username")
                out["gitlab_hosts"].append(host)
        except Exception:
            pass
    return out


def write_claude_notes(conn):
    """Tell Claude Code what is connected, in its own memory file."""
    lines = [NOTE_START, "## Connected services (kept up to date by Kūhaku's Tsukuru)", ""]
    if conn.get("github"):
        lines.append(f"- GitHub: signed in as `{conn['github']}` with the `gh` CLI. Use `gh` for repositories, "
                     "issues, pull requests, Actions and releases (for example `gh pr create`, `gh issue list`).")
    else:
        lines.append("- GitHub: not connected. The user can connect it in Tsukuru's Connections (Cmd+Shift+K).")
    if conn.get("gitlab"):
        lines.append(f"- GitLab: signed in as `{conn['gitlab']}` with the `glab` CLI. Use `glab` for projects, "
                     "issues, merge requests and pipelines (for example `glab mr create`, `glab ci status`).")
    else:
        lines.append("- GitLab: not connected. The user can connect it in Tsukuru's Connections (Cmd+Shift+K).")
    if conn.get("railway"):
        lines.append(f"- Railway: signed in as `{conn['railway']}` with the `railway` CLI (~/.local/bin/railway). Use it to "
                     "deploy and run things on Railway (for example `railway link`, `railway up --detach`, `railway logs`, "
                     "`railway variables`, `railway domain`, `railway status`).")
    else:
        lines.append("- Railway: not connected. The user can connect it in Tsukuru's Connections (Cmd+Shift+K).")
    lines += ["- Projects are usually cloned into ~/Projects.", NOTE_END]
    block = "\n".join(lines)
    try:
        old = CLAUDE_NOTES.read_text() if CLAUDE_NOTES.exists() else ""
    except OSError:
        old = ""
    if NOTE_START in old and NOTE_END in old:
        new = re.sub(re.escape(NOTE_START) + r".*?" + re.escape(NOTE_END), lambda m: block, old, flags=re.S)
    else:
        new = (old.rstrip() + "\n\n" if old.strip() else "") + block + "\n"
    if new != old:
        CLAUDE_NOTES.parent.mkdir(parents=True, exist_ok=True)
        CLAUDE_NOTES.write_text(new)


def git_info(root):
    if not root or run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root)[0] != 0:
        return {"repo": False, "root": str(root or ""), "changes": []}
    _, branch, _ = run(["git", "branch", "--show-current"], cwd=root)
    _, remote, _ = run(["git", "remote", "get-url", "origin"], cwd=root)
    host = "github" if "github.com" in remote else "gitlab" if "gitlab" in remote else ("other" if remote else "")
    _, status, _ = run(["git", "status", "--porcelain=v1", "-uall"], cwd=root, strip=False)
    has_head = run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=root)[0] == 0
    _, numstat, _ = run(["git", "diff", "HEAD" if has_head else "--cached", "--numstat", "-M"], cwd=root)
    counts = {}
    for line in numstat.splitlines():
        a, d, path = (line.split("\t", 2) + ["", ""])[:3]
        path = re.sub(r"\{[^{}]* => ([^{}]*)\}", r"\1", path).split(" => ")[-1].replace("//", "/")
        counts[path] = (int(a) if a.isdigit() else 0, int(d) if d.isdigit() else 0)
    changes = []
    for line in status.splitlines()[:300]:
        code, path = line[:2], line[3:]
        kind = "new" if code == "??" or "A" in code else "deleted" if "D" in code else "renamed" if "R" in code else "changed"
        path = path.split(" -> ")[-1].strip('"')
        add, dele = counts.get(path, (0, 0))
        if code == "??":
            try:
                f = Path(root) / path
                add = f.read_bytes().count(b"\n") if f.stat().st_size < 2_000_000 else 0
            except OSError:
                pass
        changes.append({"path": path, "kind": kind, "mark": {"new": "A", "deleted": "D"}.get(kind, "M"),
                        "add": add, "del": dele})
    ahead = behind = 0
    code, ab, _ = run(["git", "rev-list", "--left-right", "--count", "@{upstream}...HEAD"], cwd=root)
    if code == 0 and ab:
        behind, ahead = (int(x) for x in ab.split()[:2])
    _, last, _ = run(["git", "log", "-1", "--format=%s · %cr"], cwd=root)
    return {"repo": True, "root": str(root), "branch": branch or "(detached)", "remote": remote, "host": host, "changes": changes,
            "ahead": ahead, "behind": behind, "upstream": code == 0, "last": last}


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"recent": []}


def save_state(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st))


class Tsukuru:
    def __init__(self, folder=None):
        self.t = THEMES[theme()]
        self.night = theme() == "night"
        self.state = load_state()
        self.root = None
        self.monitors = {}

        self.win = Gtk.Window(title="Tsukuru")
        self.win.set_decorated(False)
        self.win.set_icon_name("tsukuru")
        self.win.maximize()
        self.win.connect("delete-event", self.on_delete)
        self.win.connect("destroy", Gtk.main_quit)
        self.win.connect("key-press-event", self.on_key)

        ucm = WebKit2.UserContentManager()
        ucm.register_script_message_handler("ide")
        ucm.connect("script-message-received::ide", self.on_message)
        self.view = WebKit2.WebView.new_with_user_content_manager(ucm)
        self.view.set_zoom_level(1.12)  # app type a little larger than the page's own, for reading
        s = self.view.get_settings()
        s.set_allow_file_access_from_file_urls(True)
        s.set_enable_write_console_messages_to_stdout(bool(os.environ.get("TSUKURU_DEBUG")))
        s.set_javascript_can_access_clipboard(True)
        bg = (0.071, 0.071, 0.071) if self.night else (0.969, 0.957, 0.945)
        self.view.set_background_color(Gdk.RGBA(*bg, 1))
        self.view.connect("context-menu", lambda *a: True)
        self.view.connect("decide-policy", self.on_policy)

        # The terminal underneath, in Kūhaku Terminal's colours.
        self.term = Vte.Terminal()
        self.term.set_font(Pango.FontDescription.from_string("JetBrains Mono 10.5"))
        self.term.set_colors(rgba(self.t["fg"]), rgba(self.t["bg"]), [rgba(c) for c in self.t["palette"]])
        self.term.set_color_cursor(rgba(self.t["accent"]))
        self.term.set_color_cursor_foreground(rgba(self.t["bg"]))
        self.term.set_color_highlight(rgba(self.t["select"]))
        self.term.set_color_highlight_foreground(rgba(self.t["fg"]))
        self.term.set_cursor_shape(Vte.CursorShape.IBEAM)
        self.term.set_scrollback_lines(20000)
        self.term.set_cell_height_scale(1.15)
        self.term.connect("child-exited", self.on_shell_exit)
        self.term.connect("key-press-event", self.on_term_key)
        self.term_started = False

        css = Gtk.CssProvider()
        css.load_from_data(f"""
            .termwrap {{ background: {self.t['bg']}; border-top: 1px solid alpha({self.t['fg']}, 0.08); }}
            .claudewrap {{ background: {self.t['bg']}; border-left: 1px solid alpha({self.t['fg']}, 0.08); }}
            paned > separator {{ background: alpha({self.t['fg']}, 0.08); min-height: 1px; }}
        """.encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.termbox = Gtk.Box()
        self.termbox.get_style_context().add_class("termwrap")
        inner = Gtk.Box()
        inner.set_margin_start(28)
        inner.set_margin_end(20)
        inner.set_margin_top(12)
        inner.set_margin_bottom(8)
        inner.pack_start(self.term, True, True, 0)
        self.termbox.pack_start(inner, True, True, 0)

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.paned.pack1(self.view, True, False)
        self.paned.pack2(self.termbox, False, False)

        self.win.add(self.paned)
        # Claude Code runs headless per session; its steps arrive as chat blocks in the page.
        self.runner = chat.Runner(lambda kind, d: GLib.idle_add(self.on_chat, kind, d),
                                  claude=shutil_which("claude") or str(HOME / ".local/bin/claude"))

        page = (HERE / "ide" / "tsukuru.html").as_uri() + f"?theme={'night' if self.night else 'day'}"
        self.pending = folder
        self.view.load_uri(page)
        self.win.show_all()
        self.termbox.hide()
        self.conn = {}
        self.refresh_connections()
        self.win.connect("focus-in-event", lambda *a: self.refresh_git() or False)
        GLib.timeout_add_seconds(8, lambda: self.refresh_git() or True)

    # --- page ----------------------------------------------------------------
    def js(self, fn, payload=None):
        self.view.evaluate_javascript(f"{fn}({json.dumps(payload)})", -1, None, None, None, None, None)

    def on_ready(self):
        acct = chat.account()
        import getpass
        self.js("init", {"home": str(HOME), "user": getpass.getuser(), "account": accounts.label(acct) if len(accounts.ready("claude") or []) > 1 else "Claude",
                         "model": self.state.get("model") or ""})
        self.js("connections", self.conn)
        self.send_projects()
        folder = self.pending or (self.state.get("recent") or [None])[0]
        if folder and Path(folder).is_dir():
            self.open_project(folder)
        if os.environ.get("TSUKURU_OPEN"):       # for testing: open a file
            GLib.timeout_add(900, lambda: self.read(os.environ["TSUKURU_OPEN"]) or False)

    def send_projects(self):
        """The sidebar: projects opened here, newest first. The picker also gets other places to find one."""
        recent = [p for p in self.state.get("recent", []) if Path(p).is_dir()][:10]
        self.js("projects", [{"root": p, "name": Path(p).name} for p in recent])

        def work():
            more, seen = [], set(recent)
            for c in claude_projects(recent):
                if c["path"] not in seen and not c["path"].startswith(("/tmp/", str(HOME / ".config"), str(HOME / ".cache"))):
                    seen.add(c["path"])
                    more.append({"root": c["path"], "name": Path(c["path"]).name})
            for base in (HOME / "Projects", HOME / "projects", HOME / "code", HOME / "src", HOME / "Documents", HOME):
                try:
                    for p in sorted(base.iterdir()):
                        if p.is_dir() and not p.name.startswith(".") and (p / ".git").exists() and str(p) not in seen:
                            seen.add(str(p))
                            more.append({"root": str(p), "name": p.name})
                except OSError:
                    pass
            return more[:40]
        self.in_thread(work, lambda more: self.js("morePlaces", more))

    def open_project(self, folder):
        root = Path(folder).expanduser().resolve()
        if not root.is_dir():
            self.js("toast", f"{folder} is not a folder")
            return
        self.root = root
        recent = [str(root)] + [p for p in self.state.get("recent", []) if p != str(root)]
        self.state["recent"] = recent[:12]
        save_state(self.state)
        self.win.set_title(f"{root.name} — Tsukuru")
        self.send_projects()
        self.js("project", {"root": str(root), "name": root.name, "sessions": chat.sessions(root)})
        self.refresh_git()
        if self.term_started:
            self.term.feed_child(f"cd {GLib.shell_quote(str(root))}\r".encode())

    def on_chat(self, kind, d):
        if kind == "model":
            self.state["model"] = d["model"]
            save_state(self.state)
            return self.js("model", d["model"])
        self.js("chat", dict(d, kind=kind))
        if kind == "busy" and not d["busy"] and self.root and d["root"] == str(self.root):
            self.refresh_git()

    def pages(self):
        root = self.root

        def work():
            found = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if d not in SKIP and not d.startswith("."))
                for f in sorted(filenames):
                    if f.lower().endswith((".html", ".htm")):
                        found.append(os.path.relpath(os.path.join(dirpath, f), root))
                if len(found) > 60:
                    break
            # index pages first, shallow before deep
            found.sort(key=lambda r: (r.count("/"), not os.path.basename(r).startswith("index"), r))
            return found[:40]
        self.in_thread(work, lambda found: self.js("pages", {"root": str(root), "list": found}))

    def diff(self, rel):
        root = self.root

        def work():
            has_head = run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=root)[0] == 0
            _, out, _ = run(["git", "diff", "HEAD" if has_head else "--cached", "--no-color", "-U2", "--", rel], cwd=root)
            rows, n, inside = [], 0, False
            if not out:
                # not tracked yet: the whole file is new
                try:
                    text = (root / rel).read_text("utf-8")
                except (OSError, UnicodeDecodeError):
                    return []
                lines = text.split("\n")
                if lines and lines[-1] == "":
                    lines.pop()
                return [{"s": "+", "t": t, "n": i + 1} for i, t in enumerate(lines[:1500])]
            for line in out.splitlines():
                if line.startswith("@@"):
                    m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
                    n = int(m.group(1)) if m else 1
                    if rows:
                        rows.append({"s": " ", "t": "⋯", "n": ""})
                    inside = True
                elif not inside:
                    continue
                elif line.startswith("+"):
                    rows.append({"s": "+", "t": line[1:], "n": n})
                    n += 1
                elif line.startswith("-"):
                    rows.append({"s": "-", "t": line[1:], "n": ""})
                elif line.startswith(" "):
                    rows.append({"s": " ", "t": line[1:], "n": n})
                    n += 1
                if len(rows) > 3000:
                    break
            return rows
        self.in_thread(work, lambda rows: self.js("diff", {"path": rel, "rows": rows}))

    def list_dir(self, folder):
        out = []
        try:
            items = sorted(Path(folder).iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return out
        for p in items:
            if p.name in SKIP or p.name in (".DS_Store",):
                continue
            if p.name.startswith(".") and p.name not in (".gitignore", ".env.example", ".github", ".claude"):
                continue
            out.append({"name": p.name, "path": str(p), "dir": p.is_dir()})
        return out

    def all_files(self, limit=20000):
        files = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP and not d.startswith(".")]
            for f in filenames:
                if not f.startswith("."):
                    files.append(os.path.relpath(os.path.join(dirpath, f), self.root))
                    if len(files) >= limit:
                        return files
        return sorted(files)

    def read(self, path):
        p = Path(path)
        try:
            if p.stat().st_size > MAX_FILE:
                return self.js("toast", f"{p.name} is too large to edit here")
            data = p.read_bytes()
            if b"\0" in data[:8000]:
                subprocess.Popen([str(HOME / ".local/bin/miru"), str(p)], start_new_session=True)
                return
            text = data.decode("utf-8", "replace")
        except OSError as e:
            return self.js("toast", f"Cannot open {p.name}: {e.strerror}")
        self.watch(p)
        self.js("opened", {"path": str(p), "name": p.name, "text": text, "rel": self.rel(p)})

    def rel(self, p):
        try:
            return str(Path(p).relative_to(self.root))
        except Exception:
            return str(p)

    def watch(self, p):
        if str(p) in self.monitors:
            return
        try:
            mon = Gio.File.new_for_path(str(p)).monitor_file(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error:
            return
        mon.set_rate_limit(300)

        def changed(_m, _f, _o, event):
            if event in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED):
                try:
                    text = p.read_text("utf-8", "replace")
                except OSError:
                    return
                self.js("changedOnDisk", {"path": str(p), "text": text})
            elif event == Gio.FileMonitorEvent.DELETED:
                self.js("deletedOnDisk", {"path": str(p)})
        mon.connect("changed", changed)
        self.monitors[str(p)] = mon

    def write(self, path, text):
        p = Path(path)
        try:
            tmp = p.with_name(f".{p.name}.tsukuru-tmp")
            tmp.write_text(text, "utf-8")
            if p.exists():
                os.chmod(tmp, p.stat().st_mode)
            os.replace(tmp, p)
        except OSError as e:
            return self.js("toast", f"Could not save {p.name}: {e.strerror}")
        self.js("saved", {"path": str(p)})

    # --- GitHub, GitLab and git ----------------------------------------------------
    def in_thread(self, work, then):
        def go():
            result = work()
            GLib.idle_add(lambda: then(result) or False)
        threading.Thread(target=go, daemon=True).start()

    def refresh_connections(self):
        def done(conn):
            self.conn = conn
            write_claude_notes(conn)
            self.js("connections", conn)
        self.in_thread(connections, done)

    def refresh_git(self):
        if self.root:
            root = self.root
            self.in_thread(lambda: git_info(root), lambda info: self.js("gitInfo", info))

    def git(self, argv, ok_text, after=None):
        """Run git (or gh/glab) for the project and report quietly."""
        root = self.root

        def done(res):
            code, out, err = res
            msg = ok_text if code == 0 else (err or out or "It did not work").splitlines()[-1][:160]
            self.js("toast", msg)
            self.refresh_git()
            if after and code == 0:
                after(out)
        self.in_thread(lambda: run(argv, cwd=root, timeout=120), done)

    def list_repos(self):
        def work():
            repos = []
            if self.conn.get("github"):
                code, out, _ = run(["gh", "repo", "list", "--limit", "60", "--json", "nameWithOwner,description,updatedAt"], timeout=30)
                if code == 0:
                    for r in json.loads(out or "[]"):
                        repos.append({"host": "github", "name": r["nameWithOwner"], "about": r.get("description") or "",
                                      "url": f"https://github.com/{r['nameWithOwner']}.git"})
            for host in self.conn.get("gitlab_hosts") or []:
                code, out, _ = run([GLAB, "api", "--hostname", host,
                                    "projects?membership=true&per_page=60&order_by=last_activity_at"], timeout=30)
                if code == 0:
                    for r in json.loads(out or "[]"):
                        repos.append({"host": "gitlab", "name": r["path_with_namespace"], "about": r.get("description") or "",
                                      "url": r["http_url_to_repo"]})
            return repos
        self.in_thread(work, lambda repos: self.js("repos", repos))

    def clone(self, url):
        name = re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1].split(":")[-1]) or "project"
        target = HOME / "Projects" / name
        n = 2
        while target.exists():
            target = HOME / "Projects" / f"{name}-{n}"
            n += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        self.js("toast", f"Cloning {name}…")
        gl = ["gitlab"] + (self.conn.get("gitlab_hosts") or [])
        host = "github" if "github.com" in url else "gitlab" if any(h in url for h in gl) else ""
        argv = (["gh", "repo", "clone", url, str(target)] if host == "github" and self.conn.get("github")
                else [GLAB, "repo", "clone", url, str(target)] if host == "gitlab" and self.conn.get("gitlab")
                else ["git", "clone", url, str(target)])

        def done(res):
            code, out, err = res
            if code == 0 and target.exists():
                self.open_project(str(target))
                self.js("toast", f"Cloned {name}")
            else:
                self.js("toast", (err or out or "Could not clone").splitlines()[-1][:160])
        self.in_thread(lambda: run(argv, timeout=600), done)

    def new_project(self, name):
        """An empty project: a folder in ~/Projects with git, opened with Claude ready."""
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-.") or "project"
        target = HOME / "Projects" / slug
        if target.exists():
            return self.js("toast", f"There is already a project called {slug}")
        target.mkdir(parents=True)
        run(["git", "init", "-q"], cwd=target)
        self.open_project(str(target))
        self.js("toast", f"Started {slug}")

    def on_git_message(self, msg):
        kind = msg.get("type")
        if kind == "connect" and msg["service"] == "railway" and not Path(RAILWAY).exists():
            self.js("toast", "Getting Railway's tool…")

            def done(res):
                if isinstance(res, Exception):
                    return self.js("toast", f"Could not get Railway's tool: {res}"[:140])
                self.on_git_message(msg)
            self.in_thread(lambda: self._try(install_railway), done)
        elif kind == "connect" and msg["service"] == "gitlab" and not Path(GLAB).exists():
            self.js("toast", "Getting GitLab's tool…")

            def done(res):
                if isinstance(res, Exception):
                    return self.js("toast", f"Could not get GitLab's tool: {res}"[:140])
                self.on_git_message(msg)
            self.in_thread(lambda: self._try(install_glab), done)
        elif kind == "connect":
            rw = GLib.shell_quote(RAILWAY)
            # Sign-in pages open in Kūhaku's own sign-in window (signin-bin), which closes itself afterwards.
            shim = GLib.shell_quote(str(HERE / "signin-bin"))
            web = f"export PATH={shim}:\"$PATH\" BROWSER={shim}/xdg-open; "
            cmd = {"github": web + "gh auth login --web --git-protocol https && gh auth setup-git",
                   # HTTPS, so cloning works without SSH keys; glab asks which GitLab (work servers too).
                   "gitlab": web + f"{GLib.shell_quote(GLAB)} auth login --git-protocol https",
                   # Sign in, then give Claude Code Railway's own skills and MCP server.
                   "railway": f"{rw} login && {rw} setup agent -y"}[msg["service"]]
            self.show_terminal(run=cmd)
            self.watch_login()
        elif kind == "disconnect":
            cmd = {"github": "gh auth logout", "gitlab": "glab auth logout",
                   "railway": f"{GLib.shell_quote(RAILWAY)} logout"}[msg["service"]]
            self.show_terminal(run=cmd)
            self.watch_login()
        elif kind == "gitRefresh":
            self.refresh_git()
            self.refresh_connections()
        elif kind == "gitInit":
            self.git(["git", "init"], "This folder is now a git repository")
        elif kind == "commit":
            text = msg.get("message", "").strip()
            if not text:
                return self.js("toast", "Write a few words about the change first")
            root = self.root

            def work():
                c, o, e = run(["git", "add", "-A"], cwd=root)
                return run(["git", "commit", "-m", text], cwd=root) if c == 0 else (c, o, e)

            def done(res):
                ok = res[0] == 0
                self.js("toast", "Committed" if ok else ((res[2] or res[1]).splitlines() or ["Could not commit"])[-1][:160])
                if ok:
                    self.js("committed", True)
                    if msg.get("push"):
                        return self.on_git_message({"type": "push"})
                self.refresh_git()
            self.in_thread(work, done)
        elif kind == "push":
            info = git_info(self.root)
            argv = ["git", "push"] if info.get("upstream") else ["git", "push", "-u", "origin", info.get("branch", "HEAD")]
            where = re.sub(r"^(https?://|git@)", "", info.get("remote") or "").replace(":", "/").removesuffix(".git")
            self.git(argv, f"Pushed to {where}" if where else "Pushed")
        elif kind == "pull":
            self.git(["git", "pull", "--ff-only"], "Up to date")
        elif kind == "request":
            info = git_info(self.root)
            cmd = "gh pr create --fill" if info.get("host") == "github" else "glab mr create --fill --yes"
            self.show_terminal(run=cmd)
        elif kind == "railwayStatus":
            root = self.root

            def work():
                code, out, err = run([RAILWAY, "status", "--json"], cwd=root, timeout=20)
                try:
                    d = json.loads(out) if code == 0 else {}
                except Exception:
                    d = {}
                env = (d.get("environments") or {}).get("edges") or []
                services = [e["node"]["name"] for e in (d.get("services") or {}).get("edges", []) if e.get("node")]
                return {"linked": bool(d), "project": d.get("name"), "services": services,
                        "environment": env[0]["node"]["name"] if env else None}
            self.in_thread(work, lambda r: self.js("railway", r))
        elif kind in ("rwLink", "rwUp", "rwLogs", "rwOpen", "rwVars", "rwInit", "rwDomain"):
            rw = GLib.shell_quote(RAILWAY)
            self.show_terminal(run={"rwLink": f"{rw} link", "rwUp": f"{rw} up --detach", "rwLogs": f"{rw} logs",
                                    "rwOpen": f"{rw} open", "rwVars": f"{rw} variables", "rwInit": f"{rw} init",
                                    "rwDomain": f"{rw} domain"}[kind])
        elif kind == "repos":
            self.list_repos()
        elif kind == "clone":
            self.clone(msg["url"])
        elif kind == "newProject":
            self.new_project(msg.get("name", ""))
        elif kind == "webRepo":
            self.show_terminal(run="gh repo view --web" if git_info(self.root).get("host") == "github" else "glab repo view --web")
        else:
            return False
        return True

    @staticmethod
    def _try(fn):
        try:
            return fn()
        except Exception as e:
            return e

    def watch_login(self):
        """While a sign-in runs in the terminal, check every few seconds for a minute."""
        left = [20]

        def tick():
            self.refresh_connections()
            left[0] -= 1
            return left[0] > 0
        GLib.timeout_add_seconds(4, tick)

    # --- terminal and Claude ---------------------------------------------------
    def show_terminal(self, run=None):
        if not self.termbox.get_visible():
            self.termbox.show()
            # A third of the window for the terminal, once the window has its size.
            GLib.idle_add(lambda: self.paned.set_position(max(200, int(self.paned.get_allocated_height() * 0.64))) and False)
        if not self.term_started:
            shell = os.environ.get("SHELL", "/bin/bash")
            env = [f"{k}={v}" for k, v in os.environ.items() if k not in ("COLUMNS", "LINES")]
            env += ["TERM=xterm-256color", "COLORTERM=truecolor"]
            argv = [shell, "-l"] if not run else [shell, "-lc", f"{run}; exec {shell} -l"]
            self.term.spawn_async(Vte.PtyFlags.DEFAULT, str(self.root or HOME), argv, env,
                                  GLib.SpawnFlags.SEARCH_PATH, None, None, -1, None, None)
            self.term_started = True
        elif run:
            self.term.feed_child(f"{run}\r".encode())
        self.term.grab_focus()
        self.js("terminalShown", True)

    def hide_terminal(self):
        self.termbox.hide()
        self.view.grab_focus()
        self.js("terminalShown", False)

    def on_shell_exit(self, *_):
        self.term_started = False
        self.hide_terminal()

    def on_term_key(self, _w, ev):
        cmd = ev.state & Gdk.ModifierType.CONTROL_MASK   # the Mac Cmd key here
        ctrl = ev.state & Gdk.ModifierType.SUPER_MASK     # the physical Ctrl key here
        k = Gdk.keyval_name(Gdk.keyval_to_lower(ev.keyval)) or ""
        if cmd and not ctrl:
            if k == "c" and self.term.get_has_selection():
                self.term.copy_clipboard_format(Vte.Format.TEXT)
                return True
            if k == "v":
                self.term.paste_clipboard()
                return True
            if k == "c":
                return False
        if ctrl and len(k) == 1 and k.isalpha():
            self.term.feed_child(bytes([ord(k.upper()) & 0x1F]))
            return True
        return False

    # --- messages ----------------------------------------------------------------
    def on_message(self, _m, result):
        try:
            msg = json.loads(result.get_js_value().to_string())
        except Exception:
            return
        kind = msg.get("type")
        if os.environ.get("TSUKURU_DEBUG"):
            print("tsukuru: message", kind, flush=True)
        if self.on_git_message(msg):
            return
        if kind == "ready":
            self.on_ready()
        elif kind == "openProject":
            self.open_project(msg["path"])
        elif kind == "ask" and self.root:
            sid, new = msg.get("sid"), bool(msg.get("isNew"))
            if new:
                old, sid = sid, str(__import__("uuid").uuid4())
                self.js("started", {"old": old, "sid": sid})
            self.runner.send(self.root, sid, msg["text"], msg.get("mode") or "auto", new=new)
        elif kind == "stop":
            self.runner.stop(msg.get("sid"))
        elif kind == "history" and self.root:
            root, sid = self.root, msg["sid"]
            self.in_thread(lambda: chat.history(root, sid), lambda b: self.js("history_", {"root": str(root), "sid": sid, "blocks": b}))
        elif kind == "sessions" and self.root:
            root = self.root
            self.in_thread(lambda: chat.sessions(root), lambda lst: self.js("sessions", {"root": str(root), "list": lst}))
        elif kind == "pages" and self.root:
            self.pages()
        elif kind == "diff" and self.root:
            self.diff(msg["path"])
        elif kind == "commitMsg" and self.root:
            root = self.root

            def work():
                has_head = run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=root)[0] == 0
                run(["git", "add", "-A", "-N"], cwd=root)        # untracked files show in the diff too
                _, d, _ = run(["git", "diff", "HEAD" if has_head else "--cached", "--stat", "-p", "--no-color"], cwd=root, timeout=30)
                try:
                    return chat.commit_message(root, d)
                except Exception:
                    return ""

            def done(text):
                self.js("commitMsg", text)
                if not text:
                    self.js("toast", "Claude could not write it this time")
            self.in_thread(work, done)
        elif kind == "openUrl":
            subprocess.Popen([str(HOME / ".local/bin/mado"), msg["url"]], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif kind == "list":
            self.js("listed", {"path": msg["path"], "items": self.list_dir(msg["path"])})
        elif kind == "read":
            p = msg["path"]
            self.read(p if os.path.isabs(p) else str(self.root / p))
        elif kind == "write":
            self.write(msg["path"], msg["text"])
        elif kind == "openTabs" and self.root:
            self.state.setdefault("open", {})[str(self.root)] = msg.get("paths", [])[:20]
            save_state(self.state)
        elif kind == "newFile" or kind == "newFolder":
            base = Path(msg.get("dir") or self.root)
            target = base / msg["name"].strip()
            try:
                if kind == "newFolder":
                    target.mkdir(parents=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.touch(exist_ok=False)
            except OSError as e:
                return self.js("toast", f"Could not create {target.name}: {e.strerror}")
            self.js("listed", {"path": str(base), "items": self.list_dir(base)})
            if kind == "newFile":
                self.read(str(target))
        elif kind == "trash":
            try:
                Gio.File.new_for_path(msg["path"]).trash(None)
                self.js("toast", f"Moved {Path(msg['path']).name} to the Trash")
                parent = str(Path(msg["path"]).parent)
                self.js("listed", {"path": parent, "items": self.list_dir(parent)})
            except GLib.Error as e:
                self.js("toast", f"Could not move it to the Trash: {e.message}")
        elif kind == "terminal":
            self.hide_terminal() if self.termbox.get_visible() and not msg.get("show") else self.show_terminal()
        elif kind == "files" and self.root:
            self.js("allFiles_", self.all_files())
        elif kind == "reveal":
            subprocess.Popen([str(HOME / ".local/bin/miru"), msg["path"]], start_new_session=True)
        elif kind == "minimize":
            self.win.iconify()
        elif kind == "quit":
            self.win.close()
        elif kind == "quitNow":
            self.win.destroy()

    def on_policy(self, _v, decision, kind):
        if kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri.startswith(("http://", "https://")) and not re.match(r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])[:/]", uri):
                subprocess.Popen([str(HOME / ".local/bin/mado"), uri], start_new_session=True)
                decision.ignore()
                return True
        return False

    def on_delete(self, *_):
        # Ask the page: it says whether something is unsaved.
        self.js("wantsToQuit")
        return True

    def on_key(self, _w, ev):
        cmd = ev.state & Gdk.ModifierType.CONTROL_MASK
        shift = ev.state & Gdk.ModifierType.SHIFT_MASK
        k = Gdk.keyval_name(Gdk.keyval_to_lower(ev.keyval)) or ""
        if cmd and k == "grave":
            if self.termbox.get_visible() and self.term.has_focus():
                self.hide_terminal()
            else:
                self.show_terminal()
            return True
        if cmd and shift and k == "l":
            self.view.grab_focus()
            self.js("focusPrompt")
            return True
        if cmd and k == "q":
            self.js("wantsToQuit")
            return True
        if self.term.has_focus() and cmd and k in ("1", "e", "n", "o"):
            self.view.grab_focus()
            if k in ("n", "o"):
                return False
            return True
        return False


def claude_projects(skip=()):
    """Every folder Claude Code has worked in (any Claude account), newest first: Tsukuru takes them over
    as they are, with their conversations, the way Cursor imports projects."""
    seen = {}
    roots = [HOME / ".claude" / "projects"] + list((HOME / ".claude-accounts").glob("*/projects"))
    for base in roots:
        for d in base.glob("*") if base.is_dir() else []:
            logs = sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
            if not logs:
                continue
            cwd = None
            try:
                with open(logs[0], errors="ignore") as fh:
                    for line in fh:
                        m = re.search(r'"cwd":"([^"]+)"', line)
                        if m:
                            cwd = m.group(1)
                            break
            except OSError:
                continue
            if cwd and Path(cwd).is_dir() and cwd != str(HOME) and cwd not in skip:
                seen[cwd] = max(seen.get(cwd, 0), logs[0].stat().st_mtime)
    return [{"path": p, "when": t} for p, t in sorted(seen.items(), key=lambda x: -x[1])][:16]


def shutil_which(name):
    import shutil
    return shutil.which(name)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg and arg.startswith("file://"):
        from urllib.parse import unquote, urlparse
        arg = unquote(urlparse(arg).path)
    if arg and Path(arg).is_file():
        arg = str(Path(arg).parent)
    Tsukuru(arg)
    Gtk.main()
