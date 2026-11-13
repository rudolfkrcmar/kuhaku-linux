"""Night or day for Kūhaku's "auto" theme: it follows the sun where the computer is.

The place comes from "latitude" / "longitude" in the config, else from the time zone's
main city (tzdata's zone1970.tab), so no network is needed. Without either, the clock
hours "night_from" / "night_until" decide, as before.
"""

import json
import math
import os
import time
from pathlib import Path

from kuhaku_log import report

CONFIG = Path.home() / ".config" / "kuhaku" / "config.json"
STATE = Path.home() / ".local" / "state" / "kuhaku" / "state.json"
ZONES = Path("/usr/share/zoneinfo/zone1970.tab")


def _zone():
    tz = os.environ.get("TZ", "").lstrip(":")
    if tz:
        return tz
    try:
        link = os.readlink("/etc/localtime")
        return link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else ""
    except OSError:
        try:
            return Path("/etc/timezone").read_text().strip()
        except OSError:
            return ""


def _iso6709(s, deg):
    """'+5005' or '+501400' style: sign, degrees (2 or 3 digits), minutes, maybe seconds."""
    sign = -1 if s[0] == "-" else 1
    d = s[1:]
    whole, rest = d[:deg], d[deg:]
    v = int(whole) + int(rest[:2]) / 60 + (int(rest[2:4]) / 3600 if len(rest) >= 4 else 0)
    return sign * v


def place(cfg=None):
    """(latitude, longitude) in degrees, or None when unknown."""
    cfg = cfg or {}
    if isinstance(cfg.get("latitude"), (int, float)) and isinstance(cfg.get("longitude"), (int, float)):
        return float(cfg["latitude"]), float(cfg["longitude"])
    zone = _zone()
    if not zone:
        return None
    try:
        for line in ZONES.read_text().splitlines():
            parts = line.split("\t")
            if line.startswith("#") or len(parts) < 3 or parts[2] != zone:
                continue
            c = parts[1]
            cut = max(c.rfind("+"), c.rfind("-"))
            return _iso6709(c[:cut], 2), _iso6709(c[cut:], 3)
    except Exception:
        report()
    return None


def sun_altitude(lat, lon, when=None):
    """The sun's height above the horizon in degrees (NOAA's simple formulas, within a minute or so)."""
    t = time.gmtime(when if when is not None else time.time())
    day = t.tm_yday
    hours = t.tm_hour + t.tm_min / 60 + t.tm_sec / 3600
    g = 2 * math.pi / 365 * (day - 1 + (hours - 12) / 24)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(g)
        - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g)
        - 0.040849 * math.sin(2 * g)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(g)
        + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g)
        + 0.00148 * math.sin(3 * g)
    )
    solar_minutes = hours * 60 + eqtime + 4 * lon
    ha = math.radians(solar_minutes / 4 - 180)
    phi = math.radians(lat)
    cos_zenith = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)
    return 90 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zenith))))


def is_night(cfg=None, when=None):
    """Night once the sun has set (its upper edge below the horizon), until it rises."""
    cfg = cfg or {}
    where = place(cfg)
    if where:
        return sun_altitude(where[0], where[1], when) < -0.833
    h = time.localtime(when).tm_hour if when is not None else time.localtime().tm_hour
    return h >= cfg.get("night_from", 20) or h < cfg.get("night_until", 7)


def _load(p):
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        return {}
    except Exception:
        report()
        return {}


def theme():
    """Kūhaku's theme now: the saved choice ("day" / "night"), else the sun."""
    cfg = _load(CONFIG)
    choice = _load(STATE).get("theme") or cfg.get("theme", "auto")
    if choice in ("day", "night"):
        return choice
    return "night" if is_night(cfg) else "day"
