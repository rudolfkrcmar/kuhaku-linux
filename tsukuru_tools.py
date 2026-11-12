"""Tsukuru's ties to the world outside the editor: git, the GitHub, GitLab and Railway sign-ins,
the notes Claude reads about them, and what Tsukuru remembers between runs.

Plain functions with no window code."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from kuhaku_log import report  # faults go to the log instead of vanishing

HOME = Path.home()
STATE = HOME / ".local/state/kuhaku/tsukuru.json"
CLAUDE_NOTES = HOME / ".claude" / "CLAUDE.md"

NOTE_START, NOTE_END = "<!-- kuhaku:connections -->", "<!-- /kuhaku:connections -->"


def run(argv, cwd=None, timeout=20, strip=True):
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip() if strip else r.stdout.rstrip("\n"), r.stderr.strip()
    except Exception as e:
        report()
        return 1, "", str(e)


RAILWAY = shutil.which("railway") or str(HOME / ".local/bin/railway")


def install_railway():
    """Railway's CLI, straight from its GitHub releases, into ~/.local/bin (no root needed)."""
    import tarfile
    import tempfile
    import urllib.request

    rel = json.loads(
        urllib.request.urlopen("https://api.github.com/repos/railwayapp/cli/releases/latest", timeout=20).read()
    )
    url = next(
        a["browser_download_url"] for a in rel["assets"] if a["name"].endswith("x86_64-unknown-linux-musl.tar.gz")
    )
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
    import tarfile
    import tempfile
    import urllib.request

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
    out: dict = {}
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
            report()
    return out


def write_claude_notes(conn):
    """Tell Claude Code what is connected, in its own memory file."""
    lines = [NOTE_START, "## Connected services (kept up to date by Kūhaku's Tsukuru)", ""]
    if conn.get("github"):
        lines.append(
            f"- GitHub: signed in as `{conn['github']}` with the `gh` CLI. Use `gh` for repositories, "
            "issues, pull requests, Actions and releases (for example `gh pr create`, `gh issue list`)."
        )
    else:
        lines.append("- GitHub: not connected. The user can connect it in Tsukuru's Connections (Cmd+Shift+K).")
    if conn.get("gitlab"):
        lines.append(
            f"- GitLab: signed in as `{conn['gitlab']}` with the `glab` CLI. Use `glab` for projects, "
            "issues, merge requests and pipelines (for example `glab mr create`, `glab ci status`)."
        )
    else:
        lines.append("- GitLab: not connected. The user can connect it in Tsukuru's Connections (Cmd+Shift+K).")
    if conn.get("railway"):
        lines.append(
            f"- Railway: signed in as `{conn['railway']}` with the `railway` CLI (~/.local/bin/railway). Use it to "
            "deploy and run things on Railway (for example `railway link`, `railway up --detach`, `railway logs`, "
            "`railway variables`, `railway domain`, `railway status`)."
        )
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
        kind = (
            "new"
            if code == "??" or "A" in code
            else "deleted"
            if "D" in code
            else "renamed"
            if "R" in code
            else "changed"
        )
        path = path.split(" -> ")[-1].strip('"')
        add, dele = counts.get(path, (0, 0))
        if code == "??":
            try:
                f = Path(root) / path
                add = f.read_bytes().count(b"\n") if f.stat().st_size < 2_000_000 else 0
            except OSError:
                pass
        changes.append(
            {"path": path, "kind": kind, "mark": {"new": "A", "deleted": "D"}.get(kind, "M"), "add": add, "del": dele}
        )
    ahead = behind = 0
    code, ab, _ = run(["git", "rev-list", "--left-right", "--count", "@{upstream}...HEAD"], cwd=root)
    if code == 0 and ab:
        behind, ahead = (int(x) for x in ab.split()[:2])
    _, last, _ = run(["git", "log", "-1", "--format=%s · %cr"], cwd=root)
    return {
        "repo": True,
        "root": str(root),
        "branch": branch or "(detached)",
        "remote": remote,
        "host": host,
        "changes": changes,
        "ahead": ahead,
        "behind": behind,
        "upstream": code == 0,
        "last": last,
    }


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        report()
        return {"recent": []}


def save_state(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st))
