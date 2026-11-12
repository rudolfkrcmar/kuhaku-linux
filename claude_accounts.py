"""AI accounts in sets: Personal, Work, any more. Each set can have a Claude, an OpenAI and a
Gemini account. One Claude account is active for the home screen; mail (Tegami) and dev
tools (Tsukuru) can each use any signed-in account.

Each account keeps its own sign-in, history and settings in its own folder (Claude
Code's CLAUDE_CONFIG_DIR, Codex's CODEX_HOME, Gemini CLI's GEMINI_CLI_HOME). The first
account is Claude's usual ~/.claude, so nothing changes until a second one is added.
Switching is instant; no signing out.
"""
import json
import os
import re
import shlex
import time
from pathlib import Path
from kuhaku_log import report  # faults go to the log instead of vanishing

HOME = Path.home()
FILE = HOME / ".config" / "kuhaku" / "claude-accounts.json"
ROOT = HOME / ".claude-accounts"

# The kinds of account: their command and the variable that gives each account its own folder.
KINDS = {
    "claude": {"label": "Claude", "bin": "claude", "var": "CLAUDE_CONFIG_DIR"},
    "codex": {"label": "OpenAI", "bin": "codex", "var": "CODEX_HOME"},
    "gemini": {"label": "Gemini", "bin": "gemini", "var": "GEMINI_CLI_HOME"},
    # A model on this computer (Ollama), driven by Codex in its open-source mode. Not part of the
    # sets: there is one per computer, "signed in" whenever Ollama runs with a model.
    "local": {"label": "Local", "bin": "codex", "var": "CODEX_HOME"},
}
CLOUD = ("claude", "codex", "gemini")
LOCAL_ID = "local"
OLLAMA = (os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
if "://" not in OLLAMA:
    OLLAMA = "http://" + OLLAMA


def kind(acct):
    return acct.get("kind") or "claude"


def exe(k):
    import shutil
    b = KINDS[k]["bin"]
    return shutil.which(b) or str(HOME / ".local/bin" / b)


SETS = ["Personal", "Work"]


def load():
    data = None
    try:
        data = json.loads(FILE.read_text())
    except Exception:
        report()
    if not data or not data.get("accounts"):
        data = {"accounts": [{"id": "private", "name": "Private", "dir": None}], "active": "private"}
    # Every account belongs to a set; one account per kind in a set (older files had neither rule).
    data.setdefault("sets", list(SETS))
    if not any(kind(a) == "local" for a in data["accounts"]):
        data["accounts"].append({"id": LOCAL_ID, "name": "Local", "set": "", "kind": "local",
                                 "dir": str(ROOT / LOCAL_ID)})
    seen, keep = set(), []
    for i, a in enumerate(data["accounts"]):
        if kind(a) == "local":
            if "local" not in seen:
                seen.add("local")
                keep.append(a)
            continue
        if not a.get("set"):
            a["set"] = "Personal" if i == 0 or kind(a) != "claude" else a["name"]
        if (a["set"], kind(a)) in seen:
            continue
        seen.add((a["set"], kind(a)))
        keep.append(a)
        if a["set"] not in data["sets"]:
            data["sets"].append(a["set"])
    data["accounts"] = keep
    return data


def save(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(data, indent=2))


def active():
    """The Claude account the home screen uses."""
    data = load()
    return next((a for a in data["accounts"] if a["id"] == data.get("active") and kind(a) == "claude"),
                data["accounts"][0])


def config_dir(acct=None):
    acct = acct or active()
    return Path(acct["dir"]) if acct.get("dir") else HOME / ".claude"


# What can have its own account: mail (Tegami) and dev tools (Tsukuru). Unset follows home.
PURPOSES = ("mail", "dev")


def pick(ident=None, purpose=None):
    """The account to use: the one named, else the one chosen for this purpose, else the active one.
    An account that is not signed in is never picked for a purpose."""
    data = load()
    by_id = {a["id"]: a for a in data["accounts"]}
    if ident and ident in by_id:
        return by_id[ident]
    chosen = by_id.get((data.get("for") or {}).get(purpose)) if purpose else None
    if chosen and signed_in(chosen):
        return chosen
    return active()


def env(base=None, ident=None, purpose=None):
    """The environment to start Claude in, for the active account (or the one named, or chosen for a purpose)."""
    e = dict(os.environ if base is None else base)
    for k in KINDS.values():
        e.pop(k["var"], None)
    a = pick(ident, purpose)
    if a.get("dir"):
        Path(a["dir"]).mkdir(parents=True, exist_ok=True)     # Codex refuses a CODEX_HOME that is not there
        e[KINDS[kind(a)]["var"]] = a["dir"]
    e["PATH"] = f"{HOME}/.local/bin:" + e.get("PATH", "/usr/bin:/bin")
    return e


def shell_prefix(purpose=None):
    """For commands run through a shell (terminals): 'CLAUDE_CONFIG_DIR=... ' or ''."""
    a = pick(purpose=purpose)
    return f"{KINDS[kind(a)]['var']}={shlex.quote(a['dir'])} " if a.get("dir") else ""


def command(purpose=None):
    """The command line tool of the account used for a purpose: claude, codex or gemini."""
    return exe(kind(pick(purpose=purpose)))


_ollama = {"at": 0, "models": None}


def ollama_models():
    """The models Ollama has on this computer, or None when Ollama is not running. Asked at most every 10 s."""
    if time.time() - _ollama["at"] > 10:
        import urllib.request
        try:
            with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=0.5) as r:
                _ollama["models"] = sorted(m["name"] for m in json.load(r).get("models", []))
        except Exception:
            _ollama["models"] = None
        _ollama["at"] = time.time()
    return _ollama["models"]


def local_model():
    """The local model chosen in Settings, else the first Ollama has."""
    models = ollama_models() or []
    chosen = load().get("local_model")
    return chosen if chosen in models else (models[0] if models else chosen or "")


def set_local_model(name):
    data = load()
    data["local_model"] = str(name or "")
    save(data)


def local_status():
    """For Settings: is Ollama here, running, and with which models."""
    import shutil
    models = ollama_models()
    return {"installed": bool(shutil.which("ollama")) or models is not None, "running": models is not None,
            "models": models or [], "model": local_model()}


def flags(purpose=None, acct=None):
    """Extra command line words for the tool: the local model needs Codex told where to look."""
    a = acct or pick(purpose=purpose)
    if kind(a) != "local":
        return ""
    return " --oss --local-provider ollama" + (f" -m {shlex.quote(local_model())}" if local_model() else "")


def signed_in(acct):
    if kind(acct) == "local":
        return bool(ollama_models())
    d = config_dir(acct)
    return {"claude": d / ".credentials.json", "codex": d / "auth.json",
            "gemini": d / ".gemini" / "oauth_creds.json"}[kind(acct)].exists()


def ready(k=None):
    """The accounts that can be used now: signed in (and of one kind, if named)."""
    return [a for a in load()["accounts"] if signed_in(a) and (k is None or kind(a) == k)]


def listing():
    data = load()
    return [{"id": a["id"], "name": a["name"], "set": a["set"], "kind": kind(a), "label": label(a),
             "active": a["id"] == data.get("active"), "signedIn": signed_in(a)}
            for a in data["accounts"]]


def login_command(acct):
    """The shell line that signs an account in, in its own folder."""
    k = kind(acct)
    prefix = f"{KINDS[k]['var']}={shlex.quote(acct['dir'])} " if acct.get("dir") else ""
    if k == "local":
        return ""                        # nothing to sign in to: Ollama runs on this computer
    if k == "gemini":
        # Gemini CLI signs in on its first question; choose Google sign-in so it does not ask how.
        settings = Path(acct["dir"]) / ".gemini" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        if not settings.exists():
            settings.write_text(json.dumps({"security": {"auth": {"selectedType": "oauth-personal"}}}, indent=2))
        return f"{prefix}{shlex.quote(exe(k))} -p 'Reply only with: signed in'"
    return prefix + shlex.quote(exe(k)) + (" auth login" if k == "claude" else " login")


def ask(prompt, system, purpose=None, cwd=None, timeout=300, schema=None):
    """One answer from an OpenAI or Gemini account, read-only (no edits, no commands that change
    anything). With a schema the answer is parsed JSON. Claude callers keep their own richer call."""
    import subprocess
    import tempfile
    a = pick(purpose=purpose)
    k = kind(a)
    text = system + "\n\n" + prompt
    if schema:
        text += "\n\nAnswer with only JSON matching this schema, nothing else:\n" + json.dumps(schema)
    with tempfile.TemporaryDirectory() as tmp:
        if k in ("codex", "local"):
            out = Path(tmp) / "answer.txt"
            argv = [exe(k), "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
                    "-o", str(out), "-"]
            if k == "local":
                Path(a["dir"]).mkdir(parents=True, exist_ok=True)
                argv[2:2] = ["--oss", "--local-provider", "ollama"] + (["-m", local_model()] if local_model() else [])
            if schema:
                (Path(tmp) / "schema.json").write_text(json.dumps(schema))
                argv[-1:-1] = ["--output-schema", str(Path(tmp) / "schema.json")]
            r = subprocess.run(argv, input=text, capture_output=True, text=True, timeout=timeout,
                               cwd=cwd or str(HOME), env=env(purpose=purpose))
            answer = out.read_text().strip() if out.exists() else ""
        elif k == "gemini":
            r = subprocess.run([exe(k), "--approval-mode", "plan", "-p", text], capture_output=True, text=True,
                               timeout=timeout, cwd=cwd or str(HOME), env=env(purpose=purpose), stdin=subprocess.DEVNULL)
            answer = r.stdout.strip()
        else:
            raise RuntimeError("ask() is for OpenAI, Gemini and local accounts")
    if not answer:
        raise RuntimeError((r.stderr or "no answer").strip()[-200:])
    if schema:
        m = re.search(r"\{.*\}", answer, re.S)
        return json.loads(m.group(0) if m else answer)
    return answer


def uses():
    """Which account mail and dev tools use ('' = the same as home)."""
    f = load().get("for") or {}
    return {p: f.get(p) or "" for p in PURPOSES}


def set_for(purpose, ident):
    if purpose not in PURPOSES:
        return
    data = load()
    f = data.setdefault("for", {})
    if ident and any(a["id"] == ident for a in data["accounts"]):
        f[purpose] = ident
    else:
        f.pop(purpose, None)
    save(data)


def find(set_name, k):
    return next((a for a in load()["accounts"] if a["set"] == set_name and kind(a) == k), None)


def ensure(set_name, k):
    """The account of this kind in this set, made if there is none yet. Returns its id."""
    a = find(set_name, k)
    return a["id"] if a else add(set_name, k)


def add_set(name):
    name = re.sub(r"\s+", " ", name or "").strip()[:30]
    data = load()
    if name and name.lower() not in (x.lower() for x in data["sets"]):
        data["sets"].append(name)
        save(data)


def remove_set(name):
    """Forget a set and its accounts in Kūhaku (their folders stay, so nothing is lost)."""
    data = load()
    if name == data["accounts"][0]["set"]:
        return
    gone = {a["id"] for a in data["accounts"] if a["set"] == name}
    data["accounts"] = [a for a in data["accounts"] if a["id"] not in gone]
    data["sets"] = [x for x in data["sets"] if x != name]
    if data.get("active") in gone:
        data["active"] = data["accounts"][0]["id"]
    data["for"] = {p: v for p, v in (data.get("for") or {}).items() if v not in gone}
    save(data)


def sets_listing():
    """For Settings: each set with its three accounts, and what home, mail and dev tools use."""
    data = load()
    out = []
    for name in data["sets"]:
        slots = {}
        for k in CLOUD:
            a = find(name, k)
            slots[k] = {"id": a["id"], "signedIn": signed_in(a)} if a else None
        out.append({"name": name, "slots": slots, "removable": name != data["accounts"][0]["set"]})
    return out


def label(acct):
    if kind(acct) == "local":
        return "Local · " + (local_model() or "Ollama")
    return f"{acct['set']} {KINDS[kind(acct)]['label']}"


def add(name, k="claude"):
    """A new account of one kind; name is the set it belongs to."""
    k = k if k in CLOUD else "claude"
    name = (name or "").strip()[:40] or "Work"
    known = find(name, k)
    if known:
        return known["id"]   # one account per kind in a set: the one already there
    data = load()
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "account"
    if k != "claude":
        base += "-" + k
    ident, n = base, 2
    while any(a["id"] == ident for a in data["accounts"]):
        ident, n = f"{base}-{n}", n + 1
    d = ROOT / ident
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    # The same notes about this computer follow into the new account.
    notes = HOME / ".claude" / "CLAUDE.md"
    target = {"claude": d / "CLAUDE.md", "codex": d / "AGENTS.md", "gemini": d / ".gemini" / "GEMINI.md"}[k]
    if notes.exists() and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(notes.read_text())
    data["accounts"].append({"id": ident, "name": name, "set": name, "kind": k, "dir": str(d), "added": time.time()})
    if name not in data["sets"]:
        data["sets"].append(name)
    save(data)
    return ident


def use(ident):
    data = load()
    if any(a["id"] == ident and signed_in(a) and kind(a) == "claude" for a in data["accounts"]):
        data["active"] = ident
        save(data)
        return True
    return False


def cycle():
    """The next signed-in account (the home screen's quick switch)."""
    data = load()
    ids = [a["id"] for a in ready("claude")]
    if not ids:
        return active()
    i = ids.index(data.get("active")) if data.get("active") in ids else -1
    use(ids[(i + 1) % len(ids)])
    return active()


def remove(ident):
    """Forget an account in Kūhaku (its folder stays, so nothing is lost)."""
    data = load()
    if ident == data["accounts"][0]["id"]:
        return False
    data["accounts"] = [a for a in data["accounts"] if a["id"] != ident]
    if data.get("active") == ident:
        data["active"] = data["accounts"][0]["id"]
    for p in list((data.get("for") or {})):
        if data["for"][p] == ident:
            del data["for"][p]
    save(data)
    return True
