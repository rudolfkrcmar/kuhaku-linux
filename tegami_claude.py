"""Tegami's assistant at work in the window: asking Claude about the mail, writing for you,
and carrying out what Claude proposes. A part of the Mail class (tegami_core.py)."""

import html
import json
import re
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import claude_accounts as accounts
import keepawake
from kuhaku_log import report  # faults go to the log instead of vanishing
from tegami_parts import MailParts
from tegami_store import (
    DATA,
    HOME,
    load_accounts,
    split_key,
)


class ClaudeHelp(MailParts):
    # Claude --------------------------------------------------------------------------------
    def ask_claude(self, text, key, shown):
        """Claude reads the mail on this computer (read-only) and answers; drafts are only offered, never sent."""
        context = []
        if key:
            m = self.read(key) if not key.startswith("outbox/") else None
            if m:
                body = m["text"] or re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m["html"] or "")))
                context.append(
                    f"The letter open on screen ({key}):\nFrom: {m['from']} <{m['fromAddr']}>\nTo: {m['to']}\n"
                    f"Date: {datetime.fromtimestamp(m['date']).strftime('%a %d %b %Y %H:%M')}\n"
                    f"Subject: {m['subject']}\n\n{body[:8000]}"
                )
        if shown:
            context.append(
                "Letters in the list on screen (newest first):\n"
                + "\n".join(
                    f"- {i.get('key')} | {datetime.fromtimestamp(i.get('date') or 0).strftime('%d %b %H:%M')} | {i.get('who')} <{i.get('addr', '')}> | "
                    f"{i.get('subject')} | {i.get('cat', '')}{'' if i.get('seen') else ' | unread'}"
                    for i in shown[:80]
                )
            )
        accts = ", ".join(a["email"] for a in load_accounts())
        folders_text = (
            "; ".join(
                f"{acct}: " + ", ".join(n for (n,) in self.store.q("SELECT name FROM allfolders WHERE acct=?", (acct,)))
                for (acct,) in self.store.q("SELECT DISTINCT acct FROM allfolders")
            )
            or "none known yet"
        )
        system = (
            "You are Claude inside Tegami, the user's mail app on Kūhaku. Accounts: " + accts + ". "
            "All mail is on this computer under the current folder: <account id>/<inbox|sent|spam>/<uid>.eml "
            "(raw RFC 822; use Grep and Read to search and read them). Today is "
            + datetime.now().strftime("%A %d %B %Y")
            + ". "
            "Reply in plain text, calm and brief: one to four short sentences, no markdown. "
            "If the user wants a reply or a new letter written, put only the letter's text between <draft> and </draft>; "
            "for a new letter add to=\"address\" and subject=\"...\" inside the opening tag. Tegami shows it for the user to send; you cannot send mail. "
            "Categories are important, purchases, updates, newsletters. To sort a sender's letters, add "
            "<sort sender=\"address\" category=\"name\"/> once per sender; Tegami applies it to their letters now and later. "
            "Categories now also include cold (unsolicited generic offers). "
            "You can also propose actions on letters; Tegami shows them and the user confirms them with one click. A letter's key "
            "is <account id>/<folder>/<uid> (its file path without .eml). Use: <archive key=\"...\"/> (out of the inbox), "
            "<trash key=\"...\"/>, <read key=\"...\"/>, <done key=\"...\"/> (no longer needs the user), "
            "<move key=\"...\" folder=\"Folder name\"/>. Folders of each account: " + folders_text + ". "
            "Say in one sentence what you propose; never claim it is done."
        )
        prompt = ("\n\n".join(context) + "\n\nThe user asks: " + text) if context else text
        argv = [
            shutil.which("claude") or str(HOME / ".local/bin/claude"),
            "-p",
            prompt,
            "--output-format",
            "json",
            "--append-system-prompt",
            system,
            "--allowedTools",
            "Read",
            "Grep",
            "Glob",
            "--disallowedTools",
            "Bash",
            "Edit",
            "Write",
            "NotebookEdit",
            "WebFetch",
            "WebSearch",
        ]
        if self.claude_session:
            argv[1:1] = ["--resume", self.claude_session]

        gen = self.claude_gen

        def work():
            try:
                if accounts.kind(accounts.pick(purpose="mail")) != "claude":
                    # An OpenAI or Gemini account: one read-only answer in the mail folder, no session.
                    with keepawake.Awake("Reading your mail"):
                        answer = accounts.ask(prompt, system, purpose="mail", cwd=str(DATA))
                    self.later(done, answer, None)
                    return
                with keepawake.Awake("Claude is reading your mail"):
                    p = self.claude_proc = subprocess.Popen(
                        argv,
                        cwd=str(DATA),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=accounts.env(purpose="mail"),
                    )
                    try:
                        stdout, stderr = p.communicate(timeout=300)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        p.communicate()
                        raise
                out = json.loads(stdout or "{}")
                answer = out.get("result") or stderr.strip()[:300] or "Claude did not answer."
                sid = out.get("session_id")
            except subprocess.TimeoutExpired:
                answer, sid = "Claude took too long. Try a smaller question.", None
            except Exception as e:
                answer, sid = f"Claude could not start: {e}"[:200], None
            self.later(done, answer, sid)

        def done(answer, sid):
            if gen != self.claude_gen:
                return False  # restarted meanwhile: this answer belongs to the old conversation
            if sid:
                self.claude_session = sid
            draft = None
            dm = re.search(r"<draft([^>]*)>(.*?)</draft>", answer, re.S)
            if dm:
                attrs = dict(re.findall(r'(\w+)="([^"]*)"', dm.group(1)))
                draft = {"body": dm.group(2).strip(), "to": attrs.get("to", ""), "subject": attrs.get("subject", "")}
            sorted_n = 0
            for addr, cat in re.findall(r'<sort sender="([^"]+)" category="([^"]+)"\s*/?>', answer):
                sorted_n += self.sort_sender(addr, cat.lower())
            actions = []
            for kind, attrs in re.findall(r"<(archive|trash|read|done|move)\b([^>]*)/?>", answer):
                a = dict(re.findall(r'(\w+)="([^"]*)"', attrs))
                if a.get("key") and (kind != "move" or a.get("folder")):
                    actions.append(
                        {"do": kind, "key": a["key"], "folder": a.get("folder", ""), "label": self.label_of(a["key"])}
                    )
            clean = re.sub(
                r"<draft[^>]*>.*?</draft>|<(sort|archive|trash|read|done|move)\b[^>]*>", "", answer, flags=re.S
            ).strip()
            self.js("claudeSaid", {"text": clean, "draft": draft, "sorted": sorted_n, "actions": actions})
            if sorted_n:
                self.js("fromMail", {"kind": "changed", "data": None})
            return False

        threading.Thread(target=work, daemon=True).start()

    def write_for_me(self, kind, d):
        """Another version of a reply or a new letter, or the owner's own text polished. Shown, never sent."""

        def work():
            try:
                with keepawake.Awake("Writing a letter"):
                    if kind == "polish":
                        out = self.ai.polish(d.get("body", ""), d.get("mode", "minimal"), d.get("wish", ""))
                    elif d.get("key") and not d["key"].startswith("outbox/"):
                        acct, folder, uid = split_key(d["key"])
                        out = self.ai.suggest(acct, folder, int(uid), True, d.get("body", ""), d.get("wish", "")) or {
                            "error": "This letter is gone."
                        }
                    else:
                        out = self.ai.write_new(
                            d.get("to", ""), d.get("subject", ""), d.get("body", ""), d.get("wish", "")
                        )
            except Exception as e:
                out = {"error": str(e)[:160]}
            self.later(lambda: self.js("written", {"kind": kind, "token": d.get("token"), **out}) and False)

        threading.Thread(target=work, daemon=True).start()

    def push_providers(self):
        """The AI accounts mail can use (signed in), and the one it uses now."""
        try:
            chosen = accounts.pick(purpose="mail")
            names = {"claude": "Claude", "codex": "ChatGPT", "gemini": "Gemini", "local": "Local"}
            ready = accounts.ready()
            kinds = [accounts.kind(a) for a in ready]
            out = [
                {
                    "id": a["id"],
                    "kind": accounts.kind(a),
                    "label": names.get(accounts.kind(a), accounts.kind(a))
                    if kinds.count(accounts.kind(a)) == 1
                    else accounts.label(a),
                    "short": names.get(accounts.kind(a), "Claude"),
                    "title": accounts.label(a),
                    "on": a["id"] == chosen.get("id"),
                }
                for a in ready
            ]
            if chosen and not any(x["on"] for x in out):
                out.insert(
                    0,
                    {
                        "id": chosen["id"],
                        "kind": accounts.kind(chosen),
                        "label": names.get(accounts.kind(chosen), "Claude"),
                        "title": accounts.label(chosen),
                        "on": True,
                    },
                )
        except Exception:
            out = [{"id": "", "kind": "claude", "label": "Claude", "title": "Claude", "on": True}]
        self.js("providersAre", out)

    def restart_claude(self):
        """Stop a Claude that is stuck or slow and start a fresh conversation."""
        self.claude_gen += 1
        self.claude_session = None
        p, self.claude_proc = self.claude_proc, None
        if p and p.poll() is None:
            p.kill()

    def label_of(self, key):
        try:
            acct, folder, uid = split_key(key)
            r = self.store.q(
                "SELECT sender_name, subject FROM msg WHERE acct=? AND folder=? AND uid=?", (acct, folder, int(uid))
            )
            return f"{r[0][0]}: {r[0][1]}" if r else key
        except Exception:
            report()
            return key

    def act(self, actions):
        """Do what Claude proposed and the user confirmed."""
        n = 0
        for x in actions:
            try:
                acct, folder, uid = split_key(x.get("key", ""))
                uid = int(uid)
            except Exception:
                report()
                continue
            row = self.store.q("SELECT path FROM msg WHERE acct=? AND folder=? AND uid=?", (acct, folder, uid))
            if not row:
                continue
            do = x.get("do")
            if do == "read":
                self.store.q(
                    "UPDATE msg SET seen=1 WHERE acct=? AND folder=? AND uid=?", (acct, folder, uid), commit=True
                )
                self.store.q(
                    "INSERT INTO ops (acct, folder, uid, op) VALUES (?,?,?,?)", (acct, folder, uid, "seen"), commit=True
                )
            elif do == "done":
                self.ai.done(acct, folder, uid)
            elif do in ("archive", "trash", "move"):
                bin_ = self.store.q("SELECT name FROM folders WHERE acct=? AND role='trash'", (acct,))
                op = (
                    "move:" + x["folder"]
                    if do == "move"
                    else "move:" + bin_[0][0]
                    if do == "trash" and bin_ and bin_[0][0]
                    else "trash"
                )
                self.store.q(
                    "INSERT INTO ops (acct, folder, uid, op) VALUES (?,?,?,?)", (acct, folder, uid, op), commit=True
                )
                self.store.q("DELETE FROM msg WHERE acct=? AND folder=? AND uid=?", (acct, folder, uid), commit=True)
                Path(row[0][0]).unlink(missing_ok=True)
            else:
                continue
            n += 1
        if self.sync:
            self.sync.wake.set()
        self.js("toast", f"Done: {n} letter{'s' if n != 1 else ''}")
        self.js("fromMail", {"kind": "changed", "data": None})
