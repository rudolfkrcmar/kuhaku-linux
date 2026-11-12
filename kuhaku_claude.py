"""Questions as jobs: each tab runs Claude Code in the background (claude -p, stream-json), names itself, finishes with an answer, and can be continued.

A part of the Kuhaku window class (kuhaku.py), kept here by subject."""

import json
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib

import claude_accounts as accounts
import jobs
import keepawake
from kuhaku_base import Home
from kuhaku_config import ANSWERS_FILE, HOME, LOG_FILE, SYSTEM_PROMPT, VOICE_PROMPT
from kuhaku_log import report
from kuhaku_system import _limits_cache, describe, note_limits, plan_limits, spawn


class ClaudeJobs(Home):
    def ask_new_tab(self, text):
        """A job from another app: a new tab of its own, started at once, with Kūhaku in front."""
        cur = self.tabs.get(self.tabs.current)
        cur["session_id"] = self.state.get("session_id")
        self.tabs.new()
        self.state["session_id"] = None
        self.tabs.save()
        self.save_state()
        subprocess.run(["wmctrl", "-k", "on"], capture_output=True)
        self.window.present()
        self.js("if (page) closePage(); if (panel) closePanel()")
        self.restore_tab()
        self.push_tabs()
        self.ask(text)
        self.push_tabs()
        self.restore_tab()
        return False

    def continue_thread(self, session_id, agent=""):
        """A finished conversation comes back as a tab (the one it lives in, if still open), ready for the next question."""
        cur = self.tabs.get(self.tabs.current)
        cur["session_id"] = self.state.get("session_id")
        tab = next((t for t in self.tabs.tabs if t.get("session_id") == session_id), None)
        if tab:
            self.tabs.current = tab["id"]
        else:
            tab = self.tabs.new()
            tab.update(session_id=session_id, agent=agent if agent.startswith(("acct:", "model:", "agent:")) else "")
            past = next((r for r in reversed(self.load_runs()) if r.get("session_id") == session_id), None)
            if past:
                tab["title"] = past.get("title") or jobs.title_of(past.get("question") or "")
                tab["answer"] = {
                    "question": past.get("question", ""),
                    "answer": past.get("answer", ""),
                    "error": past.get("outcome") == "error",
                    "time": int(past.get("end") or 0),
                    "tab": tab["id"],
                }
        self.state["session_id"] = session_id
        self.tabs.save()
        self.save_state()
        subprocess.run(["wmctrl", "-k", "on"], capture_output=True)
        self.window.present()
        self.js("if (page) closePage(); if (panel) closePanel()")
        self.restore_tab()
        self.push_tabs()
        return False

    def load_runs(self):
        try:
            return [json.loads(l) for l in jobs.RUNS_FILE.read_text().splitlines() if l.strip()]
        except Exception:
            report()
            return []

    # --- Claude -----------------------------------------------------------
    def ask(self, text, voice=False):
        """A question goes into the open tab's place in the line; schedule() starts it when there is room."""
        text = text.strip()
        tab = self.tabs.get(self.tabs.current)
        if not text or tab["status"] in ("running", "queued"):
            return
        at, text = jobs.parse_when(text)
        tab.update(question=text, status="queued", queued=time.time(), at=at, note=None, unseen=False, voice=voice)
        if not tab["title"]:
            tab["title"] = jobs.title_of(text)
            if not tab.get("named"):
                threading.Thread(target=self.name_tab, args=(tab["id"], text), daemon=True).start()
        self.tabs.save()
        self.schedule()

    def name_tab(self, tab_id, text):
        """Claude (the quick one, without tools) gives a new job a short name while it starts."""
        try:
            r = subprocess.run(
                [
                    self.claude,
                    "-p",
                    "--model",
                    "haiku",
                    "--tools",
                    "",
                    "--no-session-persistence",
                    "--system-prompt",
                    "Name the task you are given. Reply with only a title of one to "
                    "four words in the task's language: no quotes, no full stop, nothing else.",
                ],
                input=text[:3000],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(HOME),
                env=accounts.env(),
            )
            name = (r.stdout.strip().splitlines() or [""])[0].strip(" \"'.*") if r.returncode == 0 else ""
        except Exception:
            name = ""
        if name and len(name) <= 40:
            GLib.idle_add(self.set_title, tab_id, name)

    def set_title(self, tab_id, name):
        tab = self.tabs.get(tab_id)
        if tab and not tab.get("named"):
            tab["title"] = name
            self.tabs.save()
            self.push_tabs()
        return False

    def agent_choices(self):
        """Who can do a tab's job: Claude, its quicker models, your own Claude agents, other accounts."""
        # Which version each choice really is: what Claude Code said it started, else today's latest.
        seen = self.state.get("models") or {}
        default = self.config.get("model") or seen.get("") or "claude-opus-5-5"
        out = [
            {
                "id": "",
                "name": jobs.model_label(default),
                "about": "the default; most capable, for building and hard problems",
            },
            {
                "id": "model:sonnet",
                "name": jobs.model_label(seen.get("model:sonnet") or "claude-sonnet-5-5"),
                "about": "quick and capable, for everyday jobs; lighter on your plan",
            },
            {
                "id": "model:haiku",
                "name": jobs.model_label(seen.get("model:haiku") or "claude-haiku-5-5"),
                "about": "the fastest, for short simple jobs; lightest on your plan",
            },
        ]
        for f in sorted((accounts.config_dir() / "agents").glob("*.md")):
            head = f.read_text(errors="ignore")[:2000]
            m = re.search(r"^name:\s*(.+)$", head, re.M)
            d = re.search(r"^description:\s*(.+)$", head, re.M)
            name = m.group(1).strip().strip("\"'") if m else f.stem
            out.append(
                {
                    "id": "agent:" + name,
                    "name": name.replace("-", " ").capitalize(),
                    "about": "your agent" + (": " + d.group(1).strip().strip("\"'")[:90] if d else ""),
                }
            )
        accts = accounts.ready("claude")  # an account that is not signed in cannot do a job
        if len(accts) > 1:
            out += [
                {
                    "id": "acct:" + a["id"],
                    "name": "Claude · " + a["name"],
                    "about": jobs.model_label(default) + " in your " + a["name"] + " account",
                }
                for a in accts
            ]
        return out

    def argv_for(self, tab):
        argv = [
            self.claude,
            "-p",
            tab["question"],
            "--output-format",
            "stream-json",
            "--verbose",
            "--append-system-prompt",
            SYSTEM_PROMPT + (VOICE_PROMPT if tab.get("voice") else ""),
        ]
        if self.config.get("permission_mode"):
            argv += ["--permission-mode", self.config["permission_mode"]]
        tools = list(self.config.get("allowed_tools") or [])
        if self.config.get("permission_mode") != "bypassPermissions":
            # Kūhaku cannot show a permission prompt, so anything short of "Anything,
            # without asking" is careful: no commands at all, rather than commands unasked.
            tools = [t for t in tools if t != "Bash" and not t.startswith("Bash(")]
            argv += ["--disallowedTools", "Bash"]
        if tools:
            argv += ["--allowedTools", " ".join(tools)]
        kind, _, name = (tab.get("agent") or "").partition(":")
        if kind == "model":
            argv += ["--model", name]
        elif self.config.get("model"):
            argv += ["--model", self.config["model"]]
        if kind == "agent":
            argv += ["--agent", name]
        if tab.get("session_id"):
            argv += ["--resume", tab["session_id"]]
        return argv

    def schedule(self):
        """Start what the line allows: memory and the Claude plan decide how many run at once."""
        waiting = self.tabs.waiting()
        if waiting:
            threading.Thread(target=plan_limits, daemon=True).start()  # fresh numbers for the next look
        room, why, resets = jobs.slots(
            len(self.tabs.running()), int(self.config.get("max_jobs") or 3), _limits_cache[1]
        )
        for tab in waiting[:room]:
            tab.update(status="running", note=None)
            self.live[tab["id"]] = {"activity": [], "tokens": None}
            acct = (tab.get("agent") or "")[5:] if (tab.get("agent") or "").startswith("acct:") else None
            if acct and acct not in {a["id"] for a in accounts.ready("claude")}:
                acct = None  # chosen before it was signed in (or signed out since): the active one does it
            threading.Thread(
                target=self.run_claude, args=(self.argv_for(tab), tab["question"], tab["id"], acct), daemon=True
            ).start()
        back = ""
        if resets:
            try:
                back = " until " + datetime.fromisoformat(resets.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
            except ValueError:
                pass
        note = {"plan": "Waiting for your Claude plan" + back, "memory": "Waiting for memory", "busy": "In line"}.get(
            why or ""
        )
        for tab in waiting[room:]:
            tab["note"] = note
        self.tabs.save()
        self.push_tabs()
        return True

    def push_tabs(self):
        self.send("setTabs", dict(self.tabs.public(), agents=self.agent_choices()))

    def restore_tab(self):
        """The open tab as it is: working (with what Claude has done so far), waiting, or at rest."""
        tab = self.tabs.get(self.tabs.current)
        live = self.live.get(tab["id"]) or {}
        self.send(
            "restoreTab",
            {
                "id": tab["id"],
                "status": tab["status"],
                "question": tab.get("question", ""),
                "activity": live.get("activity", []),
                "tokens": live.get("tokens"),
                "answer": tab.get("answer"),
                "draft": tab.get("draft", ""),
                "next": tab.get("next", ""),
            },
        )

    def tab_action(self, msg):
        action, ident = msg.get("action"), msg.get("id")
        cur = self.tabs.get(self.tabs.current)
        cur["session_id"] = self.state.get("session_id")
        if "draft" in msg:  # what was typed in the tab being left stays with it
            cur.update(draft=str(msg.get("draft") or "")[:20000], next=str(msg.get("next") or "")[:20000])
        t = self.tabs.get(ident)
        if action == "new":
            self.tabs.new()
        elif action == "open" and self.tabs.get(ident):
            self.tabs.current = ident
        elif action == "close" and self.tabs.get(ident):
            self.cancel(ident)
            if t.get("anon"):
                acct = next((a for a in accounts.load()["accounts"] if "acct:" + a["id"] == t.get("agent")), None)
                jobs.erase_session(accounts.config_dir(acct), t.get("session_id"))
            self.tabs.close(ident)
        elif action == "anon" and t and t["status"] not in ("running", "queued"):
            # Into or out of anonymous: a fresh conversation, and an anonymous one is erased.
            if t.get("anon"):
                acct = next((a for a in accounts.load()["accounts"] if "acct:" + a["id"] == t.get("agent")), None)
                jobs.erase_session(accounts.config_dir(acct), t.get("session_id"))
            t.update(anon=not t.get("anon"), session_id=None, answer=None)
        elif action == "first" and self.tabs.get(ident):
            t = self.tabs.get(ident)
            t["priority"] = jobs.NORMAL if t.get("priority") == jobs.FIRST else jobs.FIRST
        elif action == "now" and t:
            t["at"] = None
        elif action == "rename" and t:
            title = re.sub(r"\s+", " ", str(msg.get("title") or "")).strip()[:60]
            t.update(title=title or jobs.title_of(t.get("question") or ""), named=bool(title))
        elif action == "agent" and t and t["status"] not in ("running", "queued"):
            agent = str(msg.get("agent") or "")
            if agent.startswith("acct:") != (t.get("agent") or "").startswith("acct:") or (
                agent.startswith("acct:") and agent != t.get("agent")
            ):
                t["session_id"] = None  # a conversation lives in one account; another starts afresh
            t["agent"] = agent
        tab = self.tabs.get(self.tabs.current)
        tab["unseen"] = False
        self.state["session_id"] = tab.get("session_id")
        self.tabs.save()
        self.save_state()
        if action in ("new", "open", "close"):
            self.restore_tab()
        self.schedule()

    def run_claude(self, argv, question, tab_id, acct=None):
        env = accounts.env(ident=acct)
        env["PATH"] = f"{HOME}/.local/bin:" + env.get("PATH", "/usr/bin:/bin")
        result, error, session_id, lost = None, None, None, False
        keepawake.hold()  # no idle sleep while Claude works; released when the answer is in
        tokens: dict[str, Any] = {"context": 0, "out": {}, "steps": 0}
        live = self.live.setdefault(tab_id, {"activity": [], "tokens": None})
        threading.Thread(target=lambda: GLib.idle_add(self.send, "setLimits", plan_limits()), daemon=True).start()
        tab = self.tabs.get(tab_id) or {}
        run = {
            "tab": tab_id,
            "title": tab.get("title"),
            "question": question,
            "agent": tab.get("agent") or "",
            "account": acct,
            "client": "Kūhaku",
            "start": time.time(),
            "outcome": "stopped",
        }
        cpu, watts_at, peak = None, 0.0, 0.0
        watts: list[float] = []
        anon = bool(tab.get("anon"))  # anonymous: no log, no line in Kiroku, no saved answer
        with open(os.devnull if anon else LOG_FILE, "a") as log:
            log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}  [tab {tab_id}]  {question}\n")
            log.flush()
            try:
                proc = self.procs[tab_id] = subprocess.Popen(
                    argv,
                    cwd=HOME,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
                for line in proc.stdout or []:
                    log.write(line)
                    cpu = jobs.cpu_seconds(proc.pid) or cpu
                    if time.time() - watts_at > 5:
                        watts_at = time.time()
                        w = jobs.battery_watts()
                        if w:
                            watts.append(w)
                        peak = max(peak, jobs.memory_mb(proc.pid) or 0)
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    session_id = ev.get("session_id") or session_id
                    if ev.get("type") == "rate_limit_event":
                        lim = note_limits(ev.get("rate_limit_info"))
                        if lim:
                            GLib.idle_add(self.send, "setLimits", lim)
                    if ev.get("subtype") == "init":
                        run.update(model=ev.get("model"), cwd=ev.get("cwd"))
                        if ev.get("model") and not run["agent"].startswith(("agent:", "acct:")):
                            self.state.setdefault("models", {})[run["agent"]] = ev["model"]
                    for step in describe(ev):
                        live["activity"] = (live["activity"] + [step])[-200:]
                        GLib.idle_add(self.send, "addActivity", dict(step, tab=tab_id))
                    if ev.get("type") == "assistant":
                        msg = ev.get("message") or {}
                        u = msg.get("usage") or {}
                        if u:
                            # The latest call's input is the whole conversation so far.
                            tokens["context"] = (
                                u.get("input_tokens", 0)
                                + u.get("cache_read_input_tokens", 0)
                                + u.get("cache_creation_input_tokens", 0)
                            )
                            tokens["out"][msg.get("id")] = u.get("output_tokens", 0)
                            tokens["steps"] = len(tokens["out"])
                            live["tokens"] = {
                                "context": tokens["context"],
                                "written": sum(tokens["out"].values()),
                                "steps": tokens["steps"],
                                "tab": tab_id,
                            }
                            GLib.idle_add(self.send, "setTokens", live["tokens"])
                    if ev.get("type") == "result":
                        if ev.get("is_error") and not ev.get("result"):
                            why = "; ".join(str(e) for e in (ev.get("errors") or []))[:300]
                            if "No conversation found" in why and "--resume" in argv:
                                # The conversation lives in another account: start this tab afresh there.
                                lost = True
                            error = "Something went wrong" + (": " + why if why else ".")
                        result = ev.get("result")
                        u = ev.get("usage") or {}
                        run.update(
                            cost=ev.get("total_cost_usd"),
                            turns=ev.get("num_turns"),
                            api_ms=ev.get("duration_api_ms"),
                            models=ev.get("modelUsage"),
                            tokens={
                                "in": u.get("input_tokens", 0),
                                "out": u.get("output_tokens", 0),
                                "cache_read": u.get("cache_read_input_tokens", 0),
                                "cache_write": u.get("cache_creation_input_tokens", 0),
                            },
                        )
                code = proc.wait()
                # First start: Claude Code is there but not signed in yet. Open its sign-in once.
                said = (result or "") + " " + (error or "")
                if any(
                    k in said for k in ("Invalid API key", "/login", "Not logged in", "not logged in", "Please run")
                ):
                    result, error = None, "Sign in to Claude in the window that just opened, then ask again."
                    GLib.idle_add(self.first_sign_in)
                if self.procs.get(tab_id) is not proc:
                    return  # cancelled
                run["outcome"] = "done" if result else "error"
                if result is None and error is None:
                    error = "Claude stopped unexpectedly." if code else "Done."
            except FileNotFoundError:
                error = "Claude is getting ready. Connect to Wi‑Fi; it installs itself in a minute, then ask again."
                run["outcome"] = "error"
                GLib.idle_add(self.install_claude_code)
            finally:
                if self.procs.get(tab_id) is not None and self.procs[tab_id].poll() is not None:
                    self.procs.pop(tab_id, None)
                keepawake.release()
                # One line per job for Kiroku: who did it, with which model, how long, what it cost.
                run.update(
                    end=time.time(),
                    session_id=session_id,
                    cpu=cpu,
                    answer=(result or error or "")[:4000],
                    memory_mb=round(peak) or None,
                )
                run["seconds"] = round(run["end"] - run["start"], 1)
                run["wh"], run["wh_from"] = jobs.energy_wh(run["seconds"], cpu, watts)
                if not anon:
                    jobs.log_run(run)
        if lost and not result:
            i = argv.index("--resume")
            if self.tabs.get(tab_id):
                self.tabs.get(tab_id)["session_id"] = None
            return self.run_claude(argv[:i] + argv[i + 2 :], question, tab_id, acct)
        GLib.idle_add(self.finish, question, result, error, session_id, tab_id)

    def first_sign_in(self):
        """Right after Claude Code is installed: its own first start, which signs in."""
        term = HOME / ".local/bin/kuhaku-terminal"
        spawn([str(term), "-e", self.claude] if term.exists() else ["x-terminal-emulator", "-e", self.claude])
        return False

    def install_claude_code(self):
        """Anthropic's own installer, into ~/.local/bin; then the sign-in opens."""
        if getattr(self, "installing", False):
            return False
        self.installing = True

        def work():
            r = subprocess.run(["sh", "-c", "curl -fsSL https://claude.ai/install.sh | bash"], capture_output=True)
            self.installing = False
            if r.returncode == 0 and Path(self.claude).exists():
                GLib.idle_add(self.note, "Claude", "Ready. Sign in once.")
                GLib.idle_add(self.first_sign_in)

        threading.Thread(target=work, daemon=True).start()
        return False

    def finish(self, question, result, error, session_id, tab_id):
        tab = self.tabs.get(tab_id)
        entry = {
            "question": question,
            "answer": result or error,
            "error": bool(error and not result),
            "time": int(time.time()),
            "tab": tab_id,
        }
        if tab:
            if session_id:
                tab["session_id"] = session_id
            tab.update(status="idle", answer=entry, unseen=tab_id != self.tabs.current)
            self.tabs.save()
        self.live.pop(tab_id, None)
        if tab_id == self.tabs.current and session_id:
            self.state["session_id"] = session_id
            self.save_state()
        if result and not (tab or {}).get("anon"):
            answers = self.load_answers()
            answers.append(entry)
            ANSWERS_FILE.write_text(json.dumps(answers[-300:]))
            if tab_id == self.tabs.current:
                self.state["dismissed"] = False
                self.save_state()
        self.send("showAnswer", entry)
        if self.convo and tab_id == self.tabs.current:
            threading.Thread(
                target=self.speak_then_listen, args=(entry["answer"] or "", self.convo), daemon=True
            ).start()
        self.schedule()  # a place is free: the next in line starts
        return False

    def load_answers(self):
        try:
            return json.loads(ANSWERS_FILE.read_text())
        except Exception:
            report()
            return []

    def cancel(self, tab_id=None):
        """Stop a tab's job (the open tab's if none is named); a waiting one just leaves the line."""
        tab_id = tab_id or self.tabs.current
        tab = self.tabs.get(tab_id)
        if tab and tab["status"] in ("running", "queued"):
            tab.update(status="idle", note=None, at=None)
            self.tabs.save()
            GLib.idle_add(self.push_tabs)
        self.live.pop(tab_id, None)
        proc = self.procs.pop(tab_id, None)
        if proc:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                report()
