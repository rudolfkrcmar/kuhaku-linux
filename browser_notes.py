"""Mado's comments: notes left on web pages in Developer mode, kept for Claude.

Each note remembers the page, the element (a selector, its text and a bit of its HTML),
where on it the pointer was, the window's size and a picture of the spot. Claude reads them
from NOTES, does what they ask, and marks each one done; Mado notices and the pin goes.

The page layer (browser/notes.js) runs in its own script world with its own web channel,
so the site's scripts never see the notes and cannot write one.
"""

import json
import os
import time
import urllib.parse
import uuid
from pathlib import Path

from PyQt6.QtCore import QFile, QIODevice, QObject, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEngineScript

from kuhaku_log import report  # faults go to the log instead of vanishing

HERE = Path(__file__).resolve().parent
STATE = Path.home() / ".local" / "state" / "kuhaku"
NOTES = STATE / "mado-comments.json"
SHOTS = STATE / "mado-comments"
FIFO = STATE / "kuhaku.fifo"
TABS = STATE / "tabs.json"  # the home screen's jobs, where a handed-over batch is followed
WORLD = int(QWebEngineScript.ScriptWorldId.ApplicationWorld.value)
NOTES_JS = (HERE / "browser" / "notes.js").read_text(encoding="utf-8")


def page_key(url):
    """The page a note belongs to: scheme, host and path, without the query or #part."""
    try:
        u = urllib.parse.urlsplit(url or "")
        return f"{u.scheme}://{u.netloc}{u.path or '/'}"
    except ValueError:
        return url or ""


def site_of(url):
    try:
        u = urllib.parse.urlsplit(url or "")
        return u.netloc or u.scheme + ":" + u.path.rsplit("/", 1)[0]
    except ValueError:
        return ""


class Store:
    """The notes file, read again whenever Claude (or anyone) has changed it."""

    def __init__(self, path=NOTES):
        self.path = path
        self.stamp = None
        self.notes: list[dict] = []
        self.fresh()

    def fresh(self):
        """True when the file changed since it was last read."""
        try:
            st = self.path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            stamp = None
        if stamp == self.stamp:
            return False
        self.stamp = stamp
        try:
            data = json.loads(self.path.read_text()) if stamp else {}
            self.notes = [n for n in data.get("notes", []) if isinstance(n, dict) and n.get("id")]
        except Exception:
            report()  # half-written by someone else: kept as it was, read next time
        return True

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "about": "Comments left in Mado's Developer mode. Do the open ones; then set status "
                    'to "done" and add a short "reply".',
                    "notes": self.notes,
                },
                indent=1,
                ensure_ascii=False,
            )
        )
        os.replace(tmp, self.path)
        st = self.path.stat()
        self.stamp = (st.st_mtime_ns, st.st_size)

    def open_on(self, url):
        key = page_key(url)
        return [n for n in self.notes if n.get("status") == "open" and n.get("page") == key]

    def open_on_site(self, url):
        s = site_of(url)
        return [n for n in self.notes if n.get("status") == "open" and n.get("site") == s]

    def add(self, msg, device):
        url = msg.get("url", "")
        n = {
            "id": uuid.uuid4().hex[:10],
            "status": "open",
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "site": site_of(url),
            "page": page_key(url),
            "url": url,
            "title": str(msg.get("title", ""))[:200],
            "text": str(msg.get("text", ""))[:4000],
            "element": msg.get("element") or {},
            "viewport": {"size": msg.get("viewport"), "scroll": msg.get("scroll"), "device": device},
        }
        self.notes.append(n)
        self.save()
        return n

    def change(self, nid, **what):
        for n in self.notes:
            if n["id"] == nid:
                n.update(what)
                self.save()
                return n
        return None

    def delete(self, nid):
        n = next((n for n in self.notes if n["id"] == nid), None)
        if not n:
            return
        self.notes.remove(n)
        shot = n.get("shot")
        if shot:
            try:
                Path(shot).unlink(missing_ok=True)
            except OSError:
                report()
        self.save()


def hand_off_text(notes, url, how=""):
    """What Claude is asked when the user hands a site's open comments over."""
    site = site_of(url)
    lines = [
        f"I left {len(notes)} comment{'s' if len(notes) != 1 else ''} on {site} in Mado's Developer mode "
        f"({url}). Please do what they ask.",
        "",
        f"They are the open notes with site {site!r} in ~/.local/state/kuhaku/mado-comments.json: each has the "
        "page, the element (selector, text, HTML), where on it I pointed, and a picture of the spot (shot).",
        "Find the project that makes this site (usually in ~/Projects), make the changes, check them, and "
        'commit and push as usual. Then set each note\'s status to "done" with a short "reply". '
        "If one is unclear, leave it open and tell me why.",
        "Answer in a few short sentences: what you changed, and where to see it.",
        "",
    ]
    if how:
        lines += ["How I want it done: " + how, ""]
    for i, n in enumerate(notes, 1):
        el = n.get("element", {})
        lines.append(f"{i}. {n.get('text', '')}  [on {el.get('tag') or 'the whole page'} at {n.get('page', '')}]")
    return "\n".join(lines)


def job(origin):
    """The home screen's job for a handed-over batch: its status, answer and conversation."""
    if not origin:
        return None
    try:
        tabs = json.loads(TABS.read_text()).get("tabs", [])
    except FileNotFoundError:
        return None
    except Exception:
        report()
        return None
    t = next((t for t in tabs if t.get("origin") == origin), None)
    if not t:
        return None
    a = t.get("answer") or {}
    return {
        "status": t.get("status"),
        "answer": a.get("answer") or "",
        "error": bool(a.get("error")),
        "session": t.get("session_id") or "",
    }


def continue_home(session):
    """Bring a finished job forward on the home screen, ready for the next question."""
    return bool(session) and _fifo("continue " + session)


def _fifo(line):
    try:
        fd = os.open(FIFO, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        os.write(fd, (line + "\n").encode())
        return True
    except OSError:
        report()
        return False
    finally:
        os.close(fd)


def send_home(text, origin=""):
    """A new job on the home screen, through its FIFO; False when Kūhaku is not listening.
    With an origin it starts quietly (a tab of its own, the window stays where it is) and
    the asking app follows it in tabs.json."""
    m = {"text": text, "quiet": True, "origin": origin} if origin else {"text": text}
    return _fifo("ask " + json.dumps(m))


def script():
    """The page layer, with Qt's web channel, for the notes' own world."""
    f = QFile(":/qtwebchannel/qwebchannel.js")
    f.open(QIODevice.OpenModeFlag.ReadOnly)
    s = QWebEngineScript()
    s.setName("mado-notes")
    s.setSourceCode(bytes(f.readAll()).decode() + "\n" + NOTES_JS)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    s.setWorldId(WORLD)
    s.setRunsOnSubFrames(False)
    return s


class NotesBridge(QObject):
    def __init__(self, browser, page):
        super().__init__(page)
        self.browser = browser
        self.page = page

    @pyqtSlot(str)
    def post(self, text):
        try:
            self.browser.on_note(self.page, json.loads(text))
        except Exception:
            report()


def attach(browser, page):
    """Give a page its notes channel (in the notes' world only)."""
    ch = QWebChannel(page)
    ch.registerObject("notes", NotesBridge(browser, page))
    page.setWebChannel(ch, WORLD)
    page.notes_channel = ch
