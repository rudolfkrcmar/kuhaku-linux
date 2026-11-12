"""Tsukuru's Claude sessions: Claude Code runs headless (stream-json) in the project and its
steps become chat blocks: what you asked, what Claude said, the plan, and each tool it used
with its diff or output. Past sessions are read back from Claude Code's own transcripts, so
anything started in a terminal shows up here too, and the other way round (claude --resume).

Block kinds (the page draws them):
  {k:'user', t}            {k:'text', t}  (markdown)        {k:'todo', items:[[text, done]]}
  {k:'tool', id, verb, arg, path, note, diff:[{s,t,n}], out:[...], running, error}
  {k:'denied', items:[...]}  Claude wanted to do something the mode did not allow
  {k:'error', t}
"""

import difflib
import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

import claude_accounts as accounts
from kuhaku_log import report  # faults go to the log instead of vanishing

HOME = Path.home()
MODES = {"auto": "bypassPermissions", "plan": "plan", "ask": "default"}
VERBS = {
    "Bash": "Run",
    "Read": "Read",
    "Write": "Write",
    "Edit": "Edit",
    "MultiEdit": "Edit",
    "NotebookEdit": "Edit",
    "Glob": "Find",
    "Grep": "Search",
    "WebFetch": "Fetch",
    "WebSearch": "Search the web",
    "Task": "Agent",
    "Agent": "Agent",
    "Skill": "Skill",
    "ToolSearch": "Tools",
}
SKIP_TOOLS = {"ToolSearch", "ExitPlanMode", "EnterPlanMode"}


def account():
    """The Claude account Tsukuru's sessions use: the one chosen for dev tools when it is a Claude
    account, else the home screen's."""
    a = accounts.pick(purpose="dev")
    return a if accounts.kind(a) == "claude" else accounts.active()


def project_dir(root, acct=None):
    """Where Claude Code keeps a folder's transcripts: every character but letters and digits becomes '-'."""
    return accounts.config_dir(acct or account()) / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root))


def short(t, n=140):
    t = " ".join(str(t or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def user_text(content):
    """The words the person typed, without Claude Code's own notes in the same message."""
    if isinstance(content, str):
        parts = [content]
    else:
        parts = [c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]
    keep = []
    for p in parts:
        p = re.sub(r"<system-reminder>.*?</system-reminder>", "", p, flags=re.S).strip()
        if p and not p.startswith(("<command-", "<local-command", "Caveat:", "[Request interrupted")):
            keep.append(p)
    return "\n\n".join(keep)


def sessions(root, limit=14):
    """A project's sessions, newest first: id, title and when."""
    d = project_dir(root)
    out = []
    for f in sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True) if d.is_dir() else []:
        title = None
        try:
            with open(f, errors="ignore") as fh:
                for n, line in enumerate(fh):
                    if n > 400:
                        break
                    if '"custom-title"' in line or '"summary"' in line:
                        try:
                            e = json.loads(line)
                            title = e.get("customTitle") or e.get("summary") or title
                        except ValueError:
                            pass
                    if title or '"user"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("isSidechain") or e.get("isMeta"):
                        continue
                    t = user_text((e.get("message") or {}).get("content"))
                    if t:
                        title = short(t, 60)
        except OSError:
            continue
        if not title:
            continue
        out.append({"id": f.stem, "title": title, "at": f.stat().st_mtime})
        if len(out) >= limit:
            break
    return out


def diff_lines(old, new, cap=400):
    """A line diff with a little context, in the page's {s, t, n} rows; '⋯' marks a gap."""
    a, b = (old or "").split("\n"), (new or "").split("\n")
    rows = []
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(2):
        if rows:
            rows.append({"s": " ", "t": "⋯", "n": ""})
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                rows += [{"s": " ", "t": b[j], "n": j + 1} for j in range(j1, j2)]
                continue
            if tag in ("replace", "delete"):
                rows += [{"s": "-", "t": a[i], "n": ""} for i in range(i1, i2)]
            if tag in ("replace", "insert"):
                rows += [{"s": "+", "t": b[j], "n": j + 1} for j in range(j1, j2)]
        if len(rows) > cap:
            break
    return rows[:cap]


def rel(root, p):
    try:
        return str(Path(p).relative_to(root))
    except Exception:
        report()
        return str(p or "")


def tool_block(root, b):
    name, i = b.get("name", ""), b.get("input") or {}
    if name == "TodoWrite":
        return {
            "k": "todo",
            "id": b.get("id"),
            "items": [
                [t.get("content") or t.get("activeForm") or "", t.get("status") == "completed"]
                for t in i.get("todos") or []
            ],
        }
    path = i.get("file_path") or i.get("notebook_path") or i.get("path") or ""
    block = {
        "k": "tool",
        "id": b.get("id"),
        "verb": VERBS.get(name) or name.replace("mcp__", "").replace("__", " ").replace("_", " "),
        "arg": "",
        "path": path,
        "running": True,
    }
    if name == "Bash":
        block["arg"] = short(i.get("command"), 200)
        block["note"] = short(i.get("description"), 60) if i.get("description") else ""
    elif name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        block["arg"] = rel(root, path)
    elif name in ("Glob", "Grep"):
        block["arg"] = short(i.get("pattern"), 120)
    elif name in ("WebFetch",):
        block["arg"] = short(i.get("url"), 120)
    elif name in ("WebSearch",):
        block["arg"] = short(i.get("query"), 120)
    elif name in ("Task", "Agent"):
        block["arg"] = short(i.get("description") or i.get("prompt"), 120)
    elif name == "Skill":
        block["arg"] = short(i.get("skill") or i.get("command"), 80)
    else:
        block["arg"] = short(", ".join(f"{k}: {v}" for k, v in list(i.items())[:2]), 120)
    if name == "Edit":
        block["diff"] = diff_lines(i.get("old_string"), i.get("new_string"))
    elif name == "MultiEdit":
        rows = []
        for e in i.get("edits") or []:
            if rows:
                rows.append({"s": " ", "t": "⋯", "n": ""})
            rows += diff_lines(e.get("old_string"), e.get("new_string"))
        block["diff"] = rows[:400]
    elif name == "Write":
        lines = (i.get("content") or "").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        block["diff"] = [{"s": "+", "t": t, "n": n + 1} for n, t in enumerate(lines[:400])]
    return block


def result_text(content):
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def apply_result(block, r, extra=None):
    """Fill a tool block in from its result: a note and, for commands, the end of the output."""
    text = result_text(r.get("content"))
    err = bool(r.get("is_error"))
    upd = {"id": block.get("id"), "running": False, "error": err}
    verb = block.get("verb")
    if err:
        first = text.strip().splitlines()[0] if text.strip() else "Failed"
        upd["note"] = "Not allowed" if "permission" in text.lower() or "denied" in text.lower() else "Failed"
        upd["out"] = [short(first, 200)] if verb != "Run" else text.strip().splitlines()[-30:]
    elif verb == "Read":
        n = len(re.findall(r"^\s*\d+[\t→]", text, re.M)) or text.count("\n")
        upd["note"] = f"{n} lines"
    elif verb == "Run":
        lines = [ln for ln in text.rstrip().splitlines()]
        upd["out"] = lines[-40:]
        if not block.get("note"):
            upd["note"] = "Done" if lines else "No output"
    elif verb in ("Find", "Search"):
        n = len([ln for ln in text.splitlines() if ln.strip()])
        upd["note"] = "Nothing found" if not n or text.startswith("No ") else f"{n} found"
    elif verb == "Agent":
        upd["note"] = "Done"
    if extra and isinstance(extra, dict) and verb == "Run" and not upd.get("out"):
        out = (extra.get("stdout") or "") + (extra.get("stderr") or "")
        upd["out"] = out.rstrip().splitlines()[-40:]
    return upd


def history(root, sid):
    """A past session as blocks, read from its transcript."""
    f = project_dir(root) / f"{sid}.jsonl"
    blocks, tools = [], {}
    try:
        fh = open(f, errors="ignore")  # noqa: SIM115  closed by the `with fh` below
    except OSError:
        return blocks
    with fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("isSidechain") or e.get("type") not in ("user", "assistant"):
                continue
            msg = e.get("message") or {}
            content = msg.get("content")
            if e["type"] == "user":
                if isinstance(content, list) and any(
                    c.get("type") == "tool_result" for c in content if isinstance(c, dict)
                ):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in tools:
                            b = tools[c["tool_use_id"]]
                            b.update(apply_result(b, c, e.get("toolUseResult")))
                    continue
                if e.get("isMeta"):
                    continue
                t = user_text(content)
                if t:
                    blocks.append({"k": "user", "t": t})
            else:
                for b in content or []:
                    if b.get("type") == "text" and b.get("text", "").strip():
                        blocks.append({"k": "text", "t": b["text"].strip()})
                    elif b.get("type") == "tool_use" and b.get("name") not in SKIP_TOOLS:
                        tb = tool_block(root, b)
                        if tb["k"] == "todo":
                            # One plan per session: the latest version replaces the earlier one in place.
                            old = next((x for x in blocks if x["k"] == "todo"), None)
                            if old:
                                old["items"] = tb["items"]
                                continue
                        tb["running"] = False
                        tools[b.get("id")] = tb
                        blocks.append(tb)
    for b in blocks:
        b.pop("running", None)
    return blocks[-400:]


class Runner:
    """One Claude turn at a time per session; several sessions (and projects) can work at once."""

    def __init__(self, emit, claude=None):
        self.emit = emit  # emit(kind, payload), called on the GTK thread by the caller's wrapper
        self.procs = {}  # sid -> Popen
        self.claude = claude or "claude"
        self.model = None

    def busy(self, sid):
        p = self.procs.get(sid)
        return bool(p and p.poll() is None)

    def stop(self, sid):
        p = self.procs.get(sid)
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGINT)
            except OSError:
                pass

    def send(self, root, sid, text, mode="auto", new=False):
        """Ask Claude in a session. A new session gets its id now, so the page can follow it."""
        if self.busy(sid):
            return sid
        sid = sid or str(uuid.uuid4())
        acct = account()
        argv = [
            self.claude,
            "-p",
            text,
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            MODES.get(mode, mode),
        ]
        argv += ["--session-id", sid] if new else ["--resume", sid]
        env = accounts.env(ident=acct["id"])
        threading.Thread(target=self._run, args=(root, sid, argv, env), daemon=True).start()
        return sid

    def _run(self, root, sid, argv, env):
        root = str(root)
        tools, denied, error, last_text = {}, [], None, ""
        self.emit("busy", {"root": root, "sid": sid, "busy": True})
        try:
            proc = self.procs[sid] = subprocess.Popen(
                argv,
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            self.emit(
                "block", {"root": root, "sid": sid, "block": {"k": "error", "t": "Claude Code is not installed yet."}}
            )
            self.emit("busy", {"root": root, "sid": sid, "busy": False})
            return
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            t = ev.get("type")
            if ev.get("subtype") == "init" and ev.get("model"):
                self.model = ev["model"]
                self.emit("model", {"model": ev["model"]})
            if t == "assistant" and not ev.get("parent_tool_use_id"):
                for b in (ev.get("message") or {}).get("content") or []:
                    if b.get("type") == "text" and b.get("text", "").strip():
                        last_text = b["text"].strip()
                        self.emit("block", {"root": root, "sid": sid, "block": {"k": "text", "t": last_text}})
                    elif b.get("type") == "tool_use" and b.get("name") not in SKIP_TOOLS:
                        tb = tool_block(root, b)
                        tools[b.get("id")] = tb
                        self.emit("block", {"root": root, "sid": sid, "block": tb})
            elif t == "user" and not ev.get("parent_tool_use_id"):
                for c in (ev.get("message") or {}).get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in tools:
                        b = tools[c["tool_use_id"]]
                        self.emit(
                            "update", {"root": root, "sid": sid, "upd": apply_result(b, c, ev.get("tool_use_result"))}
                        )
            elif t == "result":
                for d in ev.get("permission_denials") or []:
                    i = d.get("tool_input") or {}
                    what = i.get("command") or rel(root, i.get("file_path")) or d.get("tool_name")
                    denied.append(f"{VERBS.get(d.get('tool_name'), d.get('tool_name'))}  {short(what, 120)}")
                if ev.get("is_error"):
                    error = ev.get("result") or "; ".join(map(str, ev.get("errors") or [])) or None
        err = proc.stderr.read() if proc.stderr else ""
        code = proc.wait()
        if self.procs.get(sid) is proc:
            del self.procs[sid]
        for b in tools.values():
            if b.get("running"):
                self.emit("update", {"root": root, "sid": sid, "upd": {"id": b["id"], "running": False}})
        if denied:
            self.emit("block", {"root": root, "sid": sid, "block": {"k": "denied", "items": denied[:8]}})
        said = (error or "") + err
        if any(k in said for k in ("Invalid API key", "/login", "Not logged in", "not logged in")):
            error = "Claude is not signed in. Sign in from Kūhaku's Settings, then ask again."
        elif code and code != -signal.SIGINT and code != 130 and not error and not last_text:
            error = (err.strip().splitlines() or ["Claude stopped unexpectedly."])[-1][:300]
        if error and code != 130:
            self.emit("block", {"root": root, "sid": sid, "block": {"k": "error", "t": short(error, 400)}})
        needs = bool(denied) or last_text.rstrip().endswith("?")
        self.emit("busy", {"root": root, "sid": sid, "busy": False, "needs": needs, "at": time.time()})


def commit_message(root, diff):
    """A short commit message for the changes, from a small fast model without tools."""
    prompt = (
        "Write a git commit message for these changes: one line, at most 60 characters, imperative mood, "
        "no quotes, no trailing period. Reply with the line only.\n\n" + diff[:40000]
    )
    a = accounts.pick(purpose="dev")
    if accounts.kind(a) == "claude":
        # Run outside the project, so this helper never shows up among the project's sessions.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(
                ["claude", "-p", "--model", "haiku", "--tools", "", "--no-session-persistence"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=tmp,
                env=accounts.env(ident=a["id"]),
            )
        text = r.stdout.strip()
    else:
        text = accounts.ask(prompt, "You write git commit messages.", purpose="dev", cwd=str(root), timeout=120)
    return (text.strip().splitlines() or [""])[0].strip().strip('"`')[:100]
