#!/usr/bin/env python3
"""Kiroku (記録, "the record"): every job an AI has done on this computer.

Who did it (Kūhaku's tabs, Tegami, the terminal), with which model, how long it
took, how many tokens it read and wrote, what it would cost at API prices and
how much energy the laptop used for it. A past job can be read again as a
whole conversation, or continued: it comes back as a tab on the home screen.

Reads Kūhaku's own job lines (~/.local/state/kuhaku/jobs.jsonl), the older
log of the home screen (claude.log) and Claude Code's transcripts of every
account. Nothing is changed; the export is a CSV for your own numbers.
"""

import csv
import json
import os
import re
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GLib, Gtk, WebKit2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import claude_accounts as accounts
import jobs
from kansei import theme
from kuhaku_log import report  # faults go to the log instead of vanishing

HOME = Path.home()
STATE = HOME / ".local/state/kuhaku"
LOG_FILE = STATE / "claude.log"
FIFO = STATE / "kuhaku.fifo"
MAIL_DIR = HOME / ".local/share/kuhaku/mail"


def model_name(m):
    """claude-opus-5-5 -> Opus 5.5, claude-haiku-4-5-20251001 -> Haiku 4.5."""
    m = (m or "").removeprefix("claude-")
    parts = [p for p in m.split("-") if not (p.isdigit() and len(p) == 8)]
    if not parts:
        return ""
    return parts[0].capitalize() + (" " + ".".join(parts[1:]) if parts[1:] else "")


def main_model(models, fallback=None):
    """The model that did the work: the one that cost the most (helpers like Haiku come second)."""
    if models:
        return max(models.items(), key=lambda kv: kv[1].get("costUSD") or 0)[0]
    return fallback


def tokens_of(models):
    t = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}
    for u in (models or {}).values():
        t["in"] += u.get("inputTokens", 0)
        t["out"] += u.get("outputTokens", 0)
        t["cache_read"] += u.get("cacheReadInputTokens", 0)
        t["cache_write"] += u.get("cacheCreationInputTokens", 0)
    return t


def per_run_cost(r, prev):
    """Claude Code counts a resumed conversation's cost from its very start; one run is the difference."""
    sid, total = r.get("session_id"), r.get("cost")
    if total is None or not sid:
        return
    before = prev.get(sid, 0)
    r["cost"] = round(total - before if total >= before else total, 6)
    prev[sid] = total


def from_jobs_file(prev):
    out: list[dict] = []
    try:
        lines = jobs.RUNS_FILE.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        r["model"] = main_model(r.get("models"), r.get("model"))
        r.setdefault("tokens", tokens_of(r.get("models")))
        per_run_cost(r, prev)
        out.append(r)
    return out


HEADER = re.compile(r"^=== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)  (?:\[tab (\w+)\]  )?(.*)$")


def from_old_log(before, prev):
    """The home screen's jobs from before Kiroku kept its own lines (claude.log, stream-json)."""
    runs: list[dict] = []
    open_runs: list[dict] = []
    by_session: dict[str, dict] = {}
    try:
        fh = open(LOG_FILE, errors="ignore")  # noqa: SIM115  closed by the `with fh` below
    except OSError:
        return runs
    with fh:
        for line in fh:
            m = HEADER.match(line)
            if m:
                start = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
                run = {
                    "start": start,
                    "tab": m.group(2),
                    "question": m.group(3),
                    "client": "Kūhaku",
                    "outcome": "stopped",
                    "events": 0,
                }
                if start < before:
                    runs.append(run)
                    open_runs.append(run)
                continue
            if not line.startswith("{"):
                # A question of several lines continues until the first event.
                if open_runs and not open_runs[-1]["events"] and not open_runs[-1].get("session_id"):
                    open_runs[-1]["question"] += "\n" + line.rstrip("\n")
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            sid = ev.get("session_id")
            if not sid:
                continue
            r = by_session.get(sid)
            if ev.get("subtype") == "init" or r is None:
                # Jobs can run side by side: each start belongs to the oldest job not yet started.
                r = next((x for x in open_runs if not x.get("session_id")), None)
                if r is None:
                    continue
                r["session_id"] = sid
                by_session[sid] = r
                if ev.get("subtype") == "init":
                    r.update(model=ev.get("model"), cwd=ev.get("cwd"))
            r["events"] += 1
            if ev.get("type") == "result":
                u = ev.get("usage") or {}
                r.update(
                    cost=ev.get("total_cost_usd"),
                    turns=ev.get("num_turns"),
                    api_ms=ev.get("duration_api_ms"),
                    models=ev.get("modelUsage"),
                    answer=(ev.get("result") or "")[:4000],
                    outcome="error" if ev.get("is_error") else "done",
                    tokens={
                        "in": u.get("input_tokens", 0),
                        "out": u.get("output_tokens", 0),
                        "cache_read": u.get("cache_read_input_tokens", 0),
                        "cache_write": u.get("cache_creation_input_tokens", 0),
                    },
                )
                if ev.get("duration_ms"):
                    r["end"] = r["start"] + ev["duration_ms"] / 1000
                    r["seconds"] = round(ev["duration_ms"] / 1000, 1)
                r["model"] = main_model(r.get("models"), r.get("model"))
                per_run_cost(r, prev)
                if r in open_runs:
                    open_runs.remove(r)
                by_session.pop(sid, None)
    for r in runs:
        r.pop("events", None)
        r["question"] = r["question"].strip()
    return runs


def jobs_start():
    """When Kūhaku began keeping its own job lines; the old log covers what came before."""
    try:
        with open(jobs.RUNS_FILE) as fh:
            return json.loads(fh.readline())["start"]
    except Exception:
        report()
        return time.time() + 1


def transcript_dirs():
    """Each Claude account's transcripts, with the account it belongs to."""
    seen, out = set(), []
    try:
        accts = accounts.load()["accounts"]
    except Exception:
        accts = []
    for a in accts or [None]:
        d = accounts.config_dir(a) / "projects"
        if d not in seen and d.exists():
            seen.add(d)
            out.append((d, (a or {}).get("id") if a and accts and a is not accts[0] else None))
    if not out and (HOME / ".claude/projects").exists():
        out.append((HOME / ".claude/projects", None))
    return out


def text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def real_prompt(e):
    """A question someone typed, not a tool's result or a reminder the system added."""
    if e.get("type") != "user" or e.get("isMeta") or e.get("isSidechain"):
        return None
    content = (e.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    t = text_of(content).strip()
    if not t or t.startswith(("<command-", "<local-command", "<system-reminder>", "Caveat:")):
        return None
    return t


def session_files():
    for d, acct in transcript_dirs():
        for f in d.glob("*/*.jsonl"):
            yield f, acct


_sessions: dict = {}


def from_transcripts(known):
    """Conversations Kūhaku's tabs did not start: Tegami's assistant, the terminal, Tsukuru."""
    out = []
    for f, acct in session_files():
        sid = f.stem
        if sid in known:
            continue
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        hit = _sessions.get(f)
        if hit and hit[0] == mtime:
            out.append(hit[1])
            continue
        r = {
            "session_id": sid,
            "account": acct,
            "client": "Claude Code",
            "outcome": "done",
            "question": "",
            "answer": "",
            "tokens": {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0},
        }
        seen: set = set()
        costs: dict = {}
        models: dict = {}
        first = last = None
        with open(f, errors="ignore") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                ts = e.get("timestamp")
                if ts:
                    try:
                        t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                        first, last = first or t, t
                    except ValueError:
                        pass
                if e.get("entrypoint") and "entry" not in r:
                    r["entry"], r["cwd"] = e["entrypoint"], e.get("cwd")
                if e.get("type") == "cost-state":
                    # One per run of Claude Code; a resumed conversation starts counting again.
                    costs[e.get("startTime")] = (e.get("totalCostUSD") or 0, e.get("totalDuration") or 0)
                    # (totals carry over when a conversation is resumed, so the largest is the whole)
                q = real_prompt(e)
                if q and not r["question"]:
                    r["question"] = q
                if q:
                    r["turns_asked"] = r.get("turns_asked", 0) + 1
                if e.get("type") == "assistant" and not e.get("isSidechain"):
                    msg = e.get("message") or {}
                    key = (msg.get("id"), e.get("requestId"))
                    if msg.get("usage") and key not in seen:
                        seen.add(key)
                        u = msg["usage"]
                        r["tokens"]["in"] += u.get("input_tokens", 0)
                        r["tokens"]["out"] += u.get("output_tokens", 0)
                        r["tokens"]["cache_read"] += u.get("cache_read_input_tokens", 0)
                        r["tokens"]["cache_write"] += u.get("cache_creation_input_tokens", 0)
                        models[msg.get("model")] = models.get(msg.get("model"), 0) + u.get("output_tokens", 0)
                    t = text_of(msg.get("content")).strip()
                    if t:
                        r["answer"] = t[:4000]
        if not r["question"] and not models:
            continue
        cwd = r.get("cwd") or ""
        r["client"] = (
            "Tegami" if cwd.startswith(str(MAIL_DIR)) else "Terminal" if r.get("entry") == "cli" else "Claude Code"
        )
        r["model"] = max(models, key=lambda m: models[m]) if models else None
        r["cost"] = round(max(c for c, _ in costs.values()), 4) if costs else None
        r["start"], r["end"] = first, last
        busy = max(d for _, d in costs.values()) / 1000 if costs else None
        r["seconds"] = round(busy if busy else (last - first) if first and last else 0, 1)
        _sessions[f] = (mtime, r)
        out.append(r)
    return out


def from_tabs(runs):
    """Jobs at work or waiting in line right now, from the home screen's tabs."""
    try:
        tabs = json.loads((STATE / "tabs.json").read_text())["tabs"]
    except Exception:
        report()
        return runs
    for t in tabs:
        if t.get("status") not in ("running", "queued") or t.get("anon"):
            continue
        # The log's unfinished entry for this tab is this very job.
        mine = [r for r in runs if r.get("tab") == t["id"] and r.get("outcome") == "stopped"]
        last = max(mine, key=lambda r: r["start"], default=None)
        if last and t["status"] == "running":
            last["outcome"] = "running"
            last["title"] = t.get("title")
            continue
        runs.append(
            {
                "start": t.get("queued") or time.time(),
                "tab": t["id"],
                "title": t.get("title"),
                "question": t.get("question") or "",
                "client": "Kūhaku",
                "agent": t.get("agent"),
                "session_id": t.get("session_id"),
                "outcome": "running" if t["status"] == "running" else "waiting",
            }
        )
    return runs


def anonymous_sessions():
    """Conversations in anonymous tabs: never shown (they are erased when their tab closes)."""
    try:
        return {t.get("session_id") for t in json.loads((STATE / "tabs.json").read_text())["tabs"] if t.get("anon")}
    except Exception:
        report()
        return set()


def all_runs():
    prev: dict = {}
    old = from_old_log(jobs_start(), prev)
    runs = from_tabs(old + from_jobs_file(prev))
    known = {r.get("session_id") for r in runs if r.get("session_id")} | anonymous_sessions()
    runs += from_transcripts(known)
    out = []
    for r in runs:
        if not r.get("start"):
            continue
        tk = r.get("tokens") or {}
        out.append(
            {
                "start": r["start"],
                "end": r.get("end"),
                "seconds": r.get("seconds"),
                "client": r.get("client"),
                "tab": r.get("tab"),
                "title": r.get("title"),
                "question": (r.get("question") or "")[:3000],
                "answer": r.get("answer") or "",
                "model": r.get("model"),
                "model_name": model_name(r.get("model")),
                "helpers": [model_name(m) for m in (r.get("models") or {}) if m != r.get("model")],
                "agent": r.get("agent") or "",
                "account": r.get("account"),
                "session_id": r.get("session_id"),
                "outcome": r.get("outcome"),
                "cost": r.get("cost"),
                "turns": r.get("turns"),
                "api_ms": r.get("api_ms"),
                "tokens": tk,
                "total_tokens": sum(tk.values()) if tk else 0,
                "cpu": r.get("cpu"),
                "memory_mb": r.get("memory_mb"),
                "wh": r.get("wh"),
                "wh_from": r.get("wh_from"),
                "cwd": r.get("cwd"),
            }
        )
    out.sort(key=lambda r: r["start"], reverse=True)
    # Which runs share a conversation: a thread is every run with the same session.
    count: dict = {}
    for r in out:
        if r["session_id"]:
            count[r["session_id"]] = count.get(r["session_id"], 0) + 1
    for r in out:
        r["thread"] = count.get(r["session_id"], 1)
    return out


def thread(session_id):
    """The whole conversation, as it was said: questions and answers, with the work in between counted."""
    f = next((f for f, _ in session_files() if f.stem == session_id), None)
    if not f:
        return None
    items: list = []
    work = 0
    with open(f, errors="ignore") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            q = real_prompt(e)
            if q:
                if work and items:
                    items.append({"who": "work", "steps": work})
                work = 0
                items.append({"who": "you", "text": q[:20000], "time": e.get("timestamp")})
            elif e.get("type") == "assistant" and not e.get("isSidechain"):
                content = (e.get("message") or {}).get("content") or []
                if any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content):
                    work += 1
                t = text_of(content).strip()
                if t:
                    if items and items[-1]["who"] == "ai":
                        items[-1]["text"] += "\n\n" + t
                    else:
                        if work:
                            items.append({"who": "work", "steps": work})
                            work = 0
                        items.append(
                            {
                                "who": "ai",
                                "text": t[:20000],
                                "time": e.get("timestamp"),
                                "model": model_name((e.get("message") or {}).get("model")),
                            }
                        )
    return items


def export_csv(runs):
    folder = HOME / "Downloads"
    folder.mkdir(exist_ok=True)
    path = folder / f"kiroku-{time.strftime('%Y-%m-%d')}.csv"
    cols = [
        "started",
        "seconds",
        "client",
        "model",
        "agent",
        "title",
        "question",
        "outcome",
        "cost_usd",
        "tokens_in",
        "tokens_out",
        "cache_read",
        "cache_write",
        "turns",
        "cpu_seconds",
        "memory_peak_mb",
        "energy_wh",
        "energy_from",
        "session_id",
    ]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in runs:
            tk = r.get("tokens") or {}
            w.writerow(
                [
                    datetime.fromtimestamp(r["start"]).isoformat(timespec="seconds"),
                    r.get("seconds"),
                    r.get("client"),
                    r.get("model"),
                    r.get("agent"),
                    r.get("title"),
                    r.get("question"),
                    r.get("outcome"),
                    r.get("cost"),
                    tk.get("in"),
                    tk.get("out"),
                    tk.get("cache_read"),
                    tk.get("cache_write"),
                    r.get("turns"),
                    r.get("cpu"),
                    r.get("memory_mb"),
                    r.get("wh"),
                    r.get("wh_from"),
                    r.get("session_id"),
                ]
            )
    return path


def send_home(line):
    """A word to the home screen through its FIFO; False when Kūhaku is not listening."""
    try:
        fd = os.open(FIFO, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        os.write(fd, (line + "\n").encode())
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


class Kiroku:
    def __init__(self):
        self.night = theme() == "night"
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", self.night)
        self.win = Gtk.Window(title="Kiroku")
        self.win.set_decorated(False)
        self.win.set_icon_name("kiroku")
        self.win.maximize()
        self.win.connect("destroy", Gtk.main_quit)
        ucm = WebKit2.UserContentManager()
        ucm.register_script_message_handler("kiroku")
        ucm.connect("script-message-received::kiroku", self.on_message)
        self.view = WebKit2.WebView.new_with_user_content_manager(ucm)
        self.view.set_zoom_level(1.12)
        self.view.get_settings().set_enable_write_console_messages_to_stdout(bool(os.environ.get("KIROKU_DEBUG")))
        bg = (0.071, 0.071, 0.071) if self.night else (0.969, 0.957, 0.945)
        self.view.set_background_color(Gdk.RGBA(*bg, 1))
        self.view.connect("context-menu", lambda *a: True)
        self.win.add(self.view)
        self.view.load_uri((HERE / "kiroku" / "kiroku.html").as_uri() + f"?theme={'night' if self.night else 'day'}")
        self.view.connect("load-changed", lambda v, e: e == WebKit2.LoadEvent.FINISHED and self.refresh())
        self.win.show_all()
        self.runs = []
        GLib.timeout_add_seconds(20, self.refresh)  # jobs still running land here when they finish

    def js(self, fn, payload):
        self.view.evaluate_javascript(f"{fn}({json.dumps(payload)})", -1, None, None, None, None, None)
        return False

    def refresh(self):
        def work():
            runs = all_runs()
            self.runs = runs
            GLib.idle_add(self.js, "setRuns", {"runs": runs, "now": time.time()})

        threading.Thread(target=work, daemon=True).start()
        return True

    def on_message(self, _m, result):
        try:
            msg = json.loads(result.get_js_value().to_string())
        except Exception:
            report()
            return
        k = msg.get("type")
        if k == "thread":
            sid = msg.get("session_id")
            threading.Thread(
                target=lambda: GLib.idle_add(self.js, "showThread", {"session_id": sid, "items": thread(sid)}),
                daemon=True,
            ).start()
        elif k == "continue":
            agent = msg.get("agent") or (("acct:" + msg["account"]) if msg.get("account") else "")
            if send_home(f"continue {msg.get('session_id')} {agent}".strip()):
                self.win.iconify()
            else:
                self.js("toast", "Kūhaku's home screen is not running")
        elif k == "export":
            ids = set(msg.get("starts") or [])
            path = export_csv([r for r in self.runs if not ids or r["start"] in ids])
            self.js("toast", f"Saved to Downloads as {path.name}")
        elif k == "refresh":
            self.refresh()
        elif k == "close":
            self.win.destroy()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    Kiroku()
    Gtk.main()
