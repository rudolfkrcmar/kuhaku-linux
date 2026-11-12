"""Calendar invitations found in the mail, and answering them. A part of the Mail class (tegami_core.py)."""

import json
import time
from pathlib import Path

import invites
from tegami_parts import MailParts
from tegami_store import (
    load_accounts,
    parse,
    split_key,
)


class Invitations(MailParts):
    # invitations --------------------------------------------------------------------------
    def _invite_public(self, acct, folder, uid):
        ev = self.invite_of(acct, folder, uid)
        if ev:
            ev = dict(ev)
            ev.pop("raw", None)
        return ev

    def invite_of(self, acct, folder, uid, path=None):
        """The invitation in this letter (parsed once and remembered), or None."""
        r = self.store.q("SELECT ev FROM invites WHERE acct=? AND folder=? AND uid=?", (acct, folder, uid))
        if r:
            return json.loads(r[0][0]) if r[0][0] else None
        if path is None:
            row = self.store.q("SELECT path FROM msg WHERE acct=? AND folder=? AND uid=?", (acct, folder, uid))
            path = row[0][0] if row else None
        ev = None
        try:
            mine = {a["email"].lower() for a in load_accounts()}
            ev = invites.parse(invites.calendar_of(parse(Path(path).read_bytes())), mine) if path else None
        except Exception:
            ev = None
        self.store.q(
            "INSERT OR REPLACE INTO invites VALUES (?,?,?,?,?,?,?)",
            (
                acct,
                folder,
                uid,
                json.dumps(ev) if ev else "",
                ev["method"] if ev else "",
                ev["uid"] if ev else "",
                ev["start"] if ev else 0,
            ),
            commit=True,
        )
        return ev

    def scan_invites(self):
        """Look once into letters not yet looked at for invitations (in the background; slow only the first time)."""
        rows = self.store.q(
            "SELECT m.acct, m.folder, m.uid, m.path FROM msg m LEFT JOIN invites i ON i.acct=m.acct AND i.folder=m.folder AND i.uid=m.uid "
            "WHERE m.folder='inbox' AND i.uid IS NULL AND m.date > ?",
            (time.time() - 90 * 86400,),
        )
        for a, f, u, path in rows:
            self.invite_of(a, f, u, path)
        if rows:
            self.later(lambda: self.js("fromMail", {"kind": "changed", "data": None}) and False)

    def invitations(self, acct="all"):
        """Meeting invitations, one per meeting (its newest letter), with answers others sent to yours."""
        rows = self.store.q(
            "SELECT i.acct, i.folder, i.uid, i.ev, m.sender_name, m.sender, m.date, m.seen FROM invites i JOIN msg m "
            "ON m.acct=i.acct AND m.folder=i.folder AND m.uid=i.uid WHERE i.ev != '' AND m.folder='inbox' ORDER BY m.date DESC"
        )
        meetings: dict = {}
        answers: dict = {}
        for a, f, u, ev, name, sender, date, seen in rows:
            if acct != "all" and a != acct:
                continue
            ev = json.loads(ev)
            if ev["method"] == "REPLY":
                answers.setdefault(ev["uid"], []).append({"who": name or sender, "date": date, "key": f"{a}/{f}/{u}"})
                continue
            if ev["uid"] in meetings:
                continue  # an older letter about the same meeting
            ev.pop("raw", None)
            meetings[ev["uid"]] = {
                **ev,
                "key": f"{a}/{f}/{u}",
                "acct": a,
                "who": name or sender,
                "date": date,
                "seen": seen,
            }
        for uid, m in meetings.items():
            m["answers"] = answers.get(uid, [])[:8]
        return sorted(meetings.values(), key=lambda m: m["start"])

    def answer_invite(self, key, answer):
        acct, folder, uid = split_key(key)
        ev = self.invite_of(acct, folder, int(uid))
        if not ev or answer not in invites.ANSWER:
            return self.js("toast", "This invitation cannot be answered from here")
        a = next((x for x in load_accounts() if x["id"] == acct), None)
        me = ev.get("me") or (a or {}).get("email", "")
        ics = invites.reply(ev, me, (a or {}).get("name", ""), answer)
        word = invites.WORD[invites.ANSWER[answer]]
        self.queue(
            {
                "acct": acct,
                "to": ev["organizer"],
                "subject": f"{word}: {ev['title']}",
                "body": f"{(a or {}).get('name') or me} has {word.lower()} this invitation.",
                "ics": ics,
                "icsMethod": "REPLY",
            }
        )
        ev["status"] = invites.ANSWER[answer]
        self.store.q(
            "UPDATE invites SET ev=? WHERE acct=? AND folder=? AND uid=?",
            (json.dumps(ev), acct, folder, int(uid)),
            commit=True,
        )
        self.ai.done(acct, folder, int(uid))
        self.js("toast", f"{word}. {ev.get('organizerName') or ev['organizer']} will see your answer.")
        self.js("fromMail", {"kind": "changed", "data": None})
