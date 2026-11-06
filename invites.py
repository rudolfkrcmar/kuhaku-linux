"""Tegami's invitations: meeting invitations (iCalendar inside a letter) read, listed by date, and answered.

An answer is a small iCalendar REPLY mailed to the organizer, the way Google Calendar, Outlook and
Apple Mail answer, so the organizer's calendar shows Yes, Maybe or No."""
import email.utils
import re
import time
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo

ANSWER = {"yes": "ACCEPTED", "maybe": "TENTATIVE", "no": "DECLINED"}
WORD = {"ACCEPTED": "Accepted", "TENTATIVE": "Tentative", "DECLINED": "Declined"}


def unfold(ics):
    return re.sub(r"\r?\n[ \t]", "", ics.replace("\r\n", "\n")).split("\n")


def field(line):
    """'DTSTART;TZID=Europe/Prague:20261104T130000' → ('DTSTART', {'TZID': 'Europe/Prague'}, '20261104T130000')"""
    head, _, value = line.partition(":")
    if head.upper().startswith(("ORGANIZER", "ATTENDEE")) and value.lower().startswith("mailto"):
        pass
    name, *params = head.split(";")
    return name.upper(), {k.upper(): v.strip('"') for k, _, v in (p.partition("=") for p in params)}, value


def when(value, params):
    """A moment as a timestamp, and whether it is a whole day."""
    v = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", v):
        d = datetime.strptime(v[:8], "%Y%m%d")
        return d.timestamp(), True
    fmt = "%Y%m%dT%H%M%S"
    if v.endswith("Z"):
        return datetime.strptime(v[:-1], fmt).replace(tzinfo=timezone.utc).timestamp(), False
    try:
        tz = ZoneInfo(params["TZID"]) if params.get("TZID") else None
    except Exception:
        tz = None
    d = datetime.strptime(v[:15], fmt)
    return (d.replace(tzinfo=tz) if tz else d).timestamp(), False


def text(v):
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def parse(ics, mine):
    """The event of an invitation: method, uid, title, start, end, place, organizer, and the owner's answer."""
    lines = unfold(ics)
    method = next((l.split(":", 1)[1].strip().upper() for l in lines if l.upper().startswith("METHOD:")), "")
    ev, inside = {}, False
    raw = {}
    for l in lines:
        u = l.upper()
        if u == "BEGIN:VEVENT":
            inside = True
            continue
        if u == "END:VEVENT":
            break
        if not inside or ":" not in l:
            continue
        name, params, value = field(l)
        if name in ("UID", "SUMMARY", "LOCATION", "SEQUENCE", "STATUS", "DESCRIPTION", "RRULE"):
            ev[name] = text(value) if name in ("SUMMARY", "LOCATION", "DESCRIPTION") else value.strip()
        elif name in ("DTSTART", "DTEND"):
            ev[name], ev[name + "_DAY"] = when(value, params)
            raw[name] = l
        elif name == "ORGANIZER":
            ev["ORGANIZER"] = re.sub(r"^mailto:", "", value, flags=re.I)
            ev["ORGANIZER_NAME"] = params.get("CN", "")
            raw["ORGANIZER"] = l
        elif name == "ATTENDEE":
            addr = re.sub(r"^mailto:", "", value, flags=re.I).lower()
            if addr in mine:
                ev["MINE"] = params.get("PARTSTAT", "NEEDS-ACTION").upper()
                ev["ME"] = addr
    if not ev.get("UID") or "DTSTART" not in ev:
        return None
    return {"method": method or "REQUEST", "uid": ev["UID"], "title": ev.get("SUMMARY", "(no title)"),
            "start": ev["DTSTART"], "end": ev.get("DTEND", ev["DTSTART"]), "allday": ev.get("DTSTART_DAY", False),
            "place": ev.get("LOCATION", ""), "organizer": ev.get("ORGANIZER", ""), "organizerName": ev.get("ORGANIZER_NAME", ""),
            "status": "CANCELLED" if method == "CANCEL" or ev.get("STATUS", "").upper() == "CANCELLED" else ev.get("MINE", "NEEDS-ACTION"),
            "me": ev.get("ME", ""), "sequence": ev.get("SEQUENCE", "0"), "repeats": bool(ev.get("RRULE")), "raw": raw}


def calendar_of(m):
    """The iCalendar text inside a letter, if it carries one."""
    for part in m.walk():
        if part.get_content_type() in ("text/calendar", "application/ics") or (part.get_filename() or "").lower().endswith(".ics"):
            try:
                c = part.get_content()
                return c.decode("utf-8", "replace") if isinstance(c, bytes) else c
            except Exception:
                data = part.get_payload(decode=True) or b""
                return data.decode("utf-8", "replace")
    return ""


def reply(ev, me, name, answer):
    """The iCalendar REPLY that tells the organizer the owner's answer."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cn = f';CN="{name}"' if name else ""
    lines = ["BEGIN:VCALENDAR", "PRODID:-//Kuhaku//Tegami//EN", "VERSION:2.0", "CALSCALE:GREGORIAN", "METHOD:REPLY", "BEGIN:VEVENT",
             ev["raw"].get("DTSTART", ""), ev["raw"].get("DTEND", ""), f"DTSTAMP:{stamp}", ev["raw"].get("ORGANIZER", ""),
             f"UID:{ev['uid']}", f"ATTENDEE;PARTSTAT={ANSWER[answer]}{cn}:mailto:{me}", f"SEQUENCE:{ev.get('sequence', '0')}",
             f"SUMMARY:{ev['title']}", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(l for l in lines if l) + "\r\n"
