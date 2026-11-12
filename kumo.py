#!/usr/bin/env python3
"""Kumo (雲, cloud): Dropbox and Google Drive inside Miru, only the folders you choose.

Several accounts of each are fine (work and personal). The first Dropbox is "dropbox"
(rclone remote kuhaku-dropbox, folder ~/Dropbox), the next "dropbox-2" (kuhaku-dropbox-2,
~/Dropbox (Work)), and so on; Google Drive the same with "drive".

rclone does the work. `rclone authorize` signs you in (in Mado); `rclone bisync` keeps
each chosen folder and its copy under ~/Dropbox or ~/Google Drive the same, both ways.
Nothing else is downloaded: folders you did not choose stay in the cloud.

    kumo daemon     keep the chosen folders in sync (kuhaku-session starts it)
    kumo now        sync right away
    kumo status     what each cloud is doing
"""
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from kuhaku_log import report  # faults go to the log instead of vanishing

HOME = Path.home()
CONF_DIR = HOME / ".config/kuhaku"
STATE_DIR = HOME / ".local/state/kuhaku"
RCLONE_CONF = CONF_DIR / "rclone.conf"
CHOICES = CONF_DIR / "cloud.json"           # which folders, per cloud
STATUS = STATE_DIR / "cloud.json"           # what the daemon is doing
TRIGGER = STATE_DIR / "cloud.now"           # touched to ask for a sync now
LOCK = STATE_DIR / "cloud.lock"
LOG = STATE_DIR / "cloud.log"
EVERY = 300                                 # seconds between rounds
MIN_RCLONE = (1, 66)                        # bisync's --recover, --resilient, --conflict-resolve

# The kinds of cloud. An account is one kind plus a number (see account()).
KINDS = {
    "dropbox": {"name": "Dropbox", "type": "dropbox", "local": HOME / "Dropbox", "flags": []},
    # Google Docs, Sheets and Slides have no file to copy; they stay online.
    "drive": {"name": "Google Drive", "type": "drive", "local": HOME / "Google Drive",
              "flags": ["--drive-skip-gdocs"], "conf": ["scope", "drive"]},
}
KEY = re.compile(r"^(dropbox|drive)(?:-([2-9]|[1-9]\d))?$")


def account(key):
    """Everything about one account, connected or not; None for a key that cannot be one."""
    m = KEY.match(key or "")
    if not m:
        return None
    kind = KINDS[m.group(1)]
    first = m.group(2) is None
    mine = _load(CHOICES).get(key, {})
    label = mine.get("label") or ""
    local = Path(mine["local"]) if mine.get("local") else kind["local"] if first else \
        HOME / f"{kind['name']} ({label or 'account ' + m.group(2)})"
    many = first and any(k != key and k.startswith(m.group(1) + "-") for k in _remotes())
    name = kind["name"] + (f" ({label})" if label and (not first or many) else "" if first else f" {m.group(2)}")
    return {"key": key, "kind": m.group(1), "first": first, "name": name, "label": label,
            "email": mine.get("email", ""), "type": kind["type"], "remote": "kuhaku-" + key,
            "local": local, "flags": kind["flags"], "conf": kind.get("conf", [])}


def _remotes():
    """Account keys with a saved sign-in, in the order they were added."""
    try:
        names = re.findall(r"^\[kuhaku-([a-z0-9-]+)\]\s*$", RCLONE_CONF.read_text(), re.M)
    except OSError:
        return []
    return [n for n in names if KEY.match(n)]


def accounts():
    """The first Dropbox and Google Drive always (to connect), then every further account signed in."""
    keys = ["dropbox", "drive"]
    for k in sorted(_remotes(), key=lambda k: (k.split("-")[0] != "dropbox", int(k.split("-")[1]) if "-" in k else 1)):
        if k not in keys:
            keys.insert(max(i for i, x in enumerate(keys) if x.split("-")[0] == k.split("-")[0]) + 1, k)
    return {k: account(k) for k in keys}


def new_key(kind):
    """The key for one more account of this kind."""
    taken = set(_remotes()) | set(_load(CHOICES))
    n = 2
    while f"{kind}-{n}" in taken:
        n += 1
    return f"{kind}-{n}"


# --- rclone itself -------------------------------------------------------------
def rclone_path():
    for p in (shutil.which("rclone"), str(HOME / ".local/bin/rclone")):
        if p and os.access(p, os.X_OK) and rclone_version(p) >= MIN_RCLONE:
            return p
    return None


def rclone_version(path):
    try:
        out = subprocess.run([path, "version"], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"v(\d+)\.(\d+)", out)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    except Exception:
        report()
        return (0, 0)


def install_rclone():
    """The official build into ~/.local/bin, checked against its published SHA-256 (no password needed).
    Debian's own rclone is too old for safe two-way sync."""
    base = "https://downloads.rclone.org"
    arch = {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "arm-v7"}.get(os.uname().machine, "amd64")
    get = lambda url: urllib.request.urlopen(url, timeout=60).read()
    ver = get(f"{base}/version.txt").decode().split()[-1]
    name = f"rclone-{ver}-linux-{arch}.zip"
    sums = get(f"{base}/{ver}/SHA256SUMS").decode()
    want = next((ln.split()[0] for ln in sums.splitlines() if ln.strip().endswith(name)), None)
    data = get(f"{base}/{ver}/{name}")
    if not want or hashlib.sha256(data).hexdigest() != want:
        raise RuntimeError("the rclone download did not match its checksum")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        member = next(n for n in z.namelist() if n.endswith("/rclone"))
        dest = HOME / ".local/bin/rclone"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".new")
        tmp.write_bytes(z.read(member))
        tmp.chmod(0o755)
        tmp.replace(dest)
    return str(dest)


def rclone(*args, timeout=None):
    exe = rclone_path()
    if not exe:
        raise RuntimeError("rclone is not installed")
    return subprocess.run([exe, "--config", str(RCLONE_CONF), *args], capture_output=True, text=True, timeout=timeout)


# --- what you chose ----------------------------------------------------------------
def _load(p):
    try:
        return json.loads(p.read_text())
    except Exception:
        report()
        return {}


def _save(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def connected(key):
    try:
        return f"[kuhaku-{key}]" in RCLONE_CONF.read_text()
    except OSError:
        return False


def folders(key):
    return list(_load(CHOICES).get(key, {}).get("folders", []))


def tidy(paths):
    """Drop a folder when its parent is chosen too: the parent brings it along."""
    paths = sorted({p.strip("/") for p in paths if p.strip("/")})
    return [p for p in paths if not any(p.startswith(q + "/") for q in paths if q != p)]


def choose(key, paths):
    data = _load(CHOICES)
    data.setdefault(key, {})["folders"] = tidy(paths)
    _save(CHOICES, data)
    st = _load(STATUS)
    ready = st.get(key, {}).get("ready", [])
    st.setdefault(key, {})["ready"] = [f for f in ready if f in data[key]["folders"]]
    _save(STATUS, st)
    nudge()


def disconnect(key):
    """Forget the account and the choice. The copies already on this computer stay."""
    rclone("config", "delete", "kuhaku-" + key, timeout=30)
    data = _load(CHOICES)
    data.pop(key, None)
    _save(CHOICES, data)
    st = _load(STATUS)
    st.pop(key, None)
    _save(STATUS, st)


def local_base(key):
    return account(key)["local"]


def which_cloud(path):
    """The cloud a local path belongs to, if any: (key, folder relative to its base)."""
    path = Path(path)
    for key, p in accounts().items():
        try:
            rel = path.relative_to(p["local"])
        except ValueError:
            continue
        return key, str(rel) if str(rel) != "." else ""
    return None, None


def nudge(path=None):
    """Ask the daemon to sync soon (after a change in a cloud folder, or a new choice)."""
    if path is not None and which_cloud(path)[0] is None:
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    TRIGGER.touch()


def status(key):
    return _load(STATUS).get(key, {})


def describe(key):
    """One short line for Miru's side bar."""
    if not connected(key):
        return "Connect"
    chosen = folders(key)
    if not chosen:
        return "Choose folders"
    st = status(key)
    n = f"{len(chosen)} folder{'s' if len(chosen) != 1 else ''}"
    if st.get("state") == "syncing":
        return f"{n} · syncing…"
    if st.get("state") == "error":
        return f"{n} · not synced, will retry"
    last = st.get("last")
    if not last:
        return f"{n} · waiting to sync"
    mins = int((time.time() - last) // 60)
    when = "just now" if mins < 1 else f"{mins} min ago" if mins < 60 else f"{mins // 60} h ago" if mins < 1440 else "a while ago"
    return f"{n} · synced {when}"


# --- signing in ------------------------------------------------------------------------
def whoami(kind, token):
    """The signed-in person's email and name, asked of the cloud with the new token ({} if it will not say)."""
    try:
        access = json.loads(token)["access_token"]
        if kind == "dropbox":
            req = urllib.request.Request("https://api.dropboxapi.com/2/users/get_current_account", data=b"null",
                                         headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"})
            d = json.loads(urllib.request.urlopen(req, timeout=20).read())
            return {"email": d.get("email", ""), "name": (d.get("name") or {}).get("display_name", "")}
        req = urllib.request.Request("https://www.googleapis.com/drive/v3/about?fields=user",
                                     headers={"Authorization": f"Bearer {access}"})
        u = json.loads(urllib.request.urlopen(req, timeout=20).read()).get("user", {})
        return {"email": u.get("emailAddress", ""), "name": u.get("displayName", "")}
    except Exception:
        report()
        return {}


def _known_email(key):
    """The email of an account signed in before Kumo noted emails (asked once, then kept)."""
    a = account(key)
    if a["email"] or not connected(key):
        return a["email"]
    try:
        rclone("lsjson", "--max-depth", "1", "--dirs-only", f"{a['remote']}:", timeout=60)   # refreshes the token
        conf = json.loads(rclone("config", "dump", timeout=30).stdout or "{}")
        email = whoami(a["kind"], conf.get(a["remote"], {}).get("token", "")).get("email", "")
    except Exception:
        email = ""
    if email:
        data = _load(CHOICES)
        data.setdefault(key, {})["email"] = email
        _save(CHOICES, data)
    return email


def _clean_label(text):
    text = re.sub(r"[/\\\x00-\x1f]+", " ", text or "").strip()[:40]
    return text.strip(". ") or ""


def connect(key, open_url, done, label=""):
    """Signs in through the browser; calls done(None) once the account is saved, or done(error).
    A further account (dropbox-2…) is refused if it turns out to be one already here."""
    p = account(key)

    def work():
        try:
            exe = rclone_path() or install_rclone()
            proc = subprocess.Popen([exe, "--config", str(RCLONE_CONF), "authorize", p["type"], "--auth-no-open-browser"],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            out, opened = [], False
            deadline = time.time() + 600
            for line in proc.stdout:
                out.append(line)
                m = re.search(r"(http://127\.0\.0\.1:\d+/auth\?state=\S+)", line)
                if m and not opened:
                    opened = True
                    open_url(m.group(1))
                if time.time() > deadline:
                    proc.kill()
                    break
            proc.wait()
            text = "".join(out)
            m = re.search(r"--->\s*(\{.*?\})\s*<---", text, re.S)
            if not m:
                last = [ln.strip() for ln in out if ln.strip()]
                return done("Signing in did not finish" + (f": {last[-1][:120]}" if last else ""))
            token = m.group(1).strip()
            who = whoami(p["kind"], token)
            email = who.get("email", "")
            if email:
                for other, a in accounts().items():
                    if other != key and a["kind"] == p["kind"] and connected(other) and \
                            _known_email(other).lower() == email.lower():
                        return done(f"That is {email}, already here as {a['name']}. "
                                    f"Sign in with the other account (sign out of {email} on the page first).")
            r = rclone("config", "create", p["remote"], p["type"], *p.get("conf", []), "token", token,
                       "--non-interactive", timeout=60)
            if r.returncode != 0 or not connected(key):
                return done("Could not save the account: " + (r.stderr.strip().splitlines() or ["unknown error"])[-1][:120])
            RCLONE_CONF.chmod(0o600)
            data = _load(CHOICES)
            mine = data.setdefault(key, {})
            mine["email"] = email
            mine["label"] = _clean_label(label) or email or who.get("name", "") or mine.get("label", "")
            if not p["first"] and not mine.get("local"):
                kind = KINDS[p["kind"]]["name"]
                base = HOME / f"{kind} ({_clean_label(mine['label']) or key.split('-')[1]})"
                n = 2
                while base.exists() and any(a["local"] == base for k, a in accounts().items() if k != key):
                    base = HOME / f"{kind} ({_clean_label(mine['label'])} {n})"
                    n += 1
                mine["local"] = str(base)
            _save(CHOICES, data)
            done(None)
        except Exception as e:
            done(f"Could not sign in: {e}")
    threading.Thread(target=work, daemon=True).start()


def list_folders(key, path=""):
    """Folder names directly inside path in the cloud."""
    a = account(key)
    r = rclone("lsjson", "--dirs-only", *a["flags"], f"{a['remote']}:{path}", timeout=90)
    if r.returncode != 0:
        raise RuntimeError((r.stderr.strip().splitlines() or ["could not list the folders"])[-1])
    return sorted((d["Name"] for d in json.loads(r.stdout or "[]")), key=str.lower)


# --- keeping them the same -------------------------------------------------------------
def _log(text):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if LOG.exists() and LOG.stat().st_size > 2_000_000:
            LOG.replace(LOG.with_suffix(".old"))
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text.rstrip() + "\n")
    except OSError:
        pass


def _set(key, **kv):
    st = _load(STATUS)
    st.setdefault(key, {}).update(kv)
    _save(STATUS, st)


def sync_folder(key, folder, first):
    p = account(key)
    local = p["local"] / folder
    local.mkdir(parents=True, exist_ok=True)
    cmd = ["bisync", f"{p['remote']}:{folder}", str(local), *p["flags"],
           "--create-empty-src-dirs", "--compare", "size,modtime", "--resilient", "--recover",
           "--max-lock", "2m", "--conflict-resolve", "newer", "--conflict-loser", "num", "-v"]
    # The first run (or a recovery) only copies, never deletes: whatever is newer wins.
    r = rclone(*cmd, *(["--resync", "--resync-mode", "newer"] if first else []))
    _log(f"[{key}] {folder}: exit {r.returncode}\n{r.stderr[-4000:]}")
    if r.returncode != 0 and not first and re.search(r"resync", r.stderr, re.I):
        r = rclone(*cmd, "--resync", "--resync-mode", "newer")
        _log(f"[{key}] {folder} (resync): exit {r.returncode}\n{r.stderr[-4000:]}")
    if r.returncode != 0:
        errs = [ln for ln in r.stderr.splitlines() if "ERROR" in ln or "NOTICE" in ln]
        raise RuntimeError((errs or r.stderr.strip().splitlines() or ["sync failed"])[-1][-160:])


def sync_all():
    # Every account on its own: one that fails (signed out, offline, a conflict) never stops the others.
    for key in accounts():
        chosen = folders(key)
        if not chosen or not connected(key):
            continue
        _set(key, state="syncing")
        ready, error = set(status(key).get("ready", [])), None
        for folder in chosen:
            try:
                sync_folder(key, folder, folder not in ready)
                ready.add(folder)
            except Exception as e:
                error = f"{folder}: {e}"
                _log(f"[{key}] {folder}: {e}")
        _set(key, ready=sorted(ready & set(folders(key))), **(
            {"state": "error", "error": error} if error else {"state": "idle", "error": None, "last": time.time()}))


def daemon():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # already running
    st = _load(STATUS)
    for key in st:
        if st[key].get("state") == "syncing":
            st[key]["state"] = "idle"
    _save(STATUS, st)
    last = 0
    while True:
        due = time.time() - last >= EVERY
        if TRIGGER.exists():
            time.sleep(4)           # let a burst of changes settle
            TRIGGER.unlink(missing_ok=True)
            due = True
        if due and rclone_path():
            sync_all()
            last = time.time()
        time.sleep(3)


def ensure_daemon():
    """Start the daemon if it is not running (its lock keeps it to one)."""
    try:
        with open(LOCK, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
    except OSError:
        return
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "daemon"], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "daemon":
        daemon()
    elif cmd == "now":
        nudge()
        ensure_daemon()
    else:
        for k, p in accounts().items():
            print(f"{p['name']} [{k}] {str(p['local']).replace(str(HOME), '~', 1)}: {describe(k)}  {folders(k)}")
