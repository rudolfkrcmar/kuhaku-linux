"""Adding and removing mail accounts, Google sign-in included. A part of the Mail class (tegami_core.py)."""

import hashlib
import imaplib
import ssl
import subprocess
import threading

import desktop
import tegami_google as google
from kuhaku_log import report  # faults go to the log instead of vanishing
from tegami_parts import MailParts
from tegami_store import (
    DATA,
    KEYRING,
    PRESETS,
    guess,
    load_accounts,
    save_accounts,
    store_password,
)


class Accounts(MailParts):
    def add_account(self, d):
        address = d.get("email", "").strip()
        if "@" not in address or not d.get("password"):
            return self.js("accountAdded", {"ok": False, "error": "Write the address and the password"})
        imap_h, imap_p, smtp_h, smtp_p, kind = guess(address)
        # Chosen as Gmail: Google's servers, whatever the address (work Gmail on a company domain).
        if d.get("service") == "gmail":
            imap_h, imap_p, smtp_h, smtp_p, kind = PRESETS["gmail.com"]
        if d.get("imap"):
            imap_h = d["imap"]
        if d.get("smtp"):
            smtp_h = d["smtp"]
        acct = {
            "id": hashlib.sha1(address.lower().encode()).hexdigest()[:10],
            "email": address,
            "name": d.get("name", "").strip(),
            "label": d.get("label", "").strip()[:40],
            "kind": kind,
            "imap_host": imap_h,
            "imap_port": imap_p,
            "smtp_host": smtp_h,
            "smtp_port": smtp_p,
            "user": address,
        }

        def check():
            try:
                M = imaplib.IMAP4_SSL(imap_h, imap_p, ssl_context=ssl.create_default_context(), timeout=20)
                M.login(address, d["password"])
                M.logout()
                return None
            except imaplib.IMAP4.error:
                return (
                    "Gmail needs an app password: Google account, Security, App passwords."
                    if kind == "Gmail"
                    else "The address or password was not accepted"
                )
            except Exception as e:
                report()
                return f"Could not reach {imap_h}: {e}"[:160]

        def done(err):
            if err:
                return self.js("accountAdded", {"ok": False, "error": err})
            store_password(acct["id"], f"Tegami: {address}", d["password"])
            accts = [a for a in load_accounts() if a["id"] != acct["id"]] + [acct]
            save_accounts(accts)
            self.push_accounts()
            self.js("accountAdded", {"ok": True})
            if self.sync:
                self.sync.wake.set()
            return False

        threading.Thread(target=lambda: self.later(done, check()), daemon=True).start()

    def google_sign_in(self, hint=""):
        """Google's own sign-in page in Mado; Tegami keeps only the refresh token, in the keyring."""

        def work():
            try:
                who = google.sign_in(hint)
            except Exception as e:
                err = str(e)
                self.later(
                    lambda: (
                        self.js("googleSetup", {})
                        if err == "no-client"
                        else self.js(
                            "accountAdded",
                            {
                                "ok": False,
                                "error": "Google sign-in did not finish. Try again."
                                if err in ("timeout", "access_denied")
                                else err[:160],
                            },
                        )
                    )
                )
                return
            address = who["email"]
            acct = {
                "id": hashlib.sha1(address.lower().encode()).hexdigest()[:10],
                "email": address,
                "name": who.get("name", ""),
                "kind": "Gmail",
                "auth": "google",
                "imap_host": "imap.gmail.com",
                "imap_port": 993,
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 465,
                "user": address,
            }
            store_password(acct["id"], f"Tegami: {address} (Google)", who["refresh"])
            save_accounts([a for a in load_accounts() if a["id"] != acct["id"]] + [acct])

            def done():
                self.push_accounts()
                self.js("accountAdded", {"ok": True})
                if self.sync:
                    self.sync.wake.set()
                return False

            self.later(done)

        threading.Thread(target=work, daemon=True).start()

    def remove_account(self, acct_id):
        save_accounts([a for a in load_accounts() if a["id"] != acct_id])
        desktop.password_clear(KEYRING, acct_id)
        self.store.q("DELETE FROM msg WHERE acct=?", (acct_id,), commit=True)
        self.store.q("DELETE FROM folders WHERE acct=?", (acct_id,), commit=True)
        subprocess.run(["rm", "-rf", str(DATA / acct_id)])
        self.push_accounts()
        self.js("fromMail", {"kind": "changed", "data": None})
