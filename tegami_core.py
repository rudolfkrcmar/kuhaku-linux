#!/usr/bin/env python3
"""Tegami (手紙, "letter"): Kūhaku's mail. The core: accounts, the local store, syncing,
sorting and the assistant, without a window. Kūhaku's GTK window (tegami.py) and the
Mac app (through tegami_engine.py) both drive it with the same messages.

Mail is downloaded and kept on the computer, so it can be read and answered
offline. Replies wait in the Outbox and are sent by themselves as soon as the
computer is online. Several accounts; Gmail (with an app password), Seznam,
iCloud, Fastmail and any IMAP/SMTP mailbox. Passwords live in the system
keyring, never in a file.

A host gives it the window side: host.js(fn, payload) shows something, host.later(f, *args)
runs f on the host's main thread, host.minimize() and host.quit().
"""

import base64
import email
import email.policy
import email.utils
import hashlib
import html
import json
import os
import re
import threading
import time
from email.message import EmailMessage
from pathlib import Path

import claude_accounts as accounts
import tegami_google as google
from kuhaku_log import report  # faults go to the log instead of vanishing
from tegami_accounts import Accounts
from tegami_ai import Assistant
from tegami_ai_base import threads
from tegami_attachments import Attachments
from tegami_claude import ClaudeHelp
from tegami_invitations import Invitations
from tegami_store import (
    CATS,
    DATA,
    Store,
    correspondents,
    load_accounts,
    parse,
    save_accounts,
    split_key,
    when,
)
from tegami_sync import Sync


# --- the mail, for a window -------------------------------------------------------------------
class Mail(Attachments, Invitations, Accounts, ClaudeHelp):
    """The mail, for a window. Its parts by subject are in tegami_attachments, tegami_invitations,
    tegami_accounts and tegami_claude; what they share is declared in tegami_parts."""

    def __init__(self, host):
        self.host = host
        self.store = Store()
        self.sync = None
        self.claude_session = None
        self.claude_proc = None
        self.claude_gen = 0
        self.ai = Assistant(self.store, load_accounts, self.notify)

    def js(self, fn, payload=None):
        self.host.js(fn, payload)

    def later(self, f, *args):
        """f(*args) on the host's main thread, where messages are handled too."""
        self.host.later(f, *args)

    def notify(self, kind, payload):
        self.later(lambda: self.js("fromMail", {"kind": kind, "data": payload}))

    def ready(self):
        self.push_accounts()
        if os.environ.get("TEGAMI_TEST_DIR"):
            # Sandbox: no network.
            self.ai.wake.set()
            threading.Thread(target=self.scan_invites, daemon=True).start()
            return
        self.sync = Sync(self.store, self.notify)
        threading.Thread(target=self.scan_invites, daemon=True).start()

        def after():
            self.ai.wake.set()
            self.scan_invites()

        self.sync.after = after

    def push_accounts(self):
        self.js(
            "accounts",
            [
                {
                    "id": a["id"],
                    "name": a.get("label") or a.get("name") or a["email"],
                    "email": a["email"],
                    "label": a.get("label", ""),
                    "sender": a.get("name", ""),
                    "signature": a.get("signature", ""),
                    "kind": a.get("kind", ""),
                    "color": a.get("color", ""),
                }
                for a in load_accounts()
            ],
        )

    def images_ok(self, addr):
        """Pictures from the internet: always for people you have written to and senders you allowed;
        otherwise only when asked (they would tell the sender you opened the letter)."""
        addr = (addr or "").lower()
        if not addr:
            return False
        mode = self.store.q("SELECT v FROM kv WHERE k='images:all'")
        if mode and mode[0][0] == "true":
            return True
        if mode and mode[0][0] == "never":
            return False
        if self.store.q("SELECT 1 FROM kv WHERE k=?", ("images:" + addr,)):
            return True
        return addr in correspondents(self.store)

    def update_account(self, d):
        """Settings: the account's own name in Tegami, the name people see, the signature."""
        accts = load_accounts()
        for a in accts:
            if a["id"] == d.get("id"):
                for k_in, k in (("label", "label"), ("sender", "name"), ("signature", "signature"), ("color", "color")):
                    if k_in in d:
                        a[k] = str(d[k_in])[:2000]
        save_accounts(accts)
        self.push_accounts()

    # list and read ------------------------------------------------------------------------
    def counts(self):
        """Unread letters per category, for the side."""
        rows = self.store.q("SELECT cat, COUNT(*) FROM msg WHERE folder='inbox' AND seen=0 GROUP BY cat")
        out: dict = {c: 0 for c in CATS}
        out.update({c: n for c, n in rows if c})
        out["inbox"] = sum(n for _, n in rows)
        out["spam"] = self.store.q("SELECT COUNT(*) FROM msg WHERE folder='spam' AND seen=0")[0][0]
        out["today"] = len(self.ai.today())
        out["important"] = sum(1 for x in self.ai.important() if not x["done"] and not x["seen"])
        out["notifications"] = sum(1 for x in self.ai.notifications() if not x["seen"])
        out["invitations"] = sum(
            1 for x in self.invitations() if x["status"] == "NEEDS-ACTION" and x["start"] > time.time()
        )
        out["byAcct"] = {}
        for acct, folder, n in self.store.q(
            "SELECT acct, folder, COUNT(*) FROM msg WHERE seen=0 AND folder IN ('inbox','spam') GROUP BY acct, folder"
        ):
            out["byAcct"].setdefault(acct, {})[folder] = n
        out["folders"] = {}
        for acct, name in self.store.q("SELECT acct, name FROM allfolders ORDER BY name COLLATE NOCASE"):
            out["folders"].setdefault(acct, []).append(name)
        return out

    def list(self, acct, folder, query, cat=""):
        args, where = [], []
        if folder == "outbox":
            rows = self.store.q(
                "SELECT id, acct, recipients, subject, created, error FROM outbox ORDER BY created DESC"
            )
            return [
                {
                    "key": f"outbox/{r[0]}",
                    "who": "To " + r[2],
                    "subject": r[3],
                    "date": r[4],
                    "seen": 1,
                    "snippet": ("Not sent: " + r[5]) if r[5] else "Waits to be sent",
                    "acct": r[1],
                }
                for r in rows
            ]
        if folder == "today":
            return self.ai.today()
        if folder == "important":
            return self.ai.important()
        if folder == "later":
            return [x for x in self.ai.later_list() if acct == "all" or x["acct"] == acct]
        if folder == "invitations":
            return self.invitations(acct)
        if folder == "drafts":
            out = []
            for did, acct_id, reply_to, data, updated in self.store.q(
                "SELECT id, acct, reply_to, data, updated FROM drafts WHERE gone=0 ORDER BY updated DESC"
            ):
                d = json.loads(data or "{}")
                if acct != "all" and acct_id != acct:
                    continue
                out.append(
                    {
                        "key": "draft/" + did,
                        "who": "To " + (d.get("to") or "nobody yet"),
                        "subject": d.get("subject") or "(no subject)",
                        "date": updated,
                        "seen": 1,
                        "snippet": (d.get("body") or "").strip()[:160],
                        "acct": acct_id,
                        "replyTo": reply_to,
                        "draft": {"id": did, "acct": acct_id, **d},
                    }
                )
            return out
        if folder == "notifications":
            return [x for x in self.ai.notifications() if acct == "all" or x["acct"] == acct]
        if folder == "done":
            return [x for x in self.ai.done_list() if acct == "all" or x["acct"] == acct]
        if folder.startswith("f:") and acct != "all":
            # A folder of the account: kept in step from now on, fetched now if new.
            fresh = not self.store.q("SELECT 1 FROM watch WHERE acct=? AND name=?", (acct, folder[2:]))
            self.store.q("INSERT OR REPLACE INTO watch VALUES (?,?,?)", (acct, folder[2:], time.time()), commit=True)
            if fresh and self.sync:
                self.sync.wake.set()
        where.append("folder=?")
        args.append(folder)
        if cat in CATS:
            where.append("cat=?")
            args.append(cat)
        if acct != "all":
            where.append("acct=?")
            args.append(acct)
        if query:
            where.append("(subject LIKE ? OR sender LIKE ? OR sender_name LIKE ? OR snippet LIKE ?)")
            args += [f"%{query}%"] * 4
        rows = self.store.q(
            f"SELECT m.acct, m.folder, m.uid, sender, sender_name, recipients, subject, date, seen, snippet, cat, "
            f"CASE WHEN t.done=1 THEN '' ELSE t.attention END, m.msgid, m.refs FROM msg m LEFT JOIN triage t "
            f"ON t.acct=m.acct AND t.folder=m.folder AND t.uid=m.uid "
            f"WHERE {' AND '.join('m.' + w if w.startswith(('folder', 'acct')) else w for w in where)} "
            f"ORDER BY date DESC LIMIT 600",
            args,
        )
        out = []
        for r in rows:
            who = (
                r[4] if folder != "sent" else "To " + (email.utils.parseaddr(r[5])[0] or email.utils.parseaddr(r[5])[1])
            )
            out.append(
                {
                    "key": f"{r[0]}/{r[1]}/{r[2]}",
                    "who": who,
                    "subject": r[6] or "(no subject)",
                    "date": r[7],
                    "seen": r[8],
                    "snippet": r[9],
                    "acct": r[0],
                    "addr": r[3],
                    "cat": r[10] or "",
                    "attention": r[11] if r[11] in ("reply", "todo", "read") else "",
                    "msgid": r[12],
                    "refs": r[13],
                }
            )
        threads(out)
        for x in out:
            x.pop("refs", None)
        return out

    def draft_for(self, key):
        """The reply being written to this letter or to one in its conversation, if any."""
        keys = [key] + [x["key"] for x in self.ai.same_thread(key)]
        rows = self.store.q(
            f"SELECT id, acct, reply_to, data FROM drafts WHERE gone=0 AND reply_to IN ({','.join('?' * len(keys))}) "
            "ORDER BY updated DESC LIMIT 1",
            keys,
        )
        if not rows:
            return None
        return {"id": rows[0][0], "acct": rows[0][1], "replyTo": rows[0][2], **json.loads(rows[0][3] or "{}")}

    def contacts(self):
        """Addresses for the To, Cc and Bcc suggestions: people written to count most, then people who wrote
        (machines left out), newest first among equals."""
        own = {a["email"].lower() for a in load_accounts()}
        score: dict = {}
        names: dict = {}
        last: dict = {}
        for rec, date in self.store.q(
            "SELECT recipients, date FROM msg WHERE folder='sent' ORDER BY date DESC LIMIT 3000"
        ):
            for name, addr in email.utils.getaddresses([rec or ""]):
                a = addr.lower()
                if "@" in a and a not in own:
                    score[a] = score.get(a, 0) + 3
                    names.setdefault(a, name)
                    last[a] = max(last.get(a, 0), date)
        for name, addr, date in self.store.q(
            "SELECT sender_name, sender, date FROM msg WHERE folder='inbox' AND COALESCE(auto,0)=0 "
            "ORDER BY date DESC LIMIT 5000"
        ):
            a = (addr or "").lower()
            if "@" in a and a not in own:
                score[a] = score.get(a, 0) + 1
                if name:
                    names.setdefault(a, name)
                last[a] = max(last.get(a, 0), date)
        top = sorted(score, key=lambda a: (-score[a], -last[a]))[:3000]
        return [{"addr": a, "name": names.get(a) or "", "n": score[a]} for a in top]

    def read(self, key):
        cat = folder = ""
        if key.startswith("outbox/"):
            row = self.store.q("SELECT path, acct FROM outbox WHERE id=?", (key.split("/", 1)[1],))
            if not row:
                return None
            path, acct = row[0]
        else:
            acct, folder, uid = split_key(key)
            row = self.store.q(
                "SELECT path, seen, cat FROM msg WHERE acct=? AND folder=? AND uid=?", (acct, folder, int(uid))
            )
            if not row:
                return None
            path, cat = row[0][0], row[0][2]
            if not row[0][1]:
                self.store.q(
                    "UPDATE msg SET seen=1 WHERE acct=? AND folder=? AND uid=?", (acct, folder, int(uid)), commit=True
                )
                self.store.q(
                    "INSERT INTO ops (acct, folder, uid, op) VALUES (?,?,?,?)",
                    (acct, folder, int(uid), "seen"),
                    commit=True,
                )
        m = parse(Path(path).read_bytes())
        body_html, body_text = "", ""
        try:
            hp = m.get_body(preferencelist=("html",))
            body_html = hp.get_content() if hp else ""
            # Pictures that travel inside the letter (cid:) are shown in place, from the letter itself.
            if body_html and "cid:" in body_html:
                for part in m.walk():
                    cid = (part.get("Content-ID") or "").strip().strip("<>")
                    if cid and part.get_content_maintype() == "image":
                        data = part.get_payload(decode=True) or b""
                        uri = f"data:{part.get_content_type()};base64," + base64.b64encode(data).decode()
                        body_html = body_html.replace(f"cid:{cid}", uri)
        except Exception:
            report()
        try:
            tp = m.get_body(preferencelist=("plain",))
            body_text = tp.get_content() if tp else ""
        except Exception:
            report()
        files = []
        for part in m.iter_attachments():
            fn = part.get_filename()
            if fn:
                files.append({"name": fn, "size": len(part.get_payload(decode=True) or b"")})
        sender = email.utils.parseaddr(str(m.get("from", "")))
        return {
            "key": key,
            "acct": acct,
            "from": sender[0] or sender[1],
            "fromAddr": sender[1],
            "to": str(m.get("to", "")),
            "cc": str(m.get("cc", "")),
            "subject": str(m.get("subject", "")),
            "date": when(m),
            "html": body_html,
            "text": body_text,
            "files": files,
            "remoteImages": bool(re.search(r"<img[^>]+src=[\"']?https?://", body_html or "", re.I)),
            "showImages": self.images_ok(sender[1]),
            "msgid": str(m.get("message-id", "")),
            "refs": str(m.get("references", "")),
            "cat": "" if key.startswith("outbox/") else (cat or ""),
            "folder": "" if key.startswith("outbox/") else folder,
            "ai": None if key.startswith("outbox/") else self.ai.info(acct, folder, int(uid)),
            "draft": None if key.startswith("outbox/") else self.draft_for(key),
            "invite": None if key.startswith("outbox/") else self._invite_public(acct, folder, int(uid)),
            "thread": []
            if key.startswith("outbox/")
            else [
                {
                    "key": x["key"],
                    "who": x["who"],
                    "date": x["date"],
                    "subject": x["subject"],
                    "snippet": x["snippet"],
                    "sent": x["folder"] == "sent",
                }
                for x in self.ai.same_thread(key)
                if x["key"] != key
            ],
        }

    def queue(self, d):
        """A reply or a new message goes to the Outbox; the sync sends it when online."""
        accts = {a["id"]: a for a in load_accounts()}
        a = accts.get(d.get("acct")) or (load_accounts() or [None])[0]
        if not a:
            return self.js("toast", "Add a mail account first")
        msg = EmailMessage()
        msg["From"] = email.utils.formataddr((a.get("name") or "", a["email"]))
        msg["To"] = d.get("to", "")
        if d.get("cc"):
            msg["Cc"] = d["cc"]
        if d.get("bcc"):
            msg["Bcc"] = d["bcc"]  # smtplib sends to these and leaves the header out of what goes out
        msg["Subject"] = d.get("subject", "")
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid(domain=a["email"].split("@")[-1])
        if d.get("inReplyTo"):
            self.ai.replied(d["inReplyTo"])
            msg["In-Reply-To"] = d["inReplyTo"]
            msg["References"] = (d.get("refs", "") + " " + d["inReplyTo"]).strip()
        body = d.get("body", "")
        if d.get("quote"):
            body += "\n\n" + d["quote"]
        msg.set_content(body)
        if d.get("ics"):
            # A meeting answer: the calendar part tells the organizer's calendar Yes, Maybe or No.
            msg.add_alternative(d["ics"], subtype="calendar", params={"method": d.get("icsMethod", "REPLY")})
        for f in d.get("files") or []:
            try:
                data = base64.b64decode(f.get("data", ""))
                maintype, _, subtype = (f.get("type") or "application/octet-stream").partition("/")
                msg.add_attachment(
                    data,
                    maintype=maintype or "application",
                    subtype=subtype or "octet-stream",
                    filename=os.path.basename(f.get("name") or "file"),
                )
            except Exception:
                report()
                continue
        oid = hashlib.sha1(str(msg["Message-ID"]).encode()).hexdigest()[:16]
        out = DATA / "outbox"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{oid}.eml"
        path.write_bytes(bytes(msg))
        self.store.q(
            "INSERT INTO outbox VALUES (?,?,?,?,?,?,?)",
            (oid, a["id"], str(path), time.time(), d.get("to", ""), d.get("subject", ""), None),
            commit=True,
        )
        self.js("queued", {"id": oid, "quiet": bool(d.get("ics"))})
        if self.sync:
            self.sync.wake.set()

    # sorting --------------------------------------------------------------------------------
    def sort_sender(self, addr, cat):
        """Teach Tegami: this sender's letters belong in cat, now and from now on."""
        addr = (addr or "").lower()
        if not addr or cat not in CATS:
            return 0
        self.store.q("INSERT OR REPLACE INTO senders VALUES (?,?)", (addr, cat), commit=True)
        n = self.store.q("SELECT COUNT(*) FROM msg WHERE lower(sender)=? AND folder='inbox'", (addr,))[0][0]
        self.store.q("UPDATE msg SET cat=? WHERE lower(sender)=? AND folder='inbox'", (cat, addr), commit=True)
        return n

    def move_spam(self, key, spam):
        acct, folder, uid = split_key(key)
        row = self.store.q("SELECT sender, path FROM msg WHERE acct=? AND folder=? AND uid=?", (acct, folder, int(uid)))
        if not row:
            return
        sender = (row[0][0] or "").lower()
        if spam:
            self.store.q("INSERT OR REPLACE INTO senders VALUES (?,?)", (sender, "spam"), commit=True)
        else:
            self.store.q("DELETE FROM senders WHERE addr=? AND cat='spam'", (sender,), commit=True)
        self.store.q(
            "INSERT INTO ops (acct, folder, uid, op) VALUES (?,?,?,?)",
            (acct, folder, int(uid), "spam" if spam else "notspam"),
            commit=True,
        )
        self.store.q("DELETE FROM msg WHERE acct=? AND folder=? AND uid=?", (acct, folder, int(uid)), commit=True)
        Path(row[0][1]).unlink(missing_ok=True)
        if self.sync:
            self.sync.wake.set()

    def handle(self, msg):
        """One message from the window (the same ones tegami.html posts)."""
        k = msg.get("type")
        if k == "list":
            self.js(
                "listed",
                {
                    "items": self.list(
                        msg.get("acct", "all"), msg.get("folder", "inbox"), msg.get("q", ""), msg.get("cat", "")
                    ),
                    "folder": msg.get("folder"),
                    "acct": msg.get("acct"),
                    "cat": msg.get("cat", ""),
                    "counts": self.counts(),
                },
            )
        elif k == "setcat":
            n = self.sort_sender(msg.get("addr"), msg.get("cat"))
            self.js("toast", f"Letters from {msg.get('addr')} now go to {msg.get('cat').title()}" if n else "Sorted")
            self.js("fromMail", {"kind": "changed", "data": None})
        elif k in ("spam", "notspam") and not msg["key"].startswith("outbox/"):
            self.move_spam(msg["key"], k == "spam")
            self.js(
                "toast",
                "Moved to Spam. Letters from this sender will go there too."
                if k == "spam"
                else "Moved back to the Inbox",
            )
            self.js("fromMail", {"kind": "changed", "data": None})
        elif k == "claude":
            self.ask_claude(msg.get("text", ""), msg.get("key"), msg.get("shown") or [])
        elif k == "suggest" and not msg.get("key", "").startswith("outbox/"):
            acct, folder, uid = split_key(msg["key"])

            def work(key=msg["key"], again=bool(msg.get("again"))):
                try:
                    out = self.ai.suggest(acct, folder, int(uid), again)
                    self.later(
                        lambda: (
                            self.js("suggested", {"key": key, **(out or {"error": "This letter is gone."})}) and False
                        )
                    )
                except Exception as e:
                    err = str(e)[:160]
                    self.later(lambda: self.js("suggested", {"key": key, "error": err}) and False)

            threading.Thread(target=work, daemon=True).start()
        elif k == "done":
            # One letter or a whole conversation; with a note, the note also teaches the assistant.
            keys = [x for x in (msg.get("keys") or [msg.get("key", "")]) if x and not x.startswith("outbox/")]
            note = (msg.get("note") or "").strip()
            for key in keys:
                acct, folder, uid = split_key(key)
                self.ai.done(acct, folder, int(uid), msg.get("done", True), note or None)
            if keys and (note or msg.get("verdict")):
                self.ai.teach(keys[0], msg.get("verdict") or "closed by the owner, nothing more to do", note)
            self.js("fromMail", {"kind": "changed", "data": None})
        elif k == "search":
            q = (msg.get("q") or "").strip()
            self.js(
                "searched",
                {
                    "q": q,
                    "scope": msg.get("scope", "important"),
                    "items": self.ai.search(q, msg.get("scope", "important"), msg.get("acct", "all"))
                    if q or msg.get("scope") == "all"
                    else [],
                },
            )
        elif k == "important":
            keys = [x for x in msg.get("keys") or [] if x and not x.startswith("outbox/")]
            self.ai.important_now(keys)
            if keys:
                self.ai.teach(
                    keys[0],
                    "the owner marked it important: it needs them, put letters like this on Today",
                    msg.get("note", ""),
                )
            self.js("fromMail", {"kind": "changed", "data": None})
        elif k == "check":
            text, token = msg.get("body", ""), msg.get("token")

            def check_work():
                try:
                    out = self.ai.check(text)
                except Exception as e:
                    out = {"notes": [], "error": str(e)[:120]}
                self.later(lambda: self.js("checked", {"token": token, "body": text, **out}) and False)

            threading.Thread(target=check_work, daemon=True).start()
        elif k == "draftSave":
            d = {
                x: msg.get(x, "")
                for x in ("to", "cc", "bcc", "subject", "body", "mode", "inReplyTo", "refs", "quote", "target")
            }
            self.store.q(
                "INSERT INTO drafts (id, acct, reply_to, data, updated) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "acct=excluded.acct, reply_to=excluded.reply_to, data=excluded.data, updated=excluded.updated, gone=0",
                (msg["id"], msg.get("acct", ""), msg.get("replyTo", ""), json.dumps(d), time.time()),
                commit=True,
            )
            if msg.get("leaving") and self.sync:
                self.sync.wake.set()  # going up to the server's Drafts now
        elif k == "draftDelete":
            self.store.q(
                "UPDATE drafts SET gone=1, updated=? WHERE id=?", (time.time(), msg.get("id", "")), commit=True
            )
            self.store.q("DELETE FROM drafts WHERE id=? AND COALESCE(msgid, '')=''", (msg.get("id", ""),), commit=True)
        elif k == "contacts":
            self.js("contactsAre", self.contacts())
        elif k == "pri":
            keys = [x for x in msg.get("keys") or [] if x and not x.startswith("outbox/")]
            self.ai.prioritise(keys, msg.get("how", "normal"))
            if keys and msg.get("how") in ("up", "down"):
                self.ai.teach(
                    keys[0],
                    "the owner moved it to the top: more important than you thought"
                    if msg["how"] == "up"
                    else "the owner moved it to the end: less important than you thought",
                )
            self.js("fromMail", {"kind": "changed", "data": None})
        elif k == "calm":
            self.js("calmList", self.ai.calm())
        elif k == "markRead":
            for key in msg.get("keys") or []:
                try:
                    acct, folder, uid = split_key(key)
                except Exception:
                    report()
                    continue
                self.store.q(
                    "UPDATE msg SET seen=1 WHERE acct=? AND folder=? AND uid=?", (acct, folder, int(uid)), commit=True
                )
                self.store.q(
                    "INSERT INTO ops (acct, folder, uid, op) VALUES (?,?,?,?)",
                    (acct, folder, int(uid), "seen"),
                    commit=True,
                )
            if self.sync:
                self.sync.wake.set()
            self.js("fromMail", {"kind": "changed", "data": None})
        elif k in ("rewrite", "polish"):
            self.write_for_me(k, msg)
        elif k == "providers":
            self.push_providers()
        elif k == "provider":
            accounts.set_for("mail", msg.get("id", ""))
            self.push_providers()
        elif k == "brief":
            self.js("fromMail", {"kind": "assistant", "data": self.ai.brief()})
        elif k == "assistant":
            self.ai.put("off", not msg.get("on"))
            if msg.get("on"):
                self.ai.wake.set()
            self.js("fromMail", {"kind": "assistant", "data": self.ai.brief()})
        elif k == "claudeNew":
            self.claude_session = None
        elif k == "claudeRestart":
            self.restart_claude()
        elif k == "later":
            keys = [x for x in msg.get("keys") or [] if x and not x.startswith("outbox/")]
            self.ai.later(keys, float(msg.get("until") or 0))
            self.js("fromMail", {"kind": "changed", "data": None})
        elif k == "languages":
            if "langs" in msg:
                self.ai.put("langs", [x.strip().lower()[:2] for x in msg["langs"] if x.strip()])
            if "into" in msg:
                self.ai.put("into", msg["into"])
            if "auto" in msg:
                self.ai.put("autoTranslate", bool(msg["auto"]))
            self.js("languagesAre", self.ai.languages())
        elif k == "translate":
            key, target = msg.get("key", ""), msg.get("into") or self.ai.languages()["into"]

            def translate_work():
                try:
                    acct, folder, uid = split_key(key)
                    m = self.read(key) or {}
                    text = m.get("text") or re.sub(
                        r"\s+", " ", html.unescape(re.sub(r"<style[\s\S]*?</style>|<[^>]+>", " ", m.get("html") or ""))
                    )
                    out = self.ai.translate(acct, folder, int(uid), text, target)
                except Exception as e:
                    out = {"error": str(e)[:140]}
                self.later(lambda: self.js("translated", {"key": key, "into": target, **out}) and False)

            threading.Thread(target=translate_work, daemon=True).start()
        elif k == "invitations":
            self.js("invitationsAre", self.invitations(msg.get("acct", "all")))
        elif k == "rsvp":
            self.answer_invite(msg.get("key", ""), msg.get("answer", ""))
        elif k == "openFile":
            self.open_file(msg.get("key", ""), int(msg.get("index", 0)), bool(msg.get("save")))
        elif k == "preview":
            key, index = msg.get("key", ""), int(msg.get("index", 0))
            threading.Thread(target=lambda: self.preview(key, index), daemon=True).start()
        elif k == "peek":
            self.js("peeked", self.read(msg["key"]))
        elif k == "read":
            self.js("opened", self.read(msg["key"]))
        elif k == "send":
            self.queue(msg)
        elif k == "trash" and not msg["key"].startswith("outbox/"):
            acct, folder, uid = split_key(msg["key"])
            path = self.store.q("SELECT path FROM msg WHERE acct=? AND folder=? AND uid=?", (acct, folder, int(uid)))
            self.store.q("DELETE FROM msg WHERE acct=? AND folder=? AND uid=?", (acct, folder, int(uid)), commit=True)
            self.store.q(
                "INSERT INTO ops (acct, folder, uid, op) VALUES (?,?,?,?)",
                (acct, folder, int(uid), "trash"),
                commit=True,
            )
            if path:
                Path(path[0][0]).unlink(missing_ok=True)
            self.js("fromMail", {"kind": "changed", "data": None})
        elif k == "trash":
            oid = msg["key"].split("/", 1)[1]
            for (p,) in self.store.q("SELECT path FROM outbox WHERE id=?", (oid,)):
                Path(p).unlink(missing_ok=True)
            self.store.q("DELETE FROM outbox WHERE id=?", (oid,), commit=True)
            self.js("fromMail", {"kind": "changed", "data": None})
        elif k == "refresh" and self.sync:
            self.sync.wake.set()
        elif k == "google":
            self.google_sign_in(msg.get("hint", ""))
        elif k == "googleClient":
            self.js("googleReady", bool(google.client()))
        elif k == "imagesAll":
            mode = msg.get("mode") or ("true" if msg.get("on") else "false")
            self.store.q(
                "INSERT OR REPLACE INTO kv VALUES ('images:all', ?)",
                (mode if mode in ("true", "false", "never") else "false",),
                commit=True,
            )
        elif k == "imagesAll?":
            r = self.store.q("SELECT v FROM kv WHERE k='images:all'")
            self.js("imagesAllIs", r[0][0] if r else "false")
        elif k == "imagesAlways":
            self.store.q(
                "INSERT OR REPLACE INTO kv VALUES (?,?)",
                ("images:" + (msg.get("addr") or "").lower(), "true"),
                commit=True,
            )
        elif k == "act":
            self.act(msg.get("actions") or [])
        elif k == "updateAccount":
            self.update_account(msg)
        elif k == "addAccount":
            self.add_account(msg)
        elif k == "removeAccount":
            self.remove_account(msg["id"])
        elif k == "status" and self.sync:
            self.js("fromMail", {"kind": "status", "data": self.sync.status()})
        elif k == "minimize":
            self.host.minimize()
        elif k == "quit":
            self.host.quit()
