"""Tegami's ground: mail accounts and their passwords, the local store (SQLite), parsing and sorting messages."""

import email
import email.policy
import email.utils
import html
import imaplib
import json
import os
import sys
import re
import sqlite3
import threading
from pathlib import Path
import time

imaplib._MAXLINE = 50_000_000  # long server answers (big mailboxes) are fine
HERE = Path(__file__).resolve().parent
HOME = Path.home()
CONFIG = HOME / ".config/kuhaku/mail.json"
DATA = HOME / ".local/state/kuhaku/mail"
KEEP = 400  # newest messages kept per folder
SYNC_EVERY = 180  # seconds
KEEP_SPAM = 100
CATS = ("important", "purchases", "updates", "newsletters", "cold")
KEYRING = "app.kuhaku.Tegami"

PRESETS = {
    "gmail.com": ("imap.gmail.com", 993, "smtp.gmail.com", 465, "Gmail"),
    "googlemail.com": ("imap.gmail.com", 993, "smtp.gmail.com", 465, "Gmail"),
    "seznam.cz": ("imap.seznam.cz", 993, "smtp.seznam.cz", 465, "Seznam"),
    "email.cz": ("imap.seznam.cz", 993, "smtp.seznam.cz", 465, "Seznam"),
    "post.cz": ("imap.seznam.cz", 993, "smtp.seznam.cz", 465, "Seznam"),
    "icloud.com": ("imap.mail.me.com", 993, "smtp.mail.me.com", 587, "iCloud"),
    "me.com": ("imap.mail.me.com", 993, "smtp.mail.me.com", 587, "iCloud"),
    "fastmail.com": ("imap.fastmail.com", 993, "smtp.fastmail.com", 465, "Fastmail"),
    "yahoo.com": ("imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 465, "Yahoo"),
}

if sys.platform == "darwin":  # a Mac keeps app data where Macs keep it
    CONFIG = HOME / "Library/Application Support/Tegami/mail.json"
    DATA = HOME / "Library/Application Support/Tegami/data"
if os.environ.get("TEGAMI_TEST_DIR"):  # a sandbox for trying Tegami without real mail
    CONFIG = Path(os.environ["TEGAMI_TEST_DIR"]) / "mail.json"
    DATA = Path(os.environ["TEGAMI_TEST_DIR"]) / "data"

sys.path.insert(0, str(HERE))
import desktop  # noqa: E402
from kuhaku_log import report  # faults go to the log instead of vanishing


# --- accounts ----------------------------------------------------------------------
def load_accounts():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        report()
        return []


def save_accounts(accts):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(accts, indent=2))
    os.chmod(CONFIG, 0o600)


def password(acct_id):
    return desktop.password_lookup(KEYRING, acct_id)


def store_password(acct_id, label, pw):
    desktop.password_store(KEYRING, acct_id, label, pw)


def guess(address):
    domain = address.rsplit("@", 1)[-1].lower()
    if domain in PRESETS:
        return PRESETS[domain]
    return (f"imap.{domain}", 993, f"smtp.{domain}", 465, domain)


# --- the local store ------------------------------------------------------------------
class Store:
    def __init__(self):
        DATA.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(DATA / "mail.sqlite"), check_same_thread=False)
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS msg (
              acct TEXT, folder TEXT, uid INTEGER, msgid TEXT, sender TEXT, sender_name TEXT, recipients TEXT,
              subject TEXT, date REAL, seen INTEGER, snippet TEXT, path TEXT, refs TEXT,
              PRIMARY KEY (acct, folder, uid));
            CREATE INDEX IF NOT EXISTS msg_date ON msg(date);
            CREATE TABLE IF NOT EXISTS ops (id INTEGER PRIMARY KEY, acct TEXT, folder TEXT, uid INTEGER, op TEXT);
            CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, acct TEXT, path TEXT, created REAL,
              recipients TEXT, subject TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS folders (acct TEXT, role TEXT, name TEXT, PRIMARY KEY (acct, role));
            CREATE TABLE IF NOT EXISTS senders (addr TEXT PRIMARY KEY, cat TEXT);
            CREATE TABLE IF NOT EXISTS allfolders (acct TEXT, name TEXT, PRIMARY KEY (acct, name));
            CREATE TABLE IF NOT EXISTS watch (acct TEXT, name TEXT, at REAL, PRIMARY KEY (acct, name));
            CREATE TABLE IF NOT EXISTS invites (acct TEXT, folder TEXT, uid INTEGER, ev TEXT, method TEXT, ev_uid TEXT, start REAL,
              PRIMARY KEY (acct, folder, uid));
            CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, acct TEXT, reply_to TEXT, data TEXT, updated REAL,
              pushed REAL DEFAULT 0, msgid TEXT, gone INTEGER DEFAULT 0);
            """)
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(msg)")}
            for col, kind in (("cat", "TEXT"), ("gcat", "TEXT"), ("auto", "INTEGER")):
                if col not in cols:
                    self.db.execute(f"ALTER TABLE msg ADD COLUMN {col} {kind}")
            self.db.commit()

    def q(self, sql, args=(), commit=False):
        with self.lock:
            cur = self.db.execute(sql, args)
            rows = cur.fetchall()
            if commit:
                self.db.commit()
            return rows


def split_key(key):
    """acct/folder/uid, where a folder (a Gmail label) may itself contain slashes."""
    acct, rest = key.split("/", 1)
    folder, uid = rest.rsplit("/", 1)
    return acct, folder, uid


def parse(raw):
    return email.message_from_bytes(raw, policy=email.policy.default)


def snippet_of(m):
    try:
        part = m.get_body(preferencelist=("plain", "html"))
        text = part.get_content() if part else ""
    except Exception:
        text = ""
    if part is not None and part.get_content_type() == "text/html":
        text = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:180]


PURCHASE = re.compile(
    r"\b(order|receipt|invoice|payment|paid|purchase|shipped|shipping|delivery|delivered|tracking|"
    r"booking|ticket|refund|subscription renewal|objedn\w*|faktur\w*|platb\w*|zásilk\w*|účtenk\w*|"
    r"doklad\w*|rezervac\w*|vstupenk\w*|doručen\w*)\b",
    re.I,
)
AUTOMATED = re.compile(
    r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|notif|alert|mailer|robot|bounce|postmaster|^info@|^news|"
    r"newsletter|^updates?@|^support@|^team@|^hello@|^service|^contact@|^marketing|^promo|^offers?@|"
    r"account|security|billing|statement|communication|^family|^message@|^mail@|^email@|@e\.|@email\.|@mail\.|@news\.)",
    re.I,
)
UPDATE_WORDS = re.compile(
    r"(notif|alert|security|sign-?in|password|verif|statement|account|terms|conditions|"
    r"invitation|reminder|výpis|přihlášení|heslo|upozorněn|podmín)",
    re.I,
)


def correspondents(store):
    """Everyone the user has written to: their letters are important."""
    out = set()
    for (r,) in store.q("SELECT recipients FROM msg WHERE folder='sent'"):
        out.update(a.lower() for _, a in email.utils.getaddresses([r or ""]) if a)
    return out


def is_automated(m, addr):
    """Mail sent by a machine (lists, shops, services) rather than a person."""
    return bool(
        m.get("list-unsubscribe")
        or m.get("list-id")
        or str(m.get("precedence", "")).lower() in ("bulk", "list", "junk")
        or m.get("auto-submitted", "no").lower() != "no"
        or AUTOMATED.search(addr or "")
    )


def classify(m, addr, gcat=None, rule=None, known=frozenset()):
    """One of important / purchases / updates / newsletters. A rule the user taught wins,
    then Gmail's own sorting, then what the letter itself says."""
    if rule in CATS:
        return rule
    subject = str(m.get("subject", ""))
    if gcat == "purchases" or PURCHASE.search(subject):
        return "purchases"
    if gcat in ("promotions", "forums"):
        return "newsletters"
    if gcat in ("updates", "social"):
        return "updates"
    if (addr or "").lower() in known or not is_automated(m, addr):
        return "important"
    if UPDATE_WORDS.search(addr or "") or UPDATE_WORDS.search(subject):
        return "updates"
    if m.get("list-unsubscribe") or str(m.get("precedence", "")).lower() == "bulk":
        return "newsletters"
    return "updates"


def when(m):
    try:
        return email.utils.parsedate_to_datetime(m["date"]).timestamp()
    except Exception:
        report()
        return time.time()
