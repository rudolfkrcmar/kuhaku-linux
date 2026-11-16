"""The computer, as Kūhaku's home screen sees it: battery, Wi-Fi, Bluetooth, power, trackpad,
time, displays, apps and windows, and Claude's usage and plan limits.

Plain functions that read and change the system through its own tools (nmcli, bluetoothctl,
xrandr, wmctrl) and Kūhaku's desktop.json. They never touch the window; kuhaku.py shows what they return.
"""

import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_accounts as accounts
from kuhaku_log import report

HERE = Path(__file__).resolve().parent
HOME = Path.home()
STATE_DIR = HOME / ".local" / "state" / "kuhaku"


def pgrep_x(name):
    return subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0


def load_json(path, default):
    try:
        return {**default, **json.loads(path.read_text())}
    except Exception:
        report()
        return dict(default)


def spawn(argv):
    subprocess.Popen(argv, start_new_session=True, cwd=HOME, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def find_battery():
    """The laptop's battery, whatever the firmware calls it (BAT0, CMB1 …)."""
    for p in sorted(Path("/sys/class/power_supply").glob("*")):
        try:
            if (p / "type").read_text().strip() == "Battery" and (
                not (p / "scope").exists() or (p / "scope").read_text().strip() != "Device"
            ):
                return p
        except OSError:
            pass
    return None


def on_mains():
    """True when a charger (mains or USB-C) is plugged in."""
    for p in Path("/sys/class/power_supply").glob("*"):
        try:
            if (p / "type").read_text().strip() != "Battery" and (p / "online").read_text().strip() == "1":
                return True
        except OSError:
            pass
    return False


PROFILE_ON_MAINS = STATE_DIR / "profile-on-mains"  # the power mode to go back to when plugged in


def follow_power(plugged):
    """On battery the computer saves power by itself; plugged in, the mode chosen before comes back.
    A mode picked by hand while on battery stays until the next unplug."""
    try:
        if plugged:
            if PROFILE_ON_MAINS.exists():
                run(["powerprofilesctl", "set", PROFILE_ON_MAINS.read_text().strip() or "balanced"])
                PROFILE_ON_MAINS.unlink()
            return
        code, cur, _ = run(["powerprofilesctl", "get"])
        cur = cur.strip()
        if code or not cur or cur == "power-saver":
            return
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        PROFILE_ON_MAINS.write_text(cur)
        run(["powerprofilesctl", "set", "power-saver"])
    except Exception:
        report()


def read_battery():
    for bat in [b for b in [find_battery()] if b]:
        try:
            # "capacity" is relative to the design size of a worn battery; report
            # charge against what it can hold today, as upower does.
            cap = None
            for now, full in (("energy_now", "energy_full"), ("charge_now", "charge_full")):
                if (bat / now).exists() and (bat / full).exists():
                    cap = round(100 * int((bat / now).read_text()) / max(1, int((bat / full).read_text())))
                    break
            if cap is None:
                cap = int((bat / "capacity").read_text().strip())
            cap = min(100, cap)
            status = (bat / "status").read_text().strip()
            # Plugged in counts too: a battery held at a charge limit says "Not charging".
            return {"level": cap, "charging": status in ("Charging", "Full") or on_mains()}
        except Exception:
            report()
    return None


def read_wifi():
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "TYPE,STATE", "device"], capture_output=True, text=True, timeout=3
        ).stdout
    except Exception:
        report()
        return None
    lines = [l.split(":") for l in out.splitlines()]
    if any(t == "wifi" and s == "connected" for t, s, *_ in lines):
        return "on"
    if any(t == "ethernet" and s == "connected" for t, s, *_ in lines):
        return "wired"
    return "off"


def wifi_signal():
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "IN-USE,SIGNAL", "dev", "wifi", "list", "--rescan", "no"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
        return next((int(l.split(":")[1]) for l in out.splitlines() if l.startswith("*")), 0)
    except Exception:
        report()
        return 0


DEFGUARD_DB = HOME / ".local/share/net.defguard/defguard.db"


def defguard_location():
    """The Defguard location connected now (a connection without an end), or ""."""
    if not DEFGUARD_DB.exists():
        return ""
    import sqlite3

    try:
        with sqlite3.connect(f"file:{DEFGUARD_DB}?mode=ro", uri=True, timeout=1) as db:
            row = db.execute(
                "SELECT l.name FROM connection c JOIN location l ON l.id = c.location_id "
                "WHERE c.end IS NULL ORDER BY c.id DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else ""
    except Exception:
        report()
        return ""


def read_vpn(net=Path("/sys/class/net")):
    """A VPN in use: {"name", "app"} or None. "app" is the desktop file of the app that runs it
    (Defguard), or "" when NetworkManager does (its panel is Wi‑Fi's). Tailscale is not counted:
    it is always on and only reaches your own devices."""
    try:
        wg = [
            d.name
            for d in net.iterdir()
            if d.name.startswith("wg") or "DEVTYPE=wireguard" in (d / "uevent").read_text()
        ]
    except OSError:
        wg = []
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "con", "show", "--active"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
    except Exception:
        report()
        out = ""
    nm = {}
    for line in out.splitlines():
        name, kind, dev = (line.rsplit(":", 2) + ["", ""])[:3]
        if kind in ("vpn", "wireguard"):
            nm[dev or name] = name
    for dev in wg:
        if dev not in nm and Path("/usr/share/applications/defguard-client.desktop").exists():
            return {"name": defguard_location() or "VPN", "app": "defguard-client.desktop"}
    if nm:
        return {"name": next(iter(nm.values())), "app": ""}
    return None


def run(argv, timeout=15):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env={**os.environ, "LC_ALL": "C"})
        return out.returncode, out.stdout, out.stderr
    except Exception as e:
        report()
        return 1, "", str(e)


# --- VPNs, for the Wi-Fi panel ----------------------------------------------
DEFGUARD_APP = "defguard-client.desktop"


def vpn_list():
    """Every VPN this computer knows: NetworkManager's (WireGuard, OpenVPN, a provider's imported
    files), the NordVPN app's, and Defguard's locations. Each {"id", "name", "on", "kind", "note"}."""
    vpns = []
    code, out, _ = run(["nmcli", "-t", "-f", "NAME,UUID,TYPE,ACTIVE", "connection", "show"], timeout=5)
    for line in out.splitlines() if code == 0 else []:
        parts = line.rsplit(":", 3)
        if len(parts) == 4 and parts[2] in ("vpn", "wireguard"):
            name = parts[0].replace("\\:", ":")
            vpns.append({"id": "nm:" + parts[1], "name": name, "on": parts[3] == "yes", "kind": "nm", "note": ""})
    if shutil.which("nordvpn"):
        _, out, _ = run(["nordvpn", "status"], timeout=5)
        on = bool(re.search(r"Status:\s*Connected", out))
        where = re.search(r"Country:\s*(.+)", out)
        vpns.append(
            {
                "id": "nordvpn",
                "name": "NordVPN",
                "on": on,
                "kind": "nordvpn",
                "note": where[1].strip() if on and where else "",
            }
        )
    if DEFGUARD_DB.exists():
        import sqlite3

        try:
            with sqlite3.connect(f"file:{DEFGUARD_DB}?mode=ro", uri=True, timeout=1) as db:
                rows = db.execute("SELECT id, name, location_mfa_mode FROM location ORDER BY name").fetchall()
                live = {r[0] for r in db.execute("SELECT location_id FROM connection WHERE end IS NULL")}
        except Exception:
            report()
            rows, live = [], set()
        up = any(n.startswith("wg") for n in os.listdir("/sys/class/net"))
        for lid, name, mfa in rows:
            vpns.append(
                {
                    "id": f"defguard:{lid}",
                    "name": name,
                    "on": up and lid in live,
                    "kind": "defguard",
                    "note": "Defguard · asks for a code" if (mfa or 0) > 1 else "Defguard",
                }
            )
    return vpns


def vpn_switch(vid, on):
    """Turn one VPN on or off. Defguard is switched in its own app (it keeps the keys and asks for
    the code), so its card opens instead. Returns {"ok", "error"?, "opened"?}."""
    if vid.startswith("defguard:"):
        launch_app(DEFGUARD_APP)
        return {"ok": True, "opened": True}
    if vid.startswith("nm:") and re.fullmatch(r"[0-9a-fA-F-]{36}", vid[3:]):
        code, _, err = run(["nmcli", "connection", "up" if on else "down", "uuid", vid[3:]], timeout=60)
    elif vid == "nordvpn" and shutil.which("nordvpn"):
        code, _, err = run(["nordvpn", "connect" if on else "disconnect"], timeout=60)
    else:
        return {"ok": False, "error": "Not found."}
    if code == 0:
        return {"ok": True}
    secrets = "secrets" in err.lower() or "password" in err.lower()
    return {"ok": False, "error": "It needs a password first." if secrets else "Could not connect."}


# --- Wi-Fi panel ------------------------------------------------------------
def bluetooth_state(scan=False):
    """Bluetooth through bluetoothctl: is there a controller, is it on, and the devices it knows."""
    if not shutil.which("bluetoothctl"):
        return {"present": False}
    _, show, _ = run(["bluetoothctl", "show"], timeout=5)
    if "Controller" not in show:
        return {"present": False}
    on = "Powered: yes" in show
    if on and scan:
        run(["bluetoothctl", "--timeout", "8", "scan", "on"], timeout=12)
    _, listed, _ = run(["bluetoothctl", "devices"], timeout=5)
    devices: list[dict] = []
    for line in listed.splitlines():
        m = re.match(r"Device ([0-9A-F:]{17}) (.+)", line.strip())
        if not m:
            continue
        _, info, _ = run(["bluetoothctl", "info", m.group(1)], timeout=5)
        named = m.group(2) if m.group(2).replace("-", ":") != m.group(1) else ""
        if not named and "Paired: yes" not in info:
            continue  # nameless strangers nearby: not worth a line
        kind = re.search(r"Icon: (\S+)", info)
        devices.append(
            {
                "mac": m.group(1),
                "name": named or m.group(1),
                "paired": "Paired: yes" in info,
                "connected": "Connected: yes" in info,
                "kind": kind.group(1) if kind else "",
            }
        )
    devices.sort(key=lambda d: (not d["connected"], not d["paired"], d["name"].lower()))
    return {"present": True, "on": on, "devices": devices}


def bluetooth_icon_state():
    if not shutil.which("bluetoothctl"):
        return "none"
    _, show, _ = run(["bluetoothctl", "show"], timeout=3)
    if "Controller" not in show:
        return "none"
    if "Powered: yes" not in show:
        return "off"
    _, conn, _ = run(["bluetoothctl", "devices", "Connected"], timeout=3)
    return "connected" if conn.strip() else "on"


def wifi_networks(rescan):
    _, radio, _ = run(["nmcli", "radio", "wifi"])
    if radio.strip() != "enabled":
        return {"enabled": False, "networks": []}
    _, saved, _ = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    known = {l.rsplit(":", 1)[0].replace("\\:", ":") for l in saved.splitlines() if l.endswith("802-11-wireless")}
    _, out, _ = run(
        [
            "nmcli",
            "-t",
            "-e",
            "yes",
            "-f",
            "IN-USE,SSID,SIGNAL,SECURITY,CHAN",
            "device",
            "wifi",
            "list",
            "--rescan",
            "yes" if rescan else "no",
        ],
        timeout=25,
    )
    best: dict = {}
    for line in out.splitlines():
        parts = [p.replace("\x00", ":") for p in line.replace("\\:", "\x00").split(":")]
        if len(parts) < 5 or not parts[1]:
            continue
        inuse, ssid, signal, security, chan = parts[:5]
        net = {
            "ssid": ssid,
            "signal": int(signal or 0),
            "secure": security not in ("", "--"),
            "active": inuse == "*",
            "known": ssid in known,
            "band": "5 GHz" if chan.isdigit() and int(chan) > 14 else "2.4 GHz",
        }
        old = best.get(ssid)
        if not old or net["active"] or (not old["active"] and net["signal"] > old["signal"]):
            best[ssid] = net
    nets = sorted(best.values(), key=lambda n: (not n["active"], -n["signal"]))
    return {"enabled": True, "networks": nets}


def wifi_saved(ssid):
    _, saved, _ = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    return ssid in {
        l.rsplit(":", 1)[0].replace("\\:", ":") for l in saved.splitlines() if l.endswith("802-11-wireless")
    }


def wifi_connect(ssid, password):
    if password and wifi_saved(ssid):
        # A new password for a network already known: change the saved one, never add a second copy.
        run(["nmcli", "connection", "modify", "id", ssid, "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password])
        code, out, err = run(["nmcli", "connection", "up", "id", ssid], timeout=60)
    elif password:
        code, out, err = run(["nmcli", "device", "wifi", "connect", ssid, "password", password], timeout=60)
    else:
        code, out, err = run(["nmcli", "connection", "up", "id", ssid], timeout=60)
        if code:
            code, out, err = run(["nmcli", "device", "wifi", "connect", ssid], timeout=60)
    msg = (err or out).lower()
    if code == 0:
        return {"ok": True}
    if "secrets were required" in msg or "password" in msg or "802-1x" in msg:
        return {"ok": False, "needPassword": True}
    return {"ok": False, "error": "Could not connect."}


# --- battery panel ----------------------------------------------------------
def battery_details():
    _, devs, _ = run(["upower", "-e"])
    path = next((d for d in devs.split() if "/battery_" in d and "DisplayDevice" not in d), None)
    if not path:
        return None
    _, out, _ = run(["upower", "-i", path])
    info: dict = {}
    for line in out.splitlines():
        if ":" in line and not line.startswith("    1"):
            k, v = line.split(":", 1)
            info.setdefault(k.strip(), v.strip())

    def num(key):
        try:
            return float(info.get(key, "").split()[0].replace(",", ".").rstrip("%"))
        except (ValueError, IndexError):
            return None

    def minutes(key):
        v = info.get(key)
        if not v:
            return None
        n, unit = float(v.split()[0].replace(",", ".")), v.split()[1]
        return round(n * (60 if unit.startswith("hour") else 1 / 60 if unit.startswith("second") else 1))

    full, design = num("energy-full"), num("energy-full-design")
    history = []
    model = info.get("model", "")
    for f in Path("/var/lib/upower").glob(f"history-charge-{model}*.dat") if model else []:
        cutoff = time.time() - 86400
        for line in f.read_text().splitlines():
            p = line.split("\t")
            if len(p) == 3 and p[2] != "unknown" and int(p[0]) >= cutoff:
                history.append([int(p[0]), float(p[1].replace(",", "."))])
    history.sort()
    step = max(1, len(history) // 160)
    history = history[::step] + history[-1:] if history else []
    _, profiles, _ = run(["powerprofilesctl", "list"])
    return {
        "level": num("percentage"),
        "state": info.get("state"),
        "toFull": minutes("time to full"),
        "toEmpty": minutes("time to empty"),
        "energy": num("energy"),
        "full": full,
        "design": design,
        "health": round(100 * full / design) if full and design else None,
        "rate": num("energy-rate"),
        "cycles": int(num("charge-cycles") or 0) or None,
        "temperature": num("temperature"),
        "voltage": num("voltage"),
        "name": " ".join(x for x in (info.get("vendor"), model) if x),
        "technology": info.get("technology", "").replace("-", " "),
        "history": history,
        "now": int(time.time()),
        "profiles": [l.strip(" *:") for l in profiles.splitlines() if l.rstrip().endswith(":")],
        "profile": next((l.strip(" *:") for l in profiles.splitlines() if l.startswith("*")), None),
        "limit": charge_limit(),  # what the firmware holds now; kuhaku-powerd.py sets it
        "why": charge_why(),  # why it fills to 100% now (a trip, travelling, the usual time)
        "care": "full" if read_power()["charge"] >= 100 else "once" if read_power()["full_once"] else "care",
    }


# --- Claude usage: tokens per day from Claude Code's transcripts, plan limits --
_token_cache: dict = {}


def daily_tokens():
    """{"YYYY-MM-DD": {"written", "fresh", "cached", "replies"}} in local time."""
    days: dict = {}
    seen: set = set()
    for f in (accounts.config_dir() / "projects").glob("**/*.jsonl"):
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        cached = _token_cache.get(f)
        if not cached or cached[0] != mtime:
            entries = []
            with open(f, errors="ignore") as fh:
                for line in fh:
                    if '"usage"' not in line or '"assistant"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                        m = e["message"]
                        u = m["usage"]
                        day = (
                            datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
                            .astimezone()
                            .strftime("%Y-%m-%d")
                        )
                    except Exception:
                        report()
                        continue
                    entries.append(
                        (
                            (m.get("id"), e.get("requestId")),
                            day,
                            u.get("output_tokens", 0),
                            u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0),
                            u.get("cache_read_input_tokens", 0),
                        )
                    )
            cached = _token_cache[f] = (mtime, entries)
        for key, day, written, fresh, reused in cached[1]:
            if key in seen:
                continue
            seen.add(key)
            d = days.setdefault(day, {"written": 0, "fresh": 0, "cached": 0, "replies": 0})
            d["written"] += written
            d["fresh"] += fresh
            d["cached"] += reused
            d["replies"] += 1
    return days


_limits_cache: list = [0.0, None]  # [when asked, the plan's numbers]
_limits_lock = threading.Lock()
_limits_wait = [0]  # no new asks before this time (after a failure or rate limit)
LIMITS_FILE = STATE_DIR / "plan-limits.json"


def plan_limits():
    """Session and weekly limits of the Claude plan, as Claude Code itself last reported them
    (its rate_limit_event while working). Kūhaku never reads or uses the Claude sign-in."""
    if _limits_cache[1] is None and LIMITS_FILE.exists():
        try:
            _limits_cache[1] = json.loads(LIMITS_FILE.read_text())  # last known numbers after a restart
        except Exception:
            report()
    return _limits_cache[1]


def note_limits(info):
    """Keep the plan limits Claude Code reports in its stream (type "rate_limit_event")."""
    names = {"five_hour": "session", "seven_day": "week", "seven_day_opus": "opus", "seven_day_sonnet": "sonnet"}
    out = dict(_limits_cache[1] or {})
    for key, w in ((info or {}).get("unifiedWindows") or {}).items():
        if key in names and w.get("utilization") is not None:
            at = w.get("resetsAt")
            out[names[key]] = {
                "percent": w["utilization"] * 100 if w["utilization"] <= 1 else w["utilization"],
                "resets": datetime.fromtimestamp(at, UTC).isoformat() if at else None,
            }
    if not out:
        return None
    _limits_cache[:] = [time.time(), out]
    try:
        LIMITS_FILE.write_text(json.dumps(out))
    except OSError:
        pass
    return out


# --- Screens -------------------------------------------------------------------
# Power: screen off, sleep when idle, the lid. Kept in desktop.json; kuhaku-powerd.py applies
# them. One choice applies on battery and plugged in alike.
POWER_KEYS = {"screen_off": 10, "sleep_after": 0, "lid": 1, "charge": 80, "full_once": 0}


def read_power():
    have = read_desktop().get("power") or {}
    out = {}
    for key, default in POWER_KEYS.items():
        try:
            out[key] = int(have[key])
        except (KeyError, TypeError, ValueError):
            out[key] = default
    if out["sleep_after"] <= 14:
        out["sleep_after"] = 0
    return out


def charge_limit():
    """The charge limit the battery's firmware holds now (None when it takes none)."""
    for bat in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        try:
            return int((bat / "charge_control_end_threshold").read_text())
        except (OSError, ValueError):
            pass
    return None


def charge_why():
    try:
        return json.loads((STATE_DIR / "battery" / "why.json").read_text())
    except FileNotFoundError:
        return {}
    except Exception:
        report()
        return {}


def write_power(key, value):
    if key not in POWER_KEYS:
        return
    v = max(0, int(value))
    write_desktop(lambda cfg: cfg.setdefault("power", {}).__setitem__(key, v))


# Text size and pointers live in desktop.json; kuhaku-keys.py applies them live.
DESKTOP = HOME / ".config" / "kuhaku" / "desktop.json"


def read_desktop():
    try:
        return json.loads(DESKTOP.read_text())
    except FileNotFoundError:
        return {}  # kuhaku-keys.py writes it at its first start
    except Exception:
        report()
        return {}


def write_desktop(change):
    cfg = read_desktop()
    change(cfg)
    DESKTOP.parent.mkdir(parents=True, exist_ok=True)
    tmp = DESKTOP.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.replace(DESKTOP)


# Trackpad (the Mac's): libinput settings by their X names.
PAD = "Apple SPI Touchpad"
PAD_KEYS = {
    "tap": ("libinput Tapping Enabled", "int", 0),
    "natural": ("libinput Natural Scrolling Enabled", "int", 0),
    "speed": ("libinput Accel Speed", "double", 0.0),
}


FINGER_ORDER = [
    "right-index-finger",
    "left-index-finger",
    "right-middle-finger",
    "right-thumb",
    "left-thumb",
    "left-middle-finger",
    "right-ring-finger",
    "left-ring-finger",
    "right-little-finger",
    "left-little-finger",
]


def read_unlock():
    """Fingers for the lock screen: the reader and the known fingers."""
    reader, fingers = None, []
    if shutil.which("fprintd-list"):
        try:
            out = subprocess.run(
                ["fprintd-list", pwd.getpwuid(os.getuid()).pw_name], capture_output=True, text=True, timeout=4
            ).stdout
            m = re.search(r"enrolled for (.+?)\.\s*$", out, re.M) or re.search(r" on (.+?) \(", out)
            if "found 0 devices" not in out and ("Using device" in out or m):
                reader = m.group(1).strip() if m else "Fingerprint reader"
            got = set(re.findall(r"#\d+:\s*(\S+)", out))
            fingers = [f for f in FINGER_ORDER if f in got]
        except Exception:
            report()
    return {"reader": reader, "fingers": fingers}


def read_trackpad():
    if "Apple SPI Touchpad" not in Path("/proc/bus/input/devices").read_text():
        return None
    have = (read_desktop().get("pointers") or {}).get(PAD) or {}
    out = {}
    for key, (prop, typ, default) in PAD_KEYS.items():
        try:
            out[key] = round(float(have[prop]), 1) if typ == "double" else int(have[prop])
        except (KeyError, TypeError, ValueError):
            out[key] = default
    return out


def write_trackpad(key, value):
    prop, typ, _ = PAD_KEYS[key]
    val = float(value) if typ == "double" else int(value)
    write_desktop(lambda cfg: cfg.setdefault("pointers", {}).setdefault(PAD, {}).__setitem__(prop, val))


def read_time():
    """The clock: whether it sets itself from the internet, the time zone, whether it is in step."""
    _, out, _ = run(["timedatectl", "show"])
    v = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    return {
        "auto": v.get("NTP") == "yes",
        "synced": v.get("NTPSynchronized") == "yes",
        "can_auto": v.get("CanNTP") == "yes",
        "zone": v.get("Timezone", ""),
        "zones": all_zones(),
        "now": time.strftime("%Y-%m-%dT%H:%M"),
    }


def all_zones():
    """Every place's time zone, from tzdata's own table, so any place can be chosen (none is suggested first)."""
    zones = set()
    try:
        for line in Path("/usr/share/zoneinfo/zone1970.tab").read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if not line.startswith("#") and len(parts) >= 3:
                zones.add(parts[2])
    except OSError:
        report()
    return sorted(zones) + ["UTC"]


def write_time(key, value):
    # timedatectl asks for the password itself (through the password window) when it must.
    if key == "time_auto":
        run(["timedatectl", "set-ntp", "true" if str(value) in ("1", "true", "True") else "false"], timeout=120)
    elif key == "time_zone" and re.fullmatch(r"[A-Za-z_+\-]+(/[A-Za-z0-9_+\-]+)*", str(value)):
        run(["timedatectl", "set-timezone", str(value)], timeout=120)
    elif key == "time_set" and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d", str(value)):
        run(["timedatectl", "set-ntp", "false"], timeout=120)
        run(["timedatectl", "set-time", str(value).replace("T", " ") + ":00"], timeout=120)


def read_displays():
    _, out, _ = run(["xrandr", "--query"])
    outs: list[dict] = []
    cur: dict | None = None
    for line in out.splitlines():
        m = re.match(r"^(\S+) (connected|disconnected)( primary)?\s*(\d+x\d+\+\d+\+\d+)?", line)
        if m:
            cur = None
            if m.group(2) == "connected":
                cur = {
                    "name": m.group(1),
                    "primary": bool(m.group(3)),
                    "active": bool(m.group(4)),
                    "builtin": m.group(1).startswith(("eDP", "LVDS", "DSI")),
                    "modes": [],
                    "current": None,
                    "x": int(m.group(4).split("+")[1]) if m.group(4) else 0,
                }
                outs.append(cur)
            continue
        mm = re.match(r"^\s+(\d+)x(\d+)\s+(.*)", line)
        if cur and mm:
            res = f"{mm.group(1)}x{mm.group(2)}"
            if res not in cur["modes"]:
                cur["modes"].append(res)
            if "*" in mm.group(3):
                cur["current"] = res
    for o in outs:
        # The few sizes worth offering: current, native, and a handful of common ones.
        w0 = int(o["modes"][0].split("x")[0]) if o["modes"] else 0
        keep = []
        for mres in o["modes"]:
            w, h = map(int, mres.split("x"))
            if (
                mres == o["current"]
                or mres == o["modes"][0]
                or (w >= 1280 and abs(w / h - w0 / max(1, int(o["modes"][0].split("x")[1]))) < 0.02)
            ):
                keep.append(mres)
        o["modes"] = keep[:6]
    dpi = read_desktop().get("dpi")
    return {"outputs": outs, "dpi": dpi if isinstance(dpi, int) and dpi > 0 else 96}


def write_display(value):
    v = value or {}
    if v.get("dpi"):
        d = int(v["dpi"])
        write_desktop(lambda cfg: cfg.__setitem__("dpi", d))
        return
    name, mode = v.get("output"), v.get("mode")
    if name and mode:
        run(["xrandr", "--output", name, "--mode", mode])
        run([str(HERE / "kuhaku-display"), "save", name, mode])  # kept for every login
    arrange = v.get("arrange")
    if arrange:
        info = read_displays()["outputs"]
        inner = next((o for o in info if o["builtin"]), info[0] if info else None)
        outer = next((o for o in info if not o["builtin"]), None)
        if not inner or not outer:
            return
        a, b = inner["name"], outer["name"]
        cmds = {
            "right": ["--output", a, "--auto", "--primary", "--output", b, "--auto", "--right-of", a],
            "left": ["--output", a, "--auto", "--primary", "--output", b, "--auto", "--left-of", a],
            "mirror": ["--output", a, "--auto", "--primary", "--output", b, "--auto", "--same-as", a],
            "outer": ["--output", b, "--auto", "--primary", "--output", a, "--off"],
            "inner": ["--output", a, "--auto", "--primary", "--output", b, "--off"],
        }.get(arrange)
        if cmds:
            run(["xrandr", *cmds])


# --- Apps page ----------------------------------------------------------------
def list_apps():
    from gi.repository import Gio

    apps = {}
    for a in Gio.AppInfo.get_all():
        try:
            if not a.should_show() or a.get_id() in ("xfce4-appfinder.desktop", "xfce4-run.desktop"):
                continue
            name = a.get_display_name() or a.get_name()
            if name and name not in apps:
                cats = (a.get_categories() or "") if hasattr(a, "get_categories") else ""
                apps[name] = {
                    "id": a.get_id(),
                    "name": name,
                    "about": a.get_description() or "",
                    "system": "Settings" in cats.split(";") or "System" in cats.split(";"),
                }
        except Exception:
            report()
            continue
    return sorted(apps.values(), key=lambda x: x["name"].lower())


_APP_INDEX = None


def app_index():
    """WM class (lowercase) -> (desktop id, name, icon file uri)."""
    global _APP_INDEX
    if _APP_INDEX is not None:
        return _APP_INDEX
    from gi.repository import Gio

    theme = Gtk.IconTheme.get_default()
    index: dict = {}
    for a in Gio.AppInfo.get_all():
        try:
            icon_uri = ""
            gicon = a.get_icon()
            if gicon:
                info = theme.lookup_by_gicon(gicon, 64, 0)
                if info and info.get_filename():
                    icon_uri = Path(info.get_filename()).as_uri()
            entry = (a.get_id() or "", a.get_name() or "", icon_uri)
            keys = {(a.get_id() or "").removesuffix(".desktop").lower()}
            if isinstance(a, Gio.DesktopAppInfo) and a.get_startup_wm_class():
                keys.add(a.get_startup_wm_class().lower())
            exe = (a.get_executable() or "").split("/")[-1].lower()
            if exe:
                keys.add(exe)
            for k in keys:
                if k:
                    index.setdefault(k, entry)
        except Exception:
            report()
            continue
    # Kūhaku's own apps can also be installed as Flatpaks (space.kuhaku.Tegami), with the same window
    # class. The home screen pins the native ones (tegami.desktop), so an open window keeps that id
    # and lights its pinned button instead of turning up again as an extra app.
    for k, entry in list(index.items()):
        if entry[0].startswith("space.kuhaku."):
            native = index.get(entry[0].removeprefix("space.kuhaku.").removesuffix(".desktop").lower())
            if native and not native[0].startswith("space.kuhaku."):
                index[k] = native
    _APP_INDEX = index
    return index


def open_windows():
    """App windows in the order they were opened, with their app's name and icon."""
    try:
        out = subprocess.run(["wmctrl", "-lx"], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        report()
        return []
    index, wins = app_index(), []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4 or parts[1] == "-1":
            continue  # Kūhaku itself and other desktop or sticky helpers
        xid, wmclass = parts[0], parts[2]
        title = parts[4] if len(parts) > 4 else ""
        inst, _, cls = wmclass.partition(".") if wmclass.count(".") == 1 else (wmclass, "", "")
        candidates = [wmclass.lower(), inst.lower(), cls.lower()]
        # Classes such as "com.anthropic.claude.com.anthropic.Claude" hold dots themselves.
        half = len(wmclass) // 2
        candidates += [wmclass[:half].lower(), wmclass[half + 1 :].lower()]
        # Kūhaku's own apps are Python scripts: "tegami.py" is the app tegami (Mado runs as browser.py).
        own = {"browser": "mado", "terminal": "kuhaku-terminal"}
        candidates += [
            own.get(c.removesuffix(".py"), c.removesuffix(".py")) for c in list(candidates) if c.endswith(".py")
        ]
        app = next((index[c] for c in candidates if c in index), None)
        icon = app[2] if app else ""
        # At night, Kūhaku's own apps show their dark icons (light ones would glare in the dark list).
        dark = HERE / "icons" / "apps-night" / (Path(icon).stem + ".png") if icon else None
        wins.append(
            {
                "xid": xid,
                "app": app[0] if app else "",
                "name": (app[1] if app else "") or title,
                "icon": icon,
                "iconNight": dark.as_uri() if dark and dark.exists() else icon,
                "title": title,
            }
        )
    return wins


def launch_app(app_id):
    from gi.repository import Gio

    try:
        info = Gio.DesktopAppInfo.new(app_id)
        if info:
            info.launch([], None)
    except Exception:
        report()


def short(text, n=140):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# What each Claude Code tool is doing, in plain words (from its input and the file it touches).
TOOL_WORDS = {
    "Bash": lambda i, path: i.get("description") or short(i.get("command")),
    "Read": lambda i, path: f"Reading {path}",
    "Write": lambda i, path: f"Writing {path}",
    "Edit": lambda i, path: f"Editing {path}",
    "Glob": lambda i, path: f"Looking for {i.get('pattern', 'files')}",
    "Grep": lambda i, path: f"Searching for “{short(i.get('pattern'), 60)}”",
    "WebSearch": lambda i, path: f"Searching the web for “{short(i.get('query'), 80)}”",
    "WebFetch": lambda i, path: f"Reading {short(i.get('url'), 80)}",
    "Agent": lambda i, path: i.get("description") or "Asking a helper",
    "TodoWrite": lambda i, path: "Planning the steps",
}


def describe(ev):
    """Plain words for what Claude is doing, from one stream-json event."""
    if ev.get("type") != "assistant":
        return []
    steps = []
    for block in (ev.get("message") or {}).get("content") or []:
        if block.get("type") == "text" and block.get("text", "").strip():
            steps.append({"kind": "thought", "text": short(block["text"], 220)})
        elif block.get("type") == "tool_use":
            name, i = block.get("name", ""), block.get("input") or {}
            path = Path(i.get("file_path") or i.get("path") or "").name
            words = TOOL_WORDS.get(name)
            text = words(i, path) if words else name.replace("mcp__", "").replace("__", " ").replace("_", " ")
            steps.append({"kind": "tool", "text": short(text)})
    return steps


# --- Notifications -------------------------------------------------------------
NOTIFY_LOG = HOME / ".local" / "state" / "kuhaku" / "notices.sqlite"  # kept by kuhaku-notify.py


def notices(clear=False):
    """The notifications that came in (kuhaku-notify.py keeps them), newest first; seen once shown."""
    import sqlite3

    if not NOTIFY_LOG.exists():
        return []
    try:
        db = sqlite3.connect(str(NOTIFY_LOG), timeout=2)
        if clear:
            db.execute("DELETE FROM notifications")
        rows = db.execute(
            "SELECT timestamp, app_name, summary, body, is_read FROM notifications ORDER BY timestamp DESC LIMIT 40"
        ).fetchall()
        db.execute("UPDATE notifications SET is_read = 1 WHERE is_read = 0")
        db.commit()
        db.close()
    except Exception:
        report()
        return []
    # seconds since the epoch (older notices kept microseconds)
    return [
        {
            "t": (t or 0) // 1_000_000 if (t or 0) > 10**12 else (t or 0),
            "app": a or "",
            "title": su or "",
            "body": re.sub(r"<[^>]+>", "", b or "")[:400],
            "new": not r,
        }
        for t, a, su, b, r in rows
    ]
