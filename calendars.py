"""Kūhaku calendars: Google Calendar (and any iCal feed) for the date panel.

Each calendar is its "secret address in iCal format" (Google Calendar:
Settings, then the calendar, then Integrate calendar). The feed is downloaded
every 15 minutes and kept on disk, so events show offline too. The secret
address stays in ~/.config/kuhaku/calendars.json, readable only by you.

Reading a big feed takes seconds of Python (a work calendar: 7 MB, ten seconds), and
in Kūhaku's own process that holds the interpreter and freezes the home screen. So a
feed is read once, after each download, by a separate low-priority process
(`calendars.py index`), which unfolds its events from a year back to two years ahead
into a small .json beside it. The panel only ever reads those.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from kuhaku_log import report  # faults go to the log instead of vanishing

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

HOME = Path.home()
CONFIG = HOME / ".config/kuhaku/calendars.json"
CACHE = HOME / ".local/state/kuhaku/calendars"
REFRESH = 15 * 60
BACK, AHEAD = 365, 730  # days of events kept around today
_lock = threading.Lock()


def load():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        report()
        return []


def save(cals):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cals, indent=2))
    os.chmod(CONFIG, 0o600)


def _file(i):
    return CACHE / f"{i}.ics"


def fetch(cal, i):
    url = cal["url"].strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://") :]
    req = urllib.request.Request(url, headers={"User-Agent": "Kuhaku calendar"})
    data = urllib.request.urlopen(req, timeout=30).read()
    if b"BEGIN:VCALENDAR" not in data[:2000]:
        raise ValueError("That address is not a calendar feed")
    CACHE.mkdir(parents=True, exist_ok=True)
    _file(i).write_bytes(data)
    return data


def _index_file(i):
    return CACHE / f"{i}.json"


def name_of(data):
    """The feed's name, read from its header alone (parsing all of a big feed is slow)."""
    head = data[:8000].decode("utf-8", "replace").replace("\r\n ", "").replace("\n ", "")
    m = re.search(r"^X-WR-CALNAME[^:\r\n]*:(.*)$", head, re.M)
    return m.group(1).strip().replace("\\,", ",") if m and m.group(1).strip() else "Calendar"


def stale():
    """Feeds downloaded since their events were last read."""
    out = []
    for i in range(len(load())):
        f, j = _file(i), _index_file(i)
        if f.exists() and (not j.exists() or j.stat().st_mtime < f.stat().st_mtime):
            out.append(i)
    return out


def build(i, around=None):
    """Read feed i and write its events near today to i.json. Slow: run in its own process."""
    import icalendar
    import recurring_ical_events

    around = around or date.today()
    start, end = around - timedelta(days=BACK), around + timedelta(days=AHEAD)
    out = []
    try:
        c = icalendar.Calendar.from_ical(_file(i).read_bytes())
        items = recurring_ical_events.of(c).between(start, end)
    except Exception:
        report()
        items = []
    for ev in items:
        s, e = ev.get("DTSTART"), ev.get("DTEND")
        if not s:
            continue
        s = s.dt
        e = e.dt if e else s
        all_day = not isinstance(s, datetime)
        if not all_day:
            s = s.astimezone() if s.tzinfo else s
            e = e.astimezone() if getattr(e, "tzinfo", None) else e
        out.append(
            {
                "title": str(ev.get("SUMMARY") or "(no title)"),
                "where": str(ev.get("LOCATION") or ""),
                "allDay": all_day,
                "start": s.isoformat(),
                "end": e.isoformat(),
                "day": (s if all_day else s.date()).isoformat(),
            }
        )
    tmp = _index_file(i).with_suffix(".tmp")
    tmp.write_text(json.dumps(out))
    tmp.replace(_index_file(i))


_indexer = None
_index_lock = threading.Lock()


def index():
    """Bring every stale feed's events up to date in a separate, low-priority process, and
    wait for it (waiting holds nothing, so Kūhaku stays responsive). One at a time."""
    global _indexer
    with _index_lock:
        if not stale():
            return
        try:
            cmd = ["nice", "-n", "15", sys.executable, str(Path(__file__).resolve()), "index"]
            _indexer = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
            _indexer.wait(timeout=300)
        except Exception:
            report()
            if _indexer and _indexer.poll() is None:
                _indexer.kill()


def add(url):
    """Connect a calendar by its address; returns its name or raises."""
    cals = load()
    cal = {"url": url.strip()}
    data = fetch(cal, len(cals))
    cal["name"] = name_of(data)
    cals.append(cal)
    save(cals)
    return cal["name"]


def remove(index):
    cals = load()
    if 0 <= index < len(cals):
        cals.pop(index)
        save(cals)
        for f in [*CACHE.glob("*.ics"), *CACHE.glob("*.json")]:
            f.unlink()
        refresh(force=True)


def refresh(force=False):
    with _lock:
        for i, cal in enumerate(load()):
            f = _file(i)
            if force or not f.exists() or time.time() - f.stat().st_mtime > REFRESH:
                try:
                    fetch(cal, i)
                except Exception:
                    report()
                    # offline: the copy on disk stays in use


def events(start, end):
    """Events between two dates, as plain dicts, sorted by time (from the feeds' .json)."""
    lo, hi = start.isoformat(), end.isoformat()
    out = []
    for i, cal in enumerate(load()):
        try:
            items = json.loads(_index_file(i).read_text())
        except FileNotFoundError:
            continue
        except Exception:
            report()
            continue
        for ev in items:
            if lo <= ev["day"] < hi:
                out.append({**ev, "cal": cal.get("name", "Calendar")})
    out.sort(key=lambda x: (x["day"], not x["allDay"], x["start"]))
    return out


def month_view(year, month):
    """Events for the six weeks shown around a month, plus connected calendars."""
    first = date(year, month + 1, 1)
    start = first - timedelta(days=first.weekday() + 7)
    end = start + timedelta(days=56)
    return {
        "events": events(start, end),
        "calendars": [{"name": c.get("name", "Calendar")} for c in load()],
        "range": [start.isoformat(), end.isoformat()],
    }


if __name__ == "__main__" and sys.argv[1:] == ["index"]:
    for n in stale():
        build(n)
