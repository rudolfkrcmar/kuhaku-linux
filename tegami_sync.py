"""Tegami's sync: IMAP in the background (new mail, flags, folders), sending through SMTP, the outbox."""

import base64
import email
import email.policy
import email.utils
import imaplib
import json

import re
import smtplib
import socket
import ssl
import threading
import time
from email.message import EmailMessage
from pathlib import Path
import tegami_google as google  # noqa: E402
from kuhaku_log import report  # faults go to the log instead of vanishing
from tegami_store import (
    DATA,
    KEEP,
    KEEP_SPAM,
    SYNC_EVERY,
    classify,
    correspondents,
    is_automated,
    load_accounts,
    parse,
    password,
    snippet_of,
    when,
)


# --- syncing ----------------------------------------------------------------------------
class Sync:
    def __init__(self, store, notify):
        self.store = store
        self.notify = notify  # callable(kind, payload), from any thread
        self.online = True
        self.busy = False
        self.wake = threading.Event()
        self.after = None  # called after each sync (the assistant reads what came)
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            self.run_once()
            self.wake.wait(SYNC_EVERY)
            self.wake.clear()

    def run_once(self):
        self.busy = True
        self.notify("status", self.status())
        self.backfill()
        ok_any = False
        net_fail = 0
        for a in load_accounts():
            try:
                self.sync_account(a)
                ok_any = True
                a_err = ""
            except (OSError, socket.error, imaplib.IMAP4.abort):
                a_err = "offline"
                net_fail += 1
            except imaplib.IMAP4.error as e:
                a_err = (
                    "sign-in failed"
                    if b"AUTH" in str(e).encode() or "auth" in str(e).lower() or "login" in str(e).lower()
                    else str(e)[:120]
                )
            except Exception as e:
                a_err = str(e)[:120]
            self.notify("account", {"id": a["id"], "error": a_err})
        self.send_outbox()
        # Offline only when no account could be reached at all; other trouble
        # shows next to that account instead.
        self.online = ok_any or net_fail < len(load_accounts()) or not load_accounts()
        self.busy = False
        self.notify("status", self.status())
        self.notify("changed", None)
        if self.after:
            self.after()

    def status(self):
        waiting = self.store.q("SELECT COUNT(*) FROM outbox")[0][0]
        return {"online": self.online, "busy": self.busy, "waiting": waiting}

    def imap(self, a):
        ctx = ssl.create_default_context()
        M = imaplib.IMAP4_SSL(a["imap_host"], int(a.get("imap_port", 993)), ssl_context=ctx, timeout=30)
        if a.get("auth") == "google":
            token = google.access_token(password(a["id"]) or "")
            M.authenticate("XOAUTH2", lambda _: google.xoauth2(a["email"], token).encode())
        else:
            M.login(a.get("user") or a["email"], password(a["id"]) or "")
        return M

    def folders(self, a, M):
        known = dict(self.store.q("SELECT role, name FROM folders WHERE acct=?", (a["id"],)))
        if "sent" in known and "spam" in known:
            if not self.store.q("SELECT 1 FROM allfolders WHERE acct=? LIMIT 1", (a["id"],)):
                self.remember_folders(a, M.list()[1])
            return {r: n for r, n in known.items() if n}
        typ, data = M.list()
        self.remember_folders(a, data)
        spam = ""
        for line in data or []:
            line = line.decode(errors="replace")
            name = line.rsplit(' "', 1)[-1].strip('"') if ' "' in line else line.split()[-1]
            if "\\Junk" in line or name.split("/")[-1].lower() in ("spam", "junk", "junk e-mail", "nevyžádaná pošta"):
                spam = name
                break
        sent = None
        for line in data or []:
            line = line.decode(errors="replace")
            if "\\Sent" in line:
                sent = line.rsplit(' "', 1)[-1].strip('"') if ' "' in line else line.split()[-1]
                break
        if not sent:
            for line in data or []:
                name = line.decode(errors="replace").rsplit(' ', 1)[-1].strip('"')
                if name.lower() in ("sent", "sent items", "sent messages", "odeslaná pošta", "odeslane", "sent mail"):
                    sent = name
                    break
        roles = {"inbox": "INBOX"}
        if sent:
            roles["sent"] = sent
        for r, n in list(roles.items()) + [("spam", spam)]:
            self.store.q("INSERT OR REPLACE INTO folders VALUES (?,?,?)", (a["id"], r, n), commit=True)
        if spam:
            roles["spam"] = spam
        return roles

    def remember_folders(self, a, data):
        """Every folder (Gmail label) of the account, for the side; hidden ones (\\Noselect) left out."""
        names = []
        for line in data or []:
            line = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
            if "\\Noselect" in line:
                continue
            m = re.match(r'\((?P<flags>[^)]*)\) "(?P<sep>[^"]*)" (?P<name>.+)$', line)
            if not m:
                continue
            name = m.group("name").strip().strip('"')
            flags = m.group("flags")
            if "\\Trash" in flags:  # where deleted letters really go (Gmail's own bin, in any language)
                self.store.q("INSERT OR REPLACE INTO folders VALUES (?,?,?)", (a["id"], "trash", name), commit=True)
            if name.upper() == "INBOX" or any(
                f in flags for f in ("\\Sent", "\\Junk", "\\Drafts", "\\Trash", "\\All", "\\Flagged", "\\Important")
            ):
                continue
            names.append(name)
        if names:
            self.store.q("DELETE FROM allfolders WHERE acct=?", (a["id"],), commit=True)
            for n in names:
                self.store.q("INSERT OR IGNORE INTO allfolders VALUES (?,?)", (a["id"], n), commit=True)

    def sync_account(self, a):
        M = self.imap(a)
        try:
            self.apply_ops(a, M)
            try:
                self.push_drafts(a, M)
            except Exception:
                report()
                pass  # drafts stay safe on this computer and go up next time
            for role, name in self.folders(a, M).items():
                self.sync_folder(a, M, role, name)
            # Folders the person has opened in the last two weeks stay in step as well.
            for (name,) in self.store.q(
                "SELECT name FROM watch WHERE acct=? AND at > ?", (a["id"], time.time() - 14 * 86400)
            ):
                try:
                    self.sync_folder(a, M, "f:" + name, name)
                except Exception:
                    report()
        finally:
            try:
                M.logout()
            except Exception:
                report()

    def sync_folder(self, a, M, role, name):
        typ, data = M.select(f'"{name}"', readonly=False)
        if typ != "OK":
            return
        # Only the newest KEEP messages, found by position: asking for every
        # UID of a large mailbox (tens of thousands) is too big an answer.
        total = int((data[0] or b"0").split()[0])
        uids = []
        if total:
            lo = max(1, total - (KEEP_SPAM if role == "spam" else KEEP) + 1)
            typ, resp = M.fetch(f"{lo}:{total}", "(UID)")
            for item in resp or []:
                line = item.decode(errors="replace") if isinstance(item, bytes) else ""
                m_uid = re.search(r"UID (\d+)", line)
                if m_uid:
                    uids.append(int(m_uid.group(1)))
            uids.sort()
        have = {r[0] for r in self.store.q("SELECT uid FROM msg WHERE acct=? AND folder=?", (a["id"], role))}
        new = [u for u in uids if u not in have]
        self.known = correspondents(self.store)
        folder_dir = DATA / a["id"] / role
        folder_dir.mkdir(parents=True, exist_ok=True)
        for i in range(0, len(new), 20):
            chunk = ",".join(str(u) for u in new[i : i + 20])
            typ, resp = M.uid("fetch", chunk, "(UID FLAGS BODY.PEEK[])")
            for item in resp or []:
                if not isinstance(item, tuple):
                    continue
                head = item[0].decode(errors="replace")
                m_uid = re.search(r"UID (\d+)", head)
                if not m_uid:
                    continue
                uid = int(m_uid.group(1))
                raw = item[1]
                path = folder_dir / f"{uid}.eml"
                path.write_bytes(raw)
                m = parse(raw)
                sender = email.utils.parseaddr(str(m.get("from", "")))
                rule = self.store.q("SELECT cat FROM senders WHERE addr=?", (sender[1].lower(),))
                rule = rule[0][0] if rule else None
                self.store.q(
                    "INSERT OR REPLACE INTO msg (acct, folder, uid, msgid, sender, sender_name, recipients, subject, date, "
                    "seen, snippet, path, refs, cat, auto) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        a["id"],
                        role,
                        uid,
                        str(m.get("message-id", "")),
                        sender[1],
                        sender[0] or sender[1],
                        str(m.get("to", "")),
                        str(m.get("subject", "")),
                        when(m),
                        int("\\Seen" in head),
                        snippet_of(m),
                        str(path),
                        str(m.get("references", "")) + " " + str(m.get("in-reply-to", "")),
                        classify(m, sender[1], None, rule, self.known),
                        int(is_automated(m, sender[1])),
                    ),
                    commit=True,
                )
                if role == "inbox" and rule == "spam":
                    self.store.q(
                        "INSERT INTO ops (acct, folder, uid, op) VALUES (?,?,?,?)",
                        (a["id"], role, uid, "spam"),
                        commit=True,
                    )
            self.notify("changed", None)
        # Flags for what we keep; forget what left the server.
        if uids:
            typ, resp = M.uid("fetch", f"{uids[0]}:*", "(UID FLAGS)")
            for item in resp or []:
                line = item.decode(errors="replace") if isinstance(item, bytes) else ""
                m_uid = re.search(r"UID (\d+)", line)
                if m_uid:
                    self.store.q(
                        "UPDATE msg SET seen=? WHERE acct=? AND folder=? AND uid=?",
                        (int("\\Seen" in line), a["id"], role, int(m_uid.group(1))),
                        commit=True,
                    )
        if role == "inbox" and uids and "gmail" in a["imap_host"]:
            self.gmail_categories(a, M, role, uids)
        keep = set(uids)
        for uid, path in self.store.q("SELECT uid, path FROM msg WHERE acct=? AND folder=?", (a["id"], role)):
            if uid not in keep:
                self.store.q("DELETE FROM msg WHERE acct=? AND folder=? AND uid=?", (a["id"], role, uid), commit=True)
                Path(path).unlink(missing_ok=True)

    def gmail_categories(self, a, M, role, uids):
        """Gmail already sorts mail into tabs; use its answer for the letters we keep."""
        new = {}
        for gcat in ("purchases", "promotions", "forums", "updates", "social"):
            try:
                typ, data = M.uid("search", None, f"UID {uids[0]}:*", "X-GM-RAW", f'"category:{gcat}"')
            except Exception:
                report()
                continue
            for u in (data[0] or b"").split() if typ == "OK" else []:
                new.setdefault(int(u), gcat)  # purchases first: it wins over the others
        old = dict(self.store.q("SELECT uid, gcat FROM msg WHERE acct=? AND folder=?", (a["id"], role)))
        rules = dict(self.store.q("SELECT addr, cat FROM senders"))
        # Only letters whose Gmail tab changed are sorted again (reading them is slow here).
        for uid in [u for u in old if old[u] != new.get(u)]:
            row = self.store.q("SELECT sender, path FROM msg WHERE acct=? AND folder=? AND uid=?", (a["id"], role, uid))
            try:
                m = parse(Path(row[0][1]).read_bytes())
            except Exception:
                report()
                continue
            self.store.q(
                "UPDATE msg SET gcat=?, cat=? WHERE acct=? AND folder=? AND uid=?",
                (
                    new.get(uid),
                    classify(m, row[0][0], new.get(uid), rules.get((row[0][0] or "").lower()), self.known),
                    a["id"],
                    role,
                    uid,
                ),
                commit=True,
            )

    def backfill(self):
        """Letters downloaded before Tegami sorted mail get their category once."""
        rules = dict(self.store.q("SELECT addr, cat FROM senders"))
        known = correspondents(self.store)
        for acct, folder, uid, sender, path, gcat in self.store.q(
            "SELECT acct, folder, uid, sender, path, gcat FROM msg WHERE cat IS NULL AND folder != 'sent'"
        ):
            try:
                m = parse(Path(path).read_bytes())
            except Exception:
                report()
                continue
            self.store.q(
                "UPDATE msg SET cat=?, auto=? WHERE acct=? AND folder=? AND uid=?",
                (
                    classify(m, sender, gcat, rules.get((sender or "").lower()), known),
                    int(is_automated(m, sender)),
                    acct,
                    folder,
                    uid,
                ),
                commit=True,
            )

    def drafts_folder(self, a, M):
        """The account's Drafts folder (\\Drafts, or a usual name), remembered."""
        r = self.store.q("SELECT name FROM folders WHERE acct=? AND role='drafts'", (a["id"],))
        if r and r[0][0]:
            return r[0][0]
        name = ""
        for line in M.list()[1] or []:
            line = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
            m = re.match(r'\((?P<flags>[^)]*)\) "(?P<sep>[^"]*)" (?P<name>.+)$', line)
            if not m:
                continue
            n = m.group("name").strip().strip('"')
            if "\\Drafts" in m.group("flags") or n.split("/")[-1].lower() in (
                "drafts",
                "draft",
                "koncepty",
                "rozepsané",
                "concepts",
            ):
                name = n
                break
        if name:
            self.store.q("INSERT OR REPLACE INTO folders VALUES (?,?,?)", (a["id"], "drafts", name), commit=True)
        return name

    def push_drafts(self, a, M):
        """Letters being written go to the server's Drafts too, so they are there in any mail app.
        Each new version replaces the last; a sent or discarded draft is removed there."""
        rows = self.store.q(
            "SELECT id, data, updated, pushed, msgid, gone FROM drafts WHERE acct=? AND (gone=1 OR updated > pushed)",
            (a["id"],),
        )
        if not rows:
            return
        box = self.drafts_folder(a, M)
        if not box or M.select(f'"{box}"')[0] != "OK":
            return
        for did, data, updated, pushed, msgid, gone in rows:
            if msgid:
                typ, found = M.uid("search", None, "HEADER", "Message-ID", f'"{msgid}"')
                for uid in found[0].split() if typ == "OK" and found and found[0] else []:
                    M.uid("store", uid, "+FLAGS", "(\\Deleted)")
                M.expunge()
            if gone:
                self.store.q("DELETE FROM drafts WHERE id=?", (did,), commit=True)
                continue
            d = json.loads(data or "{}")
            msg = EmailMessage()
            msg["From"] = email.utils.formataddr((a.get("name") or "", a["email"]))
            for h, k in (("To", "to"), ("Cc", "cc"), ("Bcc", "bcc"), ("Subject", "subject")):
                if d.get(k):
                    msg[h] = d[k]
            msg["Date"] = email.utils.formatdate(localtime=True)
            new_id = email.utils.make_msgid(domain=a["email"].split("@")[-1])
            msg["Message-ID"] = new_id
            if d.get("inReplyTo"):
                msg["In-Reply-To"] = d["inReplyTo"]
                msg["References"] = (d.get("refs", "") + " " + d["inReplyTo"]).strip()
            msg.set_content(d.get("body", ""))
            typ, _ = M.append(f'"{box}"', "(\\Draft \\Seen)", imaplib.Time2Internaldate(time.time()), bytes(msg))
            if typ == "OK":
                self.store.q(
                    "UPDATE drafts SET pushed=?, msgid=? WHERE id=? AND updated=?",
                    (time.time(), new_id, did, updated),
                    commit=True,
                )

    def apply_ops(self, a, M):
        ops = self.store.q("SELECT id, folder, uid, op FROM ops WHERE acct=?", (a["id"],))
        roles = dict(self.store.q("SELECT role, name FROM folders WHERE acct=?", (a["id"],)))
        for oid, folder, uid, op in ops:
            name = folder[2:] if folder.startswith("f:") else roles.get(folder, "INBOX")
            if M.select(f'"{name}"')[0] == "OK":
                if op == "seen":
                    M.uid("store", str(uid), "+FLAGS", "(\\Seen)")
                elif op == "trash":
                    M.uid("store", str(uid), "+FLAGS", "(\\Deleted)")
                    M.expunge()
                elif op.startswith("move:"):
                    if M.uid("copy", str(uid), f'"{op[5:]}"')[0] == "OK":
                        M.uid("store", str(uid), "+FLAGS", "(\\Deleted)")
                        M.expunge()
                elif op in ("spam", "notspam"):
                    target = roles.get("spam") if op == "spam" else "INBOX"
                    if target and M.uid("copy", str(uid), f'"{target}"')[0] == "OK":
                        M.uid("store", str(uid), "+FLAGS", "(\\Deleted)")
                        M.expunge()
            self.store.q("DELETE FROM ops WHERE id=?", (oid,), commit=True)

    def send_outbox(self):
        accts = {a["id"]: a for a in load_accounts()}
        for oid, acct, path, *_ in self.store.q("SELECT * FROM outbox ORDER BY created"):
            a = accts.get(acct)
            if not a or not Path(path).exists():
                self.store.q("DELETE FROM outbox WHERE id=?", (oid,), commit=True)
                continue
            raw = Path(path).read_bytes()
            try:
                port = int(a.get("smtp_port", 465))
                ctx = ssl.create_default_context()
                if port == 465:
                    S = smtplib.SMTP_SSL(a["smtp_host"], port, context=ctx, timeout=30)
                else:
                    S = smtplib.SMTP(a["smtp_host"], port, timeout=30)
                    S.starttls(context=ctx)
                if a.get("auth") == "google":
                    S.ehlo()
                    raw_auth = google.xoauth2(a["email"], google.access_token(password(a["id"]) or ""))
                    code, resp = S.docmd("AUTH", "XOAUTH2 " + base64.b64encode(raw_auth.encode()).decode())
                    if code != 235:
                        raise smtplib.SMTPAuthenticationError(code, resp)
                else:
                    S.login(a.get("user") or a["email"], password(a["id"]) or "")
                S.send_message(parse(raw))
                S.quit()
            except (OSError, socket.error):
                return  # offline: try again later
            except Exception as e:
                self.store.q("UPDATE outbox SET error=? WHERE id=?", (str(e)[:160], oid), commit=True)
                continue
            # Sent: keep a copy in Sent (Gmail does this by itself).
            if "gmail" not in a["imap_host"]:
                try:
                    M = self.imap(a)
                    roles = self.folders(a, M)
                    if "sent" in roles:
                        M.append(f'"{roles["sent"]}"', "(\\Seen)", imaplib.Time2Internaldate(time.time()), raw)
                    M.logout()
                except Exception:
                    report()
            self.store.q("DELETE FROM outbox WHERE id=?", (oid,), commit=True)
            Path(path).unlink(missing_ok=True)
            self.notify("sent", {"id": oid})
