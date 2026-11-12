"""Kūhaku calendars: Google Calendar (and any iCal feed) for the date panel.

Each calendar is its "secret address in iCal format" (Google Calendar:
Settings, then the calendar, then Integrate calendar). The feed is downloaded
every 15 minutes and kept on disk, so events show offline too. The secret
address stays in ~/.config/kuhaku/calendars.json, readable only by you.
"""

import json
import os
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


def name_of(data):
    import icalendar

    try:
        c = icalendar.Calendar.from_ical(data)
        return str(c.get("X-WR-CALNAME") or "Calendar")
    except Exception:
        report()
        return "Calendar"


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
        for f in CACHE.glob("*.ics"):
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
    """Events between two dates, as plain dicts, sorted by time."""
    import icalendar
    import recurring_ical_events

    out = []
    for i, cal in enumerate(load()):
        f = _file(i)
        if not f.exists():
            continue
        try:
            c = icalendar.Calendar.from_ical(f.read_bytes())
            items = recurring_ical_events.of(c).between(start, end)
        except Exception:
            report()
            continue
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
                    "cal": cal.get("name", "Calendar"),
                }
            )
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
