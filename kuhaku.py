#!/usr/bin/env python3
"""Kūhaku (空白, "blank space"): a full screen home for Claude. 

A WebKit window that replaces the desktop. Questions go to Claude Code
(`claude -p`) in the background; only the pulsing dot and the final answer
are shown. Everything Claude did is logged and can be opened in a real
terminal with Ctrl+` (or Ctrl+Alt+Return).
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import re
import time
from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GLib, Gtk, WebKit2

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_accounts as accounts  # noqa: E402
import keepawake  # noqa: E402
import jobs  # noqa: E402

HERE = Path(__file__).resolve().parent
HOME = Path.home()
CONFIG_DIR = HOME / ".config" / "kuhaku"
STATE_DIR = HOME / ".local" / "state" / "kuhaku"
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_FILE = STATE_DIR / "state.json"
LOG_FILE = STATE_DIR / "claude.log"
DICTATION_FILE = STATE_DIR / "dictation.wav"
ANSWERS_FILE = STATE_DIR / "answers.json"
FIFO = STATE_DIR / "kuhaku.fifo"

DEFAULT_CONFIG = {
    # "auto" follows the clock (night from 20:00 to 07:00), or "day" / "night".
    "theme": "auto",
    "night_from": 20,
    "night_until": 7,
    # How much Claude may do without asking. Kūhaku has no way to show a
    # permission prompt, so tools must be pre-approved here. The installer lets
    # people choose: this (recommended), or "acceptEdits" without Bash (careful).
    "permission_mode": "bypassPermissions",
    "allowed_tools": ["Bash", "Read", "Edit", "Write", "Glob", "Grep", "WebFetch", "WebSearch"],
    "model": None,
    "terminal": "xfce4-terminal",
    # Jobs that may run at once (memory and the Claude plan may allow fewer).
    "max_jobs": 3,
}

SYSTEM_PROMPT = """You are running inside Kūhaku (空白), the full screen home screen of the user's computer (Kūhaku runs on a Linux Mint base, X11). \
The user sees no terminal: only a pulsing dot while you work, then your final reply in large, light type.
- Reply in plain text. No markdown, no bullet symbols, no code blocks, no headings.
- Be calm and brief: usually one to three short sentences saying what you did or the answer. Give more only when asked.
- Do the work instead of explaining how. Mention file paths or commands only if the user asks.
- Start graphical apps detached so they outlive you. Open web pages in Mado, the user's calm Kūhaku browser: `setsid -f mado "https://example.com" >/dev/null 2>&1`.
- Change settings with tools such as pactl, nmcli, xfconf-query, xrandr, brightnessctl.
- Before anything destructive or irreversible (deleting files, uninstalling, sudo), stop and ask the user first in one short sentence."""

MENU = {
    "apps":     ["xfce4-appfinder"],
    "browser":  [str(HOME / ".local/bin/mado")],
    "files":    [str(HOME / ".local/bin/miru")],
    "terminal": None,  # handled specially
    "settings": ["xfce4-settings-manager"],
    "network":  ["nm-connection-editor"],
    "display":  ["xfce4-display-settings"],
    "power":    ["xfce4-power-manager-settings"],
    "lock":     ["xflock4"],
    "logout":   ["xfce4-session-logout", "--logout"],
}


def pgrep_x(name):
    return subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0


def load_json(path, default):
    try:
        return {**default, **json.loads(path.read_text())}
    except Exception:
        return dict(default)


def spawn(argv):
    subprocess.Popen(argv, start_new_session=True, cwd=HOME,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def read_battery():
    for bat in sorted(Path("/sys/class/power_supply").glob("BAT*")):
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
            return {"level": cap, "charging": status in ("Charging", "Full")}
        except Exception:
            pass
    return None


def read_wifi():
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "TYPE,STATE", "device"],
                             capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return None
    lines = [l.split(":") for l in out.splitlines()]
    if any(t == "wifi" and s == "connected" for t, s, *_ in lines):
        return "on"
    if any(t == "ethernet" and s == "connected" for t, s, *_ in lines):
        return "wired"
    return "off"


def wifi_signal():
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "IN-USE,SIGNAL", "dev", "wifi", "list", "--rescan", "no"],
                             capture_output=True, text=True, timeout=3).stdout
        return next((int(l.split(":")[1]) for l in out.splitlines() if l.startswith("*")), 0)
    except Exception:
        return 0


def run(argv, timeout=15):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                             env={**os.environ, "LC_ALL": "C"})
        return out.returncode, out.stdout, out.stderr
    except Exception as e:
        return 1, "", str(e)


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
    devices = []
    for line in listed.splitlines():
        m = re.match(r"Device ([0-9A-F:]{17}) (.+)", line.strip())
        if not m:
            continue
        _, info, _ = run(["bluetoothctl", "info", m.group(1)], timeout=5)
        named = m.group(2) if m.group(2).replace("-", ":") != m.group(1) else ""
        if not named and "Paired: yes" not in info:
            continue            # nameless strangers nearby: not worth a line
        kind = re.search(r"Icon: (\S+)", info)
        devices.append({"mac": m.group(1), "name": named or m.group(1), "paired": "Paired: yes" in info,
                        "connected": "Connected: yes" in info, "kind": kind.group(1) if kind else ""})
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
    _, out, _ = run(["nmcli", "-t", "-e", "yes", "-f", "IN-USE,SSID,SIGNAL,SECURITY,CHAN",
                     "device", "wifi", "list", "--rescan", "yes" if rescan else "no"], timeout=25)
    best = {}
    for line in out.splitlines():
        parts = [p.replace("\x00", ":") for p in line.replace("\\:", "\x00").split(":")]
        if len(parts) < 5 or not parts[1]:
            continue
        inuse, ssid, signal, security, chan = parts[:5]
        net = {"ssid": ssid, "signal": int(signal or 0), "secure": security not in ("", "--"),
               "active": inuse == "*", "known": ssid in known,
               "band": "5 GHz" if chan.isdigit() and int(chan) > 14 else "2.4 GHz"}
        old = best.get(ssid)
        if not old or net["active"] or (not old["active"] and net["signal"] > old["signal"]):
            best[ssid] = net
    nets = sorted(best.values(), key=lambda n: (not n["active"], -n["signal"]))
    return {"enabled": True, "networks": nets}


def wifi_saved(ssid):
    _, saved, _ = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    return ssid in {l.rsplit(":", 1)[0].replace("\\:", ":") for l in saved.splitlines() if l.endswith("802-11-wireless")}


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
    path = next((d for d in devs.split() if "battery_BAT" in d), None)
    if not path:
        return None
    _, out, _ = run(["upower", "-i", path])
    info = {}
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
        "level": num("percentage"), "state": info.get("state"),
        "toFull": minutes("time to full"), "toEmpty": minutes("time to empty"),
        "energy": num("energy"), "full": full, "design": design,
        "health": round(100 * full / design) if full and design else None,
        "rate": num("energy-rate"), "cycles": int(num("charge-cycles") or 0) or None,
        "temperature": num("temperature"), "voltage": num("voltage"),
        "name": " ".join(x for x in (info.get("vendor"), model) if x),
        "technology": info.get("technology", "").replace("-", " "),
        "history": history, "now": int(time.time()),
        "profiles": [l.strip(" *:") for l in profiles.splitlines() if l.rstrip().endswith(":")],
        "profile": next((l.strip(" *:") for l in profiles.splitlines() if l.startswith("*")), None),
    }


# --- Claude usage: tokens per day from Claude Code's transcripts, plan limits --
_token_cache = {}


def daily_tokens():
    """{"YYYY-MM-DD": {"written", "fresh", "cached", "replies"}} in local time."""
    days, seen = {}, set()
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
                        day = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
                    except Exception:
                        continue
                    entries.append(((m.get("id"), e.get("requestId")), day, u.get("output_tokens", 0),
                                    u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0),
                                    u.get("cache_read_input_tokens", 0)))
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


_limits_cache = [0, None]
_limits_lock = threading.Lock()
_limits_wait = [0]   # no new asks before this time (after a failure or rate limit)
LIMITS_FILE = STATE_DIR / "plan-limits.json"


def plan_limits():
    """Session and weekly limits of the Claude plan, as Claude Code's /usage shows them."""
    if _limits_cache[1] is None and LIMITS_FILE.exists():
        try:
            _limits_cache[1] = json.loads(LIMITS_FILE.read_text())   # last known numbers after a restart
        except Exception:
            pass
    if time.time() - _limits_cache[0] < 120 or time.time() < _limits_wait[0]:
        return _limits_cache[1]
    if not _limits_lock.acquire(blocking=False):   # one ask at a time
        return _limits_cache[1]
    import urllib.request
    import urllib.error
    try:
        token = json.loads((accounts.config_dir() / ".credentials.json").read_text())["claudeAiOauth"]["accessToken"]
        req = urllib.request.Request("https://api.anthropic.com/api/oauth/usage", headers={
            "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"})
        data = json.loads(urllib.request.urlopen(req, timeout=8).read())
    except Exception as e:
        # The endpoint rate-limits hard; back off instead of asking again on every look.
        wait = 300
        if isinstance(e, urllib.error.HTTPError) and e.code == 429:
            try:
                wait = max(wait, int(e.headers.get("retry-after") or 0))
            except ValueError:
                pass
        _limits_wait[0] = time.time() + wait
        return _limits_cache[1]
    finally:
        _limits_lock.release()
    out = {}
    for key, name in (("five_hour", "session"), ("seven_day", "week"), ("seven_day_opus", "opus"),
                      ("seven_day_sonnet", "sonnet")):
        v = data.get(key)
        if v and v.get("utilization") is not None:
            out[name] = {"percent": v["utilization"], "resets": v.get("resets_at")}
    extra = data.get("extra_usage") or {}
    if extra.get("is_enabled"):
        out["extra"] = {"percent": extra.get("utilization"), "used": extra.get("used_credits"),
                        "limit": extra.get("monthly_limit")}
    _limits_cache[:] = [time.time(), out]
    try:
        LIMITS_FILE.write_text(json.dumps(out))
    except OSError:
        pass
    return out


# --- Screens -------------------------------------------------------------------
# Power: screen off, sleep when idle, the lid. Kept by xfce4-power-manager (running
# headless in the session); one choice applies on battery and plugged in alike.
XFPM = "/xfce4-power-manager/"
POWER_KEYS = {
    "screen_off": ("dpms-on-battery-off", 10),
    "sleep_after": ("inactivity-on-battery", 0),
    "lid": ("lid-action-on-battery", 1),
}


def read_power():
    out = {}
    for key, (prop, default) in POWER_KEYS.items():
        _, v, _ = run(["xfconf-query", "-c", "xfce4-power-manager", "-p", XFPM + prop])
        try:
            out[key] = int(v.strip())
        except ValueError:
            out[key] = default
    if out["sleep_after"] <= 14:
        out["sleep_after"] = 0
    return out


def write_power(key, value):
    v = max(0, int(value))
    props = {
        "screen_off": [("dpms-enabled", "bool", "true" if v else "false"),
                       *[(f"dpms-on-{w}-sleep", "uint", str(v)) for w in ("battery", "ac")],
                       *[(f"dpms-on-{w}-off", "uint", str(v)) for w in ("battery", "ac")],
                       *[(f"blank-on-{w}", "int", str(v)) for w in ("battery", "ac")]],
        "sleep_after": [*[(f"inactivity-on-{w}", "uint", str(v)) for w in ("battery", "ac")],
                        *[(f"inactivity-sleep-mode-on-{w}", "uint", "1") for w in ("battery", "ac")]],
        # Sleep is left to the system (light sleep, then hibernate after two hours: keyboard,
        # trackpad and speakers survive); lock and screen off are done by the power manager.
        "lid": [(f"lid-action-on-{w}", "uint", str(v)) for w in ("battery", "ac")]
               + [("logind-handle-lid-switch", "bool", "true" if v == 1 else "false")],
    }.get(key, [])
    for prop, typ, val in props:
        if run(["xfconf-query", "-c", "xfce4-power-manager", "-p", XFPM + prop, "-s", val])[0]:
            run(["xfconf-query", "-c", "xfce4-power-manager", "-p", XFPM + prop, "-n", "-t", typ, "-s", val])


# Trackpad: kept by xfsettingsd (channel "pointers"), which applies libinput settings live.
PAD = "/Apple_SPI_Touchpad/Properties/"
PAD_KEYS = {"tap": ("libinput_Tapping_Enabled", "int", 0), "natural": ("libinput_Natural_Scrolling_Enabled", "int", 0),
            "speed": ("libinput_Accel_Speed", "double", 0.0)}


def read_trackpad():
    if "Apple SPI Touchpad" not in Path("/proc/bus/input/devices").read_text():
        return None
    out = {}
    for key, (prop, typ, default) in PAD_KEYS.items():
        _, v, _ = run(["xfconf-query", "-c", "pointers", "-p", PAD + prop])
        try:
            out[key] = round(float(v.strip().replace(",", ".")), 1) if typ == "double" else int(v.strip())
        except ValueError:
            out[key] = default
    return out


def write_trackpad(key, value):
    prop, typ, _ = PAD_KEYS[key]
    val = str(float(value)) if typ == "double" else str(int(value))
    if run(["xfconf-query", "-c", "pointers", "-p", PAD + prop, "-s", val])[0]:
        run(["xfconf-query", "-c", "pointers", "-p", PAD + prop, "-n", "-t", typ, "-s", val])


def read_time():
    """The clock: whether it sets itself from the internet, the time zone, whether it is in step."""
    _, out, _ = run(["timedatectl", "show"])
    v = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    return {"auto": v.get("NTP") == "yes", "synced": v.get("NTPSynchronized") == "yes",
            "can_auto": v.get("CanNTP") == "yes", "zone": v.get("Timezone", ""),
            "now": time.strftime("%Y-%m-%dT%H:%M")}


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
    outs, cur = [], None
    for line in out.splitlines():
        m = re.match(r"^(\S+) (connected|disconnected)( primary)?\s*(\d+x\d+\+\d+\+\d+)?", line)
        if m:
            cur = None
            if m.group(2) == "connected":
                cur = {"name": m.group(1), "primary": bool(m.group(3)), "active": bool(m.group(4)),
                       "builtin": m.group(1).startswith(("eDP", "LVDS", "DSI")), "modes": [], "current": None,
                       "x": int(m.group(4).split("+")[1]) if m.group(4) else 0}
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
            if mres == o["current"] or mres == o["modes"][0] or (w >= 1280 and abs(w / h - w0 / max(1, int(o["modes"][0].split("x")[1]))) < 0.02):
                keep.append(mres)
        o["modes"] = keep[:6]
    _, dpi, _ = run(["xfconf-query", "-c", "xsettings", "-p", "/Xft/DPI"])
    return {"outputs": outs, "dpi": int(dpi.strip()) if dpi.strip().lstrip("-").isdigit() and int(dpi.strip()) > 0 else 96}


def write_display(value):
    v = value or {}
    if v.get("dpi"):
        d = str(int(v["dpi"]))
        if run(["xfconf-query", "-c", "xsettings", "-p", "/Xft/DPI", "-s", d])[0]:
            run(["xfconf-query", "-c", "xsettings", "-p", "/Xft/DPI", "-n", "-t", "int", "-s", d])
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
            "right":  ["--output", a, "--auto", "--primary", "--output", b, "--auto", "--right-of", a],
            "left":   ["--output", a, "--auto", "--primary", "--output", b, "--auto", "--left-of", a],
            "mirror": ["--output", a, "--auto", "--primary", "--output", b, "--auto", "--same-as", a],
            "outer":  ["--output", b, "--auto", "--primary", "--output", a, "--off"],
            "inner":  ["--output", a, "--auto", "--primary", "--output", b, "--off"],
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
                apps[name] = {"id": a.get_id(), "name": name, "about": a.get_description() or "",
                              "system": "Settings" in cats.split(";") or "System" in cats.split(";")}
        except Exception:
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
    index = {}
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
            continue
    _APP_INDEX = index
    return index


def open_windows():
    """App windows in the order they were opened, with their app's name and icon."""
    try:
        out = subprocess.run(["wmctrl", "-lx"], capture_output=True, text=True, timeout=3).stdout
    except Exception:
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
        candidates += [wmclass[:half].lower(), wmclass[half + 1:].lower()]
        # Kūhaku's own apps are Python scripts: "tegami.py" is the app tegami (Mado runs as browser.py).
        own = {"browser": "mado", "terminal": "kuhaku-terminal"}
        candidates += [own.get(c.removesuffix(".py"), c.removesuffix(".py")) for c in list(candidates) if c.endswith(".py")]
        app = next((index[c] for c in candidates if c in index), None)
        icon = app[2] if app else ""
        # At night, Kūhaku's own apps show their dark icons (light ones would glare in the dark list).
        dark = HERE / "icons" / "apps-night" / (Path(icon).stem + ".png") if icon else None
        wins.append({"xid": xid, "app": app[0] if app else "", "name": (app[1] if app else "") or title,
                     "icon": icon, "iconNight": dark.as_uri() if dark and dark.exists() else icon, "title": title})
    return wins


def launch_app(app_id):
    from gi.repository import Gio
    try:
        info = Gio.DesktopAppInfo.new(app_id)
        if info:
            info.launch([], None)
    except Exception:
        pass


def short(text, n=140):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


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
            text = {
                "Bash": lambda: i.get("description") or short(i.get("command")),
                "Read": lambda: f"Reading {path}",
                "Write": lambda: f"Writing {path}",
                "Edit": lambda: f"Editing {path}",
                "Glob": lambda: f"Looking for {i.get('pattern', 'files')}",
                "Grep": lambda: f"Searching for “{short(i.get('pattern'), 60)}”",
                "WebSearch": lambda: f"Searching the web for “{short(i.get('query'), 80)}”",
                "WebFetch": lambda: f"Reading {short(i.get('url'), 80)}",
                "Agent": lambda: i.get("description") or "Asking a helper",
                "TodoWrite": lambda: "Planning the steps",
            }.get(name, lambda: name.replace("mcp__", "").replace("__", " ").replace("_", " "))()
            steps.append({"kind": "tool", "text": short(text)})
    return steps


class Kuhaku:
    def __init__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_FILE.exists():
            CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
        self.config = load_json(CONFIG_FILE, DEFAULT_CONFIG)
        self.state = load_json(STATE_FILE, {"session_id": None, "theme": None})
        # Every conversation is a tab; jobs wait in one line (jobs.py). state["session_id"] is the open tab's.
        self.tabs = jobs.Tabs(STATE_DIR / "tabs.json", self.state.get("session_id"))
        self.state["session_id"] = self.tabs.get(self.tabs.current)["session_id"]
        self.procs, self.live = {}, {}
        self.recorder = None
        self.whisper = None
        self.whisper_lock = threading.Lock()
        self.claude = shutil.which("claude") or str(HOME / ".local/bin/claude")

        self.window = Gtk.Window(title="Kūhaku")
        self.window.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_decorated(False)
        self.fit_to_screen()
        Gdk.Screen.get_default().connect("monitors-changed", lambda *_: self.fit_to_screen())

        manager = WebKit2.UserContentManager()
        manager.register_script_message_handler("kuhaku")
        manager.connect("script-message-received::kuhaku", self.on_message)
        self.view = WebKit2.WebView.new_with_user_content_manager(manager)
        settings = self.view.get_settings()
        settings.set_enable_developer_extras(bool(os.environ.get("KUHAKU_DEBUG")))
        settings.set_enable_page_cache(False)
        self.view.connect("context-menu", lambda *a: True)
        self.view.connect("decide-policy", self.on_policy)
        self.view.set_background_color(Gdk.RGBA(0.969, 0.957, 0.945, 1))
        self.view.load_uri((HERE / "index.html").as_uri())
        self.view.connect("load-changed", self.on_load)

        self.window.add(self.view)
        self.window.connect("destroy", self.quit)
        self.window.connect("key-press-event", self.on_key)
        self.window.connect("focus-in-event", lambda *a: self.js("focusInput()"))
        self.window.show_all()
        self.window.present()

        GLib.timeout_add_seconds(30, self.push_status)
        GLib.timeout_add_seconds(15, self.schedule)
        GLib.timeout_add(1500, lambda: (self.schedule(), False)[1])
        self.last_windows = None
        GLib.timeout_add(1500, self.push_windows)
        GLib.timeout_add_seconds(60, self.push_theme)
        self.recording = None
        threading.Thread(target=self.listen, daemon=True).start()
        # The red dot over apps lives in the top bar now (corner.py), before the red line.
        self.rundot = None
        try:
            from osd import Osd
            self.osd = Osd(self.current_theme)
        except Exception as e:
            print("Kūhaku: no on-screen volume feedback:", e)
        try:
            from corner import Corner
            self.corner = Corner(self.current_theme,
                                 lambda: self.window.get_window().get_xid() if self.window.get_window() else 0,
                                 read_battery, read_wifi, self.open_panel)
        except Exception as e:
            print("Kūhaku: no bar over apps:", e)

    # --- window -----------------------------------------------------------
    def fit_to_screen(self):
        display = Gdk.Display.get_default()
        mon = display.get_primary_monitor() or display.get_monitor(0)
        geo = mon.get_geometry()
        self.window.move(geo.x, geo.y)
        self.window.set_default_size(geo.width, geo.height)
        self.window.resize(geo.width, geo.height)

    def on_key(self, _w, ev):
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        alt = ev.state & Gdk.ModifierType.MOD1_MASK
        name = Gdk.keyval_name(ev.keyval)
        if ctrl and (name == "grave" or (alt and name == "Return")):
            self.open_terminal()
            return True
        if ctrl and alt and name == "r":
            self.view.reload()
            return True
        return False

    def on_policy(self, _view, decision, kind):
        # Links in answers open in the browser, never inside Kūhaku.
        if kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri.startswith("kuhaku://details"):
                self.open_terminal()   # what happened, in real Claude Code
                decision.ignore()
                return True
            if not uri.startswith("file://"):
                spawn([str(HOME / ".local/bin/mado"), uri])
                decision.ignore()
                return True
        return False

    def on_load(self, _view, event):
        if event == WebKit2.LoadEvent.FINISHED:
            self.last_windows = None
            self.push_theme()
            self.push_status()
            # Answers survive restarts: the list comes back, and the last one
            # stays on screen unless it was closed.
            answers = self.load_answers()[-300:]
            try:
                booted = float(re.search(r"btime (\d+)", Path("/proc/stat").read_text()).group(1))
            except Exception:
                booted = 0
            fresh = bool(answers) and (answers[-1].get("time") or 0) > booted
            self.send("setAnswers", {"answers": answers, "show": fresh and not self.state.get("dismissed")})
            cur = self.tabs.get(self.tabs.current)
            if "draft" not in cur:   # drafts used to be one for the whole screen
                cur.update(draft=self.state.get("draft", ""), next=self.state.get("next_draft", ""))
            self.push_tabs()
            self.restore_tab()
            self.window.present()
            self.view.grab_focus()
            self.js("focusInput()")

    def js(self, code):
        self.view.evaluate_javascript(code, -1, None, None, None, None, None)

    def send(self, fn, payload):
        self.js(f"{fn}({json.dumps(payload)})")

    # --- status / theme ---------------------------------------------------
    def push_status(self):
        self.send("setAccount", {"name": accounts.active()["name"], "many": len(accounts.ready("claude")) > 1})
        self.send("setStatus", {"battery": read_battery(), "wifi": read_wifi(), "bt": bluetooth_icon_state(),
                                "signal": wifi_signal()})
        return True

    def push_windows(self):
        wins = open_windows()
        if getattr(self, "rundot", None):
            self.rundot.update(wins)
        if wins != self.last_windows:
            self.last_windows = wins
            self.send("setWindows", wins)
        return True

    def current_theme(self):
        choice = self.state.get("theme") or self.config["theme"]
        if choice in ("day", "night"):
            return choice
        h = time.localtime().tm_hour
        a, b = self.config["night_from"], self.config["night_until"]
        return "night" if (h >= a or h < b) else "day"

    def push_theme(self):
        theme = self.current_theme()
        bg = (0.071, 0.071, 0.071) if theme == "night" else (0.969, 0.957, 0.945)
        self.view.set_background_color(Gdk.RGBA(*bg, 1))
        self.send("setTheme", {"theme": theme, "choice": self.state.get("theme") or "auto",
                               "from": self.config["night_from"], "until": self.config["night_until"]})
        return True

    def save_state(self):
        STATE_FILE.write_text(json.dumps(self.state, indent=2))

    # --- messages from the page ------------------------------------------
    def on_message(self, _manager, result):
        try:
            msg = json.loads(result.get_js_value().to_string())
        except Exception:
            return
        kind = msg.get("type")
        if kind == "ask":
            self.ask(msg.get("text", ""))
        elif kind == "cancel":
            self.cancel(msg.get("tab"))
        elif kind == "tab":
            self.tab_action(msg)
        elif kind == "menu":
            self.menu(msg.get("id"))
        elif kind == "dictate":
            self.dictate(msg.get("on"))
        elif kind == "picture":
            self.pick_pictures()
        elif kind == "stopRecording":
            if self.recording:
                self.capture("record")
        elif kind == "dismiss":
            self.state["dismissed"] = True
            self.save_state()
        elif kind == "draft":
            # What is typed (and the prepared next message) belongs to its tab and survives restarts.
            tab = self.tabs.get(msg.get("tab")) or self.tabs.get(self.tabs.current)
            tab["draft"] = msg.get("text", "")[:20000]
            tab["next"] = msg.get("next", "")[:20000]
            self.tabs.save()
        elif kind == "panel":
            threading.Thread(target=self.panel, args=(msg,), daemon=True).start()
        elif kind == "overview":
            from rundot import overview
            if not overview():
                self.js("openRuns()")

    # --- status panels: Wi-Fi, battery ----------------------------------
    def panel(self, msg):
        action = msg.get("action")
        reply = None
        if action == "wifi":
            reply = wifi_networks(msg.get("rescan", False))
        elif action == "wifi-connect":
            reply = wifi_connect(msg.get("ssid", ""), msg.get("password"))
            reply["ssid"] = msg.get("ssid")
        elif action == "wifi-forget":
            run(["nmcli", "connection", "delete", "id", msg.get("ssid", "")])
            reply = wifi_networks(False)
            action = "wifi"
        elif action == "wifi-disconnect":
            run(["nmcli", "connection", "down", "id", msg.get("ssid", "")])
            reply = {"ok": True}
        elif action == "wifi-radio":
            run(["nmcli", "radio", "wifi", "on" if msg.get("on") else "off"])
            time.sleep(2 if msg.get("on") else 0)
            reply = wifi_networks(bool(msg.get("on")))
        elif action == "bt":
            reply = bluetooth_state(msg.get("scan", False))
        elif action == "bt-power":
            run(["bluetoothctl", "power", "on" if msg.get("on") else "off"], timeout=8)
            reply = bluetooth_state()
            GLib.idle_add(lambda: self.push_status() and False)
        elif action == "bt-connect":
            mac = msg.get("mac", "")
            if re.fullmatch(r"[0-9A-F:]{17}", mac):
                info = run(["bluetoothctl", "info", mac], timeout=5)[1]
                if "Paired: yes" not in info:
                    run(["bluetoothctl", "pair", mac], timeout=30)
                    run(["bluetoothctl", "trust", mac], timeout=5)
                ok = "successful" in run(["bluetoothctl", "connect", mac], timeout=20)[1].lower()
            else:
                ok = False
            reply = {**bluetooth_state(), "note": "" if ok else "Could not connect. Is it in pairing mode?", "mac": mac}
            GLib.idle_add(lambda: self.push_status() and False)
        elif action in ("bt-disconnect", "bt-forget"):
            mac = msg.get("mac", "")
            if re.fullmatch(r"[0-9A-F:]{17}", mac):
                run(["bluetoothctl", "disconnect" if action == "bt-disconnect" else "remove", mac], timeout=10)
            reply = bluetooth_state()
            GLib.idle_add(lambda: self.push_status() and False)
        elif action == "battery":
            reply = battery_details()
        elif action == "power-profile":
            run(["powerprofilesctl", "set", msg.get("profile", "balanced")])
            reply = battery_details()
        elif action == "calendar":
            reply = {"days": daily_tokens(), "limits": plan_limits()}
        elif action == "cal-events":
            import calendars
            y, m = int(msg.get("year", 0)), int(msg.get("month", 0))
            reply = calendars.month_view(y, m)          # from the copy on disk: instant
            GLib.idle_add(self.send, "panelData", {"action": action, "data": reply})
            calendars.refresh()                          # then fresh from Google, if online
            reply = calendars.month_view(y, m)
        elif action == "cal-add":
            import calendars
            try:
                name = calendars.add(msg.get("url", ""))
                reply = {"ok": True, "name": name}
            except Exception as e:
                reply = {"ok": False, "error": "That address did not work. Copy the secret address in iCal format again."
                         if "calendar feed" in str(e) or "HTTP" in str(e) or "unknown url" in str(e).lower() else str(e)[:160]}
        elif action == "cal-remove":
            import calendars
            calendars.remove(int(msg.get("index", -1)))
            reply = {"ok": True}
        elif action == "settings":
            spawn(MENU.get(msg.get("which"), ["xfce4-settings-manager"]))
            return
        elif action == "apps":
            reply = list_apps()
        elif action == "launch":
            launch_app(msg.get("id", ""))
            return
        elif action == "focus":
            xid = msg.get("xid", "")
            if re.fullmatch(r"0x[0-9a-fA-F]+", xid):
                spawn(["wmctrl", "-i", "-a", xid])
            return
        elif action == "close":
            # Closes the window the polite way: the app may still ask to save.
            xid = msg.get("xid", "")
            if re.fullmatch(r"0x[0-9a-fA-F]+", xid):
                spawn(["wmctrl", "-i", "-c", xid])
            return
        elif action == "talk":
            # The mic hands over to the Claude app: a new chat, ready for its own mic.
            spawn(["claude-desktop", "claude://claude.ai/new?surface=chat&source=desktop_action"])
            return
        elif action == "prefs":
            reply = self.read_prefs()
        elif action in ("account-use", "account-cycle", "account-add", "account-signin", "account-remove",
                        "ai-signin", "ai-set-add", "ai-set-remove"):
            if action == "ai-signin":
                ident = accounts.ensure(str(msg.get("set") or "Personal"), str(msg.get("kind") or "claude"))
                GLib.idle_add(lambda: self.sign_in(ident) and False)
            elif action == "ai-set-add":
                accounts.add_set(msg.get("name"))
            elif action == "ai-set-remove":
                accounts.remove_set(msg.get("name"))
                GLib.idle_add(lambda: self.push_status() and False)
            elif action == "account-add":
                ident = accounts.add(msg.get("name", ""), msg.get("kind") or "claude")
                GLib.idle_add(lambda: self.switch_account(ident) and False)
            elif action == "account-use":
                GLib.idle_add(lambda: self.switch_account(msg.get("id")) and False)
            elif action == "account-cycle":
                GLib.idle_add(lambda: self.switch_account() and False)
            elif action == "account-signin":
                GLib.idle_add(lambda: self.sign_in(msg.get("id")) and False)
            elif action == "account-remove":
                accounts.remove(msg.get("id"))
                GLib.idle_add(lambda: self.push_status() and False)
            time.sleep(0.3)
            reply = self.read_prefs()
            action = "prefs"
        elif action == "pref":
            self.write_pref(msg.get("key"), msg.get("value"))
            if msg.get("quiet"):
                return
            reply = self.read_prefs()
            action = "prefs"
        GLib.idle_add(self.send, "panelData", {"action": action, "data": reply})
        GLib.idle_add(lambda: self.push_status() and False)

    # --- commands from keyboard shortcuts (kuhaku-capture writes to the FIFO) -
    def listen(self):
        try:
            if FIFO.exists():
                FIFO.unlink()
            os.mkfifo(FIFO)
        except OSError:
            return
        while True:
            with open(FIFO) as f:
                for line in f:
                    parts = line.split()
                    if len(parts) == 2 and parts[0] == "capture":
                        GLib.idle_add(self.capture, parts[1])
                    elif parts == ["menu"]:
                        GLib.idle_add(self.show_menu)
                    elif len(parts) >= 2 and parts[0] == "continue":   # from Kiroku: a past job in a new tab
                        GLib.idle_add(self.continue_thread, parts[1], " ".join(parts[2:]))

    def continue_thread(self, session_id, agent=""):
        """A finished conversation comes back as a tab (the one it lives in, if still open), ready for the next question."""
        cur = self.tabs.get(self.tabs.current)
        cur["session_id"] = self.state.get("session_id")
        tab = next((t for t in self.tabs.tabs if t.get("session_id") == session_id), None)
        if tab:
            self.tabs.current = tab["id"]
        else:
            tab = self.tabs.new()
            tab.update(session_id=session_id, agent=agent if agent.startswith(("acct:", "model:", "agent:")) else "")
            past = next((r for r in reversed(self.load_runs()) if r.get("session_id") == session_id), None)
            if past:
                tab["title"] = past.get("title") or jobs.title_of(past.get("question") or "")
                tab["answer"] = {"question": past.get("question", ""), "answer": past.get("answer", ""),
                                 "error": past.get("outcome") == "error", "time": int(past.get("end") or 0), "tab": tab["id"]}
        self.state["session_id"] = session_id
        self.tabs.save()
        self.save_state()
        subprocess.run(["wmctrl", "-k", "on"], capture_output=True)
        self.window.present()
        self.js("if (page) closePage(); if (panel) closePanel()")
        self.restore_tab()
        self.push_tabs()
        return False

    def load_runs(self):
        try:
            return [json.loads(l) for l in jobs.RUNS_FILE.read_text().splitlines() if l.strip()]
        except Exception:
            return []

    def show_menu(self):
        """The power key: Kūhaku comes forward with its menu open (lock, log out, restart, shut down)."""
        subprocess.run(["wmctrl", "-k", "on"], capture_output=True)
        self.window.present()
        self.js("if (page) closePage(); if (panel) closePanel(); openMenu()")
        return False

    def open_panel(self, name):
        """A click on the bar over apps: Kūhaku comes forward with that panel open, as on the home screen."""
        subprocess.run(["wmctrl", "-k", "on"], capture_output=True)
        self.window.present()
        self.js(f"if (page) closePage(); if (panel !== {json.dumps(name)}) openPanel({json.dumps(name)})")
        return False

    def note(self, title, detail):
        if getattr(self, "osd", None):
            self.osd.message(title, detail)

    def capture(self, how):
        stamp = time.strftime("%Y-%m-%d at %H.%M.%S")
        if how == "record":
            self.toggle_recording(stamp)
            return False
        # Mac-style: crosshair for an area, a picture in the corner to mark up (kuhaku-shot.py).
        if getattr(self, "osd", None):
            self.osd.win.hide()
        subprocess.Popen([sys.executable, str(HERE / "kuhaku-shot.py"), how], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return False

    def toggle_recording(self, stamp):
        if self.recording:
            proc, path = self.recording
            self.recording = None
            proc.send_signal(signal.SIGINT)  # -e: finish the file properly

            def wait():
                try:
                    proc.wait(timeout=20)
                except Exception:
                    proc.kill()
                GLib.idle_add(self.note, "Recording", "Saved to Videos")
                GLib.idle_add(self.send, "setRecording", False)
            threading.Thread(target=wait, daemon=True).start()
            return
        folder = HOME / "Videos" / "Screen Recordings"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"Screen Recording {stamp}.mp4"
        pipeline = ("ximagesrc use-damage=false show-pointer=true ! video/x-raw,framerate=24/1 ! queue ! videoconvert ! "
                    "video/x-raw,format=I420 ! x264enc speed-preset=ultrafast tune=zerolatency bitrate=8000 key-int-max=48 ! "
                    "h264parse ! queue ! mp4mux ! filesink")
        try:
            proc = subprocess.Popen(["gst-launch-1.0", "-e", "-q", *pipeline.split(), f"location=\"{path}\""],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            self.note("Recording", "Not available")
            return
        self.recording = (proc, path)
        self.note("Recording", "Same keys to stop")
        self.send("setRecording", True)

    # --- Kūhaku settings page --------------------------------------------
    def read_prefs(self):
        _, sinks_json, _ = run(["pactl", "-f", "json", "list", "sinks"])
        _, default_sink, _ = run(["pactl", "get-default-sink"])
        outputs = []
        try:
            for k in json.loads(sinks_json):
                if "Raw" in k.get("description", "") or k["name"] in ("mb81_speakers", "mb81_speakers_eq_out"):
                    continue
                outputs.append({"id": k["name"], "name": k.get("description", k["name"])
                                .replace(" (EQ)", "").replace("Built-in Audio ", ""),
                                "active": k["name"] == default_sink.strip()})
        except Exception:
            pass

        def percent(cmd):
            _, out, _ = run(cmd)
            nums = [int(x) for x in re.findall(r"(\d+)%", out)]
            return round(sum(nums) / len(nums)) if nums else 0
        bright = None
        bl = next(iter(sorted(Path("/sys/class/backlight").glob("*"))), None)
        if bl:
            try:
                bright = round(100 * int((bl / "brightness").read_text()) / int((bl / "max_brightness").read_text()))
            except Exception:
                pass
        _, profile, _ = run(["powerprofilesctl", "get"])
        mem = disk = None
        try:
            mem = round(int(re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1)) / 1048576)
            st = os.statvfs(str(HOME))
            disk = [round(st.f_bavail * st.f_frsize / 1e9), round(st.f_blocks * st.f_frsize / 1e9)]
        except Exception:
            pass
        os_name = re.search(r'PRETTY_NAME="([^"]+)"', Path("/etc/os-release").read_text())
        cpu = re.search(r"model name\s*:\s*(.+)", Path("/proc/cpuinfo").read_text())
        try:
            model = Path("/sys/class/dmi/id/product_name").read_text().strip()
        except Exception:
            model = ""
        bat = read_battery()
        updated = None
        for stamp in ("/var/lib/apt/periodic/unattended-upgrades-stamp", "/var/lib/apt/periodic/update-success-stamp"):
            try:
                updated = max(updated or 0, Path(stamp).stat().st_mtime)
            except OSError:
                pass
        return {
            "updates": {"auto": Path("/etc/apt/apt.conf.d/52kuhaku-unattended").exists(), "checked": updated,
                        "running": bool(getattr(self, "updating", None))},
            "theme": self.state.get("theme") or "auto",
            "volume": percent(["pactl", "get-sink-volume", "@DEFAULT_SINK@"]),
            "mic": percent(["pactl", "get-source-volume", "@DEFAULT_SOURCE@"]),
            "outputs": outputs, "brightness": bright, "profile": profile.strip() or None,
            "permission_mode": self.config.get("permission_mode") or "default",
            "model": self.config.get("model") or "default",
            "dictation_model": self.config.get("dictation_model") or "base",
            "displays": read_displays(),
            "power": read_power(),
            "time": read_time(),
            "accounts": accounts.listing(),
            "account_for": accounts.uses(),
            "ai_sets": accounts.sets_listing(),
            "ai_signing": sorted(getattr(self, "signing", set())),
            "ai_local": accounts.local_status(),
            "trackpad": read_trackpad(),
            "about": {"computer": model, "system": os_name.group(1) if os_name else "Linux",
                      "processor": re.sub(r"\s+", " ", cpu.group(1)).replace("(TM)", "").replace("(R)", "") if cpu else "",
                      "memory": mem, "disk": disk, "kernel": os.uname().release,
                      "battery": bat["level"] if bat else None},
        }

    def write_pref(self, key, value):
        if key == "volume":
            run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])
            run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{max(0, min(100, int(value)))}%"])
        elif key == "mic":
            run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{max(0, min(100, int(value)))}%"])
        elif key == "output":
            run(["pactl", "set-default-sink", str(value)])
        elif key == "brightness":
            run([str(HERE / "kuhaku-brightness"), "set", str(max(5, min(100, int(value))))])
        elif key == "display":
            write_display(value)
        elif key == "profile":
            run(["powerprofilesctl", "set", str(value)])
        elif key in POWER_KEYS:
            write_power(key, value)
        elif key in ("time_auto", "time_zone", "time_set"):
            write_time(key, value)
            GLib.idle_add(lambda: self.push_status() and False)
        elif key in PAD_KEYS:
            write_trackpad(key, value)
        elif key == "account":
            GLib.idle_add(lambda: self.switch_account(str(value)) and False)
        elif key == "home_set":
            ident = accounts.ensure(str(value), "claude")
            GLib.idle_add(lambda: self.switch_account(ident) and False)
        elif key in ("account_mail", "account_dev"):
            accounts.set_for(key[8:], str(value or ""))
        elif key == "local_model":
            accounts.set_local_model(value)
        elif key == "theme":
            self.state["theme"] = None if value == "auto" else value
            self.save_state()
            GLib.idle_add(lambda: self.push_theme() and False)
        elif key == "update-now":
            self.update_now()
        elif key in ("permission_mode", "model", "dictation_model"):
            self.config[key] = None if value == "default" else value
            CONFIG_FILE.write_text(json.dumps(self.config, indent=2) + "\n")
            if key == "dictation_model" and self.whisper:
                self.whisper.terminate()
                self.whisper = None

    def update_now(self):
        """Everything up to date now (asks for the password once); the OSD says when it is done."""
        if getattr(self, "updating", None):
            return

        def work():
            r = subprocess.run(["pkexec", "sh", "-c", "DEBIAN_FRONTEND=noninteractive apt-get update -q && "
                                "DEBIAN_FRONTEND=noninteractive apt-get -y -q full-upgrade && apt-get -y -q autoremove"],
                               capture_output=True, text=True)
            self.updating = None
            msg = "Up to date" if r.returncode == 0 else ("Cancelled" if r.returncode in (126, 127) else "Could not finish")
            GLib.idle_add(self.note, "Updates", msg)
        self.updating = threading.Thread(target=work, daemon=True)
        self.updating.start()
        self.note("Updates", "Updating…")

    # --- pictures for Claude: copied into ~/Pictures/ForClaude ------------
    def pick_pictures(self):
        dialog = Gtk.FileChooserNative.new("Add a picture for Claude", self.window,
                                           Gtk.FileChooserAction.OPEN, "Add", "Cancel")
        dialog.set_select_multiple(True)
        shots = Path.home() / "Pictures" / "Screenshots"
        if shots.is_dir():
            dialog.set_current_folder(str(shots))
        images = Gtk.FileFilter()
        images.set_name("Pictures")
        images.add_mime_type("image/*")
        dialog.add_filter(images)
        files = []
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            inbox = Path.home() / "Pictures" / "ForClaude"
            inbox.mkdir(parents=True, exist_ok=True)
            for name in dialog.get_filenames():
                src = Path(name)
                if src.parent == inbox:
                    files.append(str(src).replace(str(Path.home()), "~", 1))
                    continue
                dest, n = inbox / src.name, 2
                while dest.exists():
                    dest, n = inbox / f"{src.stem} {n}{src.suffix}", n + 1
                try:
                    shutil.copy2(src, dest)
                    files.append(str(dest).replace(str(Path.home()), "~", 1))
                except OSError:
                    pass
        dialog.destroy()
        self.send("picturesAdded", {"files": files})

    # --- dictation: record with PipeWire, transcribe offline with Whisper --
    def dictate(self, on):
        if on and not self.recorder:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                self.recorder = subprocess.Popen(
                    ["parec", "--rate=16000", "--channels=1", "--format=s16le",
                     "--file-format=wav", str(DICTATION_FILE)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                # Load Whisper while the user is still speaking.
                threading.Thread(target=self.start_whisper, daemon=True).start()
            except FileNotFoundError:
                self.send("dictationDone", {"error": "No microphone recorder found."})
        elif not on and self.recorder:
            rec, self.recorder = self.recorder, None
            rec.send_signal(signal.SIGINT)  # lets parec finish the WAV header
            rec.wait()
            threading.Thread(target=self.transcribe, daemon=True).start()

    def start_whisper(self):
        """Keep one Whisper worker loaded so dictation answers in seconds."""
        with self.whisper_lock:
            if self.whisper and self.whisper.poll() is None:
                return self.whisper
            python = HERE / "venv" / "bin" / "python"
            if not python.exists():
                return None
            self.whisper = subprocess.Popen(
                [str(python), str(HERE / "transcribe.py"), self.config.get("dictation_model") or "base"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            self.whisper.stdout.readline()  # {"ready": true}
            return self.whisper

    def transcribe(self):
        text, error = "", None
        try:
            worker = self.start_whisper()
            if not worker:
                raise FileNotFoundError
            worker.stdin.write(f"{DICTATION_FILE}\n")
            worker.stdin.flush()
            reply = json.loads(worker.stdout.readline() or "{}")
            text = reply.get("text", "")
            if "error" in reply or not reply:
                error = "Dictation failed."
        except FileNotFoundError:
            error = "Dictation is not set up yet."
        except Exception:
            error = "Dictation failed."
        GLib.idle_add(self.send, "dictationDone", {"text": text, "error": error})

    def menu(self, item):
        if item == "terminal":
            self.open_terminal()
        elif item == "kansei":
            spawn(["python3", str(HERE / "kansei.py")])
        elif item == "kiroku":
            spawn(["python3", str(HERE / "kiroku.py")])
        elif item == "new":
            self.cancel(self.tabs.current)
            self.state["session_id"] = None
            tab = self.tabs.get(self.tabs.current)
            tab.update(session_id=None, title="", named=False, status="idle", question="")
            self.tabs.save()
            self.save_state()
            self.push_tabs()
            self.js("resetConversation()")
        elif item == "theme":
            self.state["theme"] = "night" if self.current_theme() == "day" else "day"
            self.save_state()
            self.push_theme()
        elif item in ("theme-day", "theme-night"):
            self.state["theme"] = item.split("-")[1]
            self.save_state()
            self.push_theme()
        elif item == "theme-auto":
            self.state["theme"] = None
            self.save_state()
            self.push_theme()
        # Inside XFCE's session ask it (it saves and closes apps); in Kūhaku's own session use systemd.
        elif item == "restart":
            spawn(["xfce4-session-logout", "--reboot"] if pgrep_x("xfce4-session") else ["systemctl", "reboot"])
        elif item == "shutdown":
            spawn(["xfce4-session-logout", "--halt"] if pgrep_x("xfce4-session") else ["systemctl", "poweroff"])
        elif item == "logout" and not pgrep_x("xfce4-session"):
            spawn(["loginctl", "terminate-session", os.environ.get("XDG_SESSION_ID", "")])
        elif item == "lock" and not shutil.which("xflock4"):
            spawn(["python3", os.path.join(HERE, "kuhaku-lock.py"), "lock"])
        elif item in MENU:
            argv = MENU[item]
            spawn(argv)

    def switch_account(self, ident=None):
        """Another Claude account: its own conversation picks up where it was left."""
        target = next((a for a in accounts.load()["accounts"] if a["id"] == ident), None) if ident else None
        if target and accounts.kind(target) != "claude":
            if not accounts.signed_in(target):
                self.sign_in(target["id"])
            return
        if target and not accounts.signed_in(target):
            # Not signed in yet: nothing to switch to. Sign in first; it becomes the one in use once done.
            self.sign_in(target["id"], then_use=True)
            return
        old = accounts.active()["id"]
        sessions = self.state.setdefault("sessions", {})
        sessions[old] = self.state.get("session_id")
        (accounts.use(ident) if ident else accounts.cycle())
        new = accounts.active()
        self.state["session_id"] = sessions.get(new["id"])
        self.tabs.get(self.tabs.current)["session_id"] = self.state["session_id"]
        self.tabs.save()
        self.save_state()
        _limits_cache[0] = 0
        self.push_status()

    def sign_in(self, ident, then_use=False):
        """Sign in to an account: the tool's own sign-in, quietly, for that account's folder.
        Its page opens in Kūhaku's clean sign-in window (signin-bin/xdg-open), not Mado, where your
        other account is already in. Closing that window stops the tool again."""
        acct = next((a for a in accounts.load()["accounts"] if a["id"] == ident), None)
        if not acct:
            return
        procs = self.__dict__.setdefault("signin_procs", {})
        old = procs.pop(ident, None)
        if old and old.poll() is None:
            old.terminate()          # a sign-in left waiting: start afresh
        env = accounts.env(ident=ident)
        shim = str(HERE / "signin-bin")
        env.update(PATH=shim + ":" + env["PATH"], BROWSER=shim + "/xdg-open",
                   KUHAKU_SIGNIN_TITLE="Sign in · " + accounts.label(acct))
        if accounts.kind(acct) == "claude":
            env["KUHAKU_SIGNIN_BROWSER"] = "firefox"   # Cloudflare's human check stops WebKitGTK
        log = open(STATE_DIR / "signin.log", "a")
        # stdin stays open: Claude reads a pasted code from it if ever needed; Gemini asks yes/no first.
        p = subprocess.Popen(["bash", "-c", "exec env " + accounts.login_command(acct)], env=env, cwd=str(HOME),
                             stdin=subprocess.PIPE, stdout=log, stderr=log, start_new_session=True)
        log.close()
        if accounts.kind(acct) == "gemini":
            p.stdin.write(b"y\n")
            p.stdin.flush()
        procs[ident] = p
        started = time.time()
        self.signing = getattr(self, "signing", set()) | {ident}
        self.send("panelData", {"action": "prefs", "data": self.read_prefs()})

        def watch():
            done = accounts.signed_in(acct)
            if done or p.poll() is not None or time.time() - started > 900:
                if p.poll() is None:
                    p.terminate()
                self.signing.discard(ident)
                if done and then_use:
                    self.switch_account(ident)
                self.push_status()
                self.send("panelData", {"action": "prefs", "data": self.read_prefs()})
                return False
            return True
        GLib.timeout_add_seconds(2, watch)

    def open_terminal(self):
        """The escape hatch: real Claude Code, same conversation, full history."""
        sid = self.state.get("session_id")
        cmd = accounts.shell_prefix() + (f"{self.claude} --resume {sid}" if sid else self.claude)
        term = self.config.get("terminal") or "kuhaku-terminal"
        kuhaku_term = HOME / ".local/bin/kuhaku-terminal"
        # A terminal must always open: an unknown or missing choice falls back to Kūhaku's own.
        if term != "kuhaku-terminal" and not shutil.which(term):
            term = "kuhaku-terminal"
        if term == "kuhaku-terminal" and kuhaku_term.exists():
            spawn([str(kuhaku_term), "-e", cmd])
        elif shutil.which(term) and "xfce4-terminal" in term:
            spawn([term, "--maximize", "--title=Claude", f"--working-directory={HOME}",
                   "-x", "bash", "-lc", f"{cmd}; exec bash"])
        else:
            spawn(["x-terminal-emulator", "-e", "bash", "-lc", f"{cmd}; exec bash"])

    # --- Claude -----------------------------------------------------------
    def ask(self, text):
        """A question goes into the open tab's place in the line; schedule() starts it when there is room."""
        text = text.strip()
        tab = self.tabs.get(self.tabs.current)
        if not text or tab["status"] in ("running", "queued"):
            return
        at, text = jobs.parse_when(text)
        tab.update(question=text, status="queued", queued=time.time(), at=at, note=None, unseen=False)
        if not tab["title"]:
            tab["title"] = jobs.title_of(text)
            if not tab.get("named"):
                threading.Thread(target=self.name_tab, args=(tab["id"], text), daemon=True).start()
        self.tabs.save()
        self.schedule()

    def name_tab(self, tab_id, text):
        """Claude (the quick one, without tools) gives a new job a short name while it starts."""
        try:
            r = subprocess.run([self.claude, "-p", "--model", "haiku", "--tools", "", "--no-session-persistence",
                                "--system-prompt", "Name the task you are given. Reply with only a title of one to "
                                "four words in the task's language: no quotes, no full stop, nothing else."],
                               input=text[:3000], capture_output=True, text=True, timeout=60, cwd=str(HOME),
                               env=accounts.env())
            name = (r.stdout.strip().splitlines() or [""])[0].strip(" \"'.*") if r.returncode == 0 else ""
        except Exception:
            name = ""
        if name and len(name) <= 40:
            GLib.idle_add(self.set_title, tab_id, name)

    def set_title(self, tab_id, name):
        tab = self.tabs.get(tab_id)
        if tab and not tab.get("named"):
            tab["title"] = name
            self.tabs.save()
            self.push_tabs()
        return False

    def agent_choices(self):
        """Who can do a tab's job: Claude, its quicker models, your own Claude agents, other accounts."""
        # Which version each choice really is: what Claude Code said it started, else today's latest.
        seen = self.state.get("models") or {}
        default = self.config.get("model") or seen.get("") or "claude-opus-5-5"
        out = [{"id": "", "name": jobs.model_label(default), "about": "the default; most capable, for building and hard problems"},
               {"id": "model:sonnet", "name": jobs.model_label(seen.get("model:sonnet") or "claude-sonnet-5-5"),
                "about": "quick and capable, for everyday jobs; lighter on your plan"},
               {"id": "model:haiku", "name": jobs.model_label(seen.get("model:haiku") or "claude-haiku-5-5"),
                "about": "the fastest, for short simple jobs; lightest on your plan"}]
        for f in sorted((accounts.config_dir() / "agents").glob("*.md")):
            head = f.read_text(errors="ignore")[:2000]
            m = re.search(r"^name:\s*(.+)$", head, re.M)
            d = re.search(r"^description:\s*(.+)$", head, re.M)
            name = m.group(1).strip().strip("\"'") if m else f.stem
            out.append({"id": "agent:" + name, "name": name.replace("-", " ").capitalize(),
                        "about": "your agent" + (": " + d.group(1).strip().strip("\"'")[:90] if d else "")})
        accts = accounts.ready("claude")   # an account that is not signed in cannot do a job
        if len(accts) > 1:
            out += [{"id": "acct:" + a["id"], "name": "Claude · " + a["name"],
                     "about": jobs.model_label(default) + " in your " + a["name"] + " account"} for a in accts]
        return out

    def argv_for(self, tab):
        argv = [self.claude, "-p", tab["question"], "--output-format", "stream-json", "--verbose",
                "--append-system-prompt", SYSTEM_PROMPT]
        if self.config.get("permission_mode"):
            argv += ["--permission-mode", self.config["permission_mode"]]
        tools = list(self.config.get("allowed_tools") or [])
        if self.config.get("permission_mode") != "bypassPermissions":
            # Kūhaku cannot show a permission prompt, so anything short of "Anything,
            # without asking" is careful: no commands at all, rather than commands unasked.
            tools = [t for t in tools if t != "Bash" and not t.startswith("Bash(")]
            argv += ["--disallowedTools", "Bash"]
        if tools:
            argv += ["--allowedTools", " ".join(tools)]
        kind, _, name = (tab.get("agent") or "").partition(":")
        if kind == "model":
            argv += ["--model", name]
        elif self.config.get("model"):
            argv += ["--model", self.config["model"]]
        if kind == "agent":
            argv += ["--agent", name]
        if tab.get("session_id"):
            argv += ["--resume", tab["session_id"]]
        return argv

    def schedule(self):
        """Start what the line allows: memory and the Claude plan decide how many run at once."""
        waiting = self.tabs.waiting()
        if waiting:
            threading.Thread(target=plan_limits, daemon=True).start()   # fresh numbers for the next look
        room, why, resets = jobs.slots(len(self.tabs.running()), int(self.config.get("max_jobs") or 3),
                                       _limits_cache[1])
        for tab in waiting[:room]:
            tab.update(status="running", note=None)
            self.live[tab["id"]] = {"activity": [], "tokens": None}
            acct = (tab.get("agent") or "")[5:] if (tab.get("agent") or "").startswith("acct:") else None
            if acct and acct not in {a["id"] for a in accounts.ready("claude")}:
                acct = None   # chosen before it was signed in (or signed out since): the active one does it
            threading.Thread(target=self.run_claude, args=(self.argv_for(tab), tab["question"], tab["id"], acct),
                             daemon=True).start()
        back = ""
        if resets:
            try:
                back = " until " + datetime.fromisoformat(resets.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
            except ValueError:
                pass
        note = {"plan": "Waiting for your Claude plan" + back, "memory": "Waiting for memory",
                "busy": "In line"}.get(why)
        for tab in waiting[room:]:
            tab["note"] = note
        self.tabs.save()
        self.push_tabs()
        return True

    def push_tabs(self):
        self.send("setTabs", dict(self.tabs.public(), agents=self.agent_choices()))

    def restore_tab(self):
        """The open tab as it is: working (with what Claude has done so far), waiting, or at rest."""
        tab = self.tabs.get(self.tabs.current)
        live = self.live.get(tab["id"]) or {}
        self.send("restoreTab", {"id": tab["id"], "status": tab["status"], "question": tab.get("question", ""),
                                 "activity": live.get("activity", []), "tokens": live.get("tokens"),
                                 "answer": tab.get("answer"), "draft": tab.get("draft", ""),
                                 "next": tab.get("next", "")})

    def tab_action(self, msg):
        action, ident = msg.get("action"), msg.get("id")
        cur = self.tabs.get(self.tabs.current)
        cur["session_id"] = self.state.get("session_id")
        if "draft" in msg:   # what was typed in the tab being left stays with it
            cur.update(draft=str(msg.get("draft") or "")[:20000], next=str(msg.get("next") or "")[:20000])
        t = self.tabs.get(ident)
        if action == "new":
            self.tabs.new()
        elif action == "open" and self.tabs.get(ident):
            self.tabs.current = ident
        elif action == "close" and self.tabs.get(ident):
            self.cancel(ident)
            if t.get("anon"):
                acct = next((a for a in accounts.load()["accounts"] if "acct:" + a["id"] == t.get("agent")), None)
                jobs.erase_session(accounts.config_dir(acct), t.get("session_id"))
            self.tabs.close(ident)
        elif action == "anon" and t and t["status"] not in ("running", "queued"):
            # Into or out of anonymous: a fresh conversation, and an anonymous one is erased.
            if t.get("anon"):
                acct = next((a for a in accounts.load()["accounts"] if "acct:" + a["id"] == t.get("agent")), None)
                jobs.erase_session(accounts.config_dir(acct), t.get("session_id"))
            t.update(anon=not t.get("anon"), session_id=None, answer=None)
        elif action == "first" and self.tabs.get(ident):
            t = self.tabs.get(ident)
            t["priority"] = jobs.NORMAL if t.get("priority") == jobs.FIRST else jobs.FIRST
        elif action == "now" and t:
            t["at"] = None
        elif action == "rename" and t:
            title = re.sub(r"\s+", " ", str(msg.get("title") or "")).strip()[:60]
            t.update(title=title or jobs.title_of(t.get("question") or ""), named=bool(title))
        elif action == "agent" and t and t["status"] not in ("running", "queued"):
            agent = str(msg.get("agent") or "")
            if agent.startswith("acct:") != (t.get("agent") or "").startswith("acct:") or \
                    (agent.startswith("acct:") and agent != t.get("agent")):
                t["session_id"] = None   # a conversation lives in one account; another starts afresh
            t["agent"] = agent
        tab = self.tabs.get(self.tabs.current)
        tab["unseen"] = False
        self.state["session_id"] = tab.get("session_id")
        self.tabs.save()
        self.save_state()
        if action in ("new", "open", "close"):
            self.restore_tab()
        self.schedule()

    def run_claude(self, argv, question, tab_id, acct=None):
        env = accounts.env(ident=acct)
        env["PATH"] = f"{HOME}/.local/bin:" + env.get("PATH", "/usr/bin:/bin")
        result, error, session_id, lost = None, None, None, False
        keepawake.hold()   # no idle sleep while Claude works; released when the answer is in
        tokens = {"context": 0, "out": {}, "steps": 0}
        live = self.live.setdefault(tab_id, {"activity": [], "tokens": None})
        threading.Thread(target=lambda: GLib.idle_add(self.send, "setLimits", plan_limits()), daemon=True).start()
        tab = self.tabs.get(tab_id) or {}
        run = {"tab": tab_id, "title": tab.get("title"), "question": question, "agent": tab.get("agent") or "",
               "account": acct, "client": "Kūhaku", "start": time.time(), "outcome": "stopped"}
        cpu, watts, watts_at, peak = None, [], 0, 0
        anon = bool(tab.get("anon"))   # anonymous: no log, no line in Kiroku, no saved answer
        with open(os.devnull if anon else LOG_FILE, "a") as log:
            log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}  [tab {tab_id}]  {question}\n")
            log.flush()
            try:
                proc = self.procs[tab_id] = subprocess.Popen(argv, cwd=HOME, env=env, text=True,
                                                             stdout=subprocess.PIPE, stderr=log,
                                                             stdin=subprocess.DEVNULL, start_new_session=True)
                for line in proc.stdout:
                    log.write(line)
                    cpu = jobs.cpu_seconds(proc.pid) or cpu
                    if time.time() - watts_at > 5:
                        watts_at = time.time()
                        w = jobs.battery_watts()
                        if w:
                            watts.append(w)
                        peak = max(peak, jobs.memory_mb(proc.pid) or 0)
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    session_id = ev.get("session_id") or session_id
                    if ev.get("subtype") == "init":
                        run.update(model=ev.get("model"), cwd=ev.get("cwd"))
                        if ev.get("model") and not run["agent"].startswith(("agent:", "acct:")):
                            self.state.setdefault("models", {})[run["agent"]] = ev["model"]
                    for step in describe(ev):
                        live["activity"] = (live["activity"] + [step])[-200:]
                        GLib.idle_add(self.send, "addActivity", dict(step, tab=tab_id))
                    if ev.get("type") == "assistant":
                        msg = ev.get("message") or {}
                        u = msg.get("usage") or {}
                        if u:
                            # The latest call's input is the whole conversation so far.
                            tokens["context"] = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                                                 + u.get("cache_creation_input_tokens", 0))
                            tokens["out"][msg.get("id")] = u.get("output_tokens", 0)
                            tokens["steps"] = len(tokens["out"])
                            live["tokens"] = {"context": tokens["context"], "written": sum(tokens["out"].values()),
                                              "steps": tokens["steps"], "tab": tab_id}
                            GLib.idle_add(self.send, "setTokens", live["tokens"])
                    if ev.get("type") == "result":
                        if ev.get("is_error") and not ev.get("result"):
                            why = "; ".join(str(e) for e in (ev.get("errors") or []))[:300]
                            if "No conversation found" in why and "--resume" in argv:
                                # The conversation lives in another account: start this tab afresh there.
                                lost = True
                            error = "Something went wrong" + (": " + why if why else ".")
                        result = ev.get("result")
                        u = ev.get("usage") or {}
                        run.update(cost=ev.get("total_cost_usd"), turns=ev.get("num_turns"),
                                   api_ms=ev.get("duration_api_ms"), models=ev.get("modelUsage"),
                                   tokens={"in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0),
                                           "cache_read": u.get("cache_read_input_tokens", 0),
                                           "cache_write": u.get("cache_creation_input_tokens", 0)})
                code = proc.wait()
                # First start: Claude Code is there but not signed in yet. Open its sign-in once.
                said = (result or "") + " " + (error or "")
                if any(k in said for k in ("Invalid API key", "/login", "Not logged in", "not logged in", "Please run")):
                    result, error = None, "Sign in to Claude in the window that just opened, then ask again."
                    GLib.idle_add(self.first_sign_in)
                if self.procs.get(tab_id) is not proc:
                    return  # cancelled
                run["outcome"] = "done" if result else "error"
                if result is None and error is None:
                    error = ("Claude stopped unexpectedly."
                             if code else "Done.")
            except FileNotFoundError:
                error = "Claude is getting ready. Connect to Wi‑Fi; it installs itself in a minute, then ask again."
                run["outcome"] = "error"
                GLib.idle_add(self.install_claude_code)
            finally:
                if self.procs.get(tab_id) is not None and self.procs[tab_id].poll() is not None:
                    self.procs.pop(tab_id, None)
                keepawake.release()
                # One line per job for Kiroku: who did it, with which model, how long, what it cost.
                run.update(end=time.time(), session_id=session_id, cpu=cpu, answer=(result or error or "")[:4000],
                           memory_mb=round(peak) or None)
                run["seconds"] = round(run["end"] - run["start"], 1)
                run["wh"], run["wh_from"] = jobs.energy_wh(run["seconds"], cpu, watts)
                if not anon:
                    jobs.log_run(run)
        if lost and not result:
            i = argv.index("--resume")
            if self.tabs.get(tab_id):
                self.tabs.get(tab_id)["session_id"] = None
            return self.run_claude(argv[:i] + argv[i + 2:], question, tab_id, acct)
        GLib.idle_add(self.finish, question, result, error, session_id, tab_id)

    def first_sign_in(self):
        """Right after Claude Code is installed: its own first start, which signs in."""
        term = HOME / ".local/bin/kuhaku-terminal"
        spawn([str(term), "-e", self.claude] if term.exists() else ["x-terminal-emulator", "-e", self.claude])
        return False

    def install_claude_code(self):
        """Anthropic's own installer, into ~/.local/bin; then the sign-in opens."""
        if getattr(self, "installing", False):
            return False
        self.installing = True

        def work():
            r = subprocess.run(["sh", "-c", "curl -fsSL https://claude.ai/install.sh | bash"], capture_output=True)
            self.installing = False
            if r.returncode == 0 and Path(self.claude).exists():
                GLib.idle_add(self.note, "Claude", "Ready. Sign in once.")
                GLib.idle_add(self.first_sign_in)
        threading.Thread(target=work, daemon=True).start()
        return False

    def finish(self, question, result, error, session_id, tab_id):
        tab = self.tabs.get(tab_id)
        entry = {"question": question, "answer": result or error,
                 "error": bool(error and not result), "time": int(time.time()), "tab": tab_id}
        if tab:
            if session_id:
                tab["session_id"] = session_id
            tab.update(status="idle", answer=entry, unseen=tab_id != self.tabs.current)
            self.tabs.save()
        self.live.pop(tab_id, None)
        if tab_id == self.tabs.current and session_id:
            self.state["session_id"] = session_id
            self.save_state()
        if result and not (tab or {}).get("anon"):
            answers = self.load_answers()
            answers.append(entry)
            ANSWERS_FILE.write_text(json.dumps(answers[-300:]))
            if tab_id == self.tabs.current:
                self.state["dismissed"] = False
                self.save_state()
        self.send("showAnswer", entry)
        self.schedule()   # a place is free: the next in line starts
        return False

    def load_answers(self):
        try:
            return json.loads(ANSWERS_FILE.read_text())
        except Exception:
            return []

    def cancel(self, tab_id=None):
        """Stop a tab's job (the open tab's if none is named); a waiting one just leaves the line."""
        tab_id = tab_id or self.tabs.current
        tab = self.tabs.get(tab_id)
        if tab and tab["status"] in ("running", "queued"):
            tab.update(status="idle", note=None, at=None)
            self.tabs.save()
            GLib.idle_add(self.push_tabs)
        self.live.pop(tab_id, None)
        proc = self.procs.pop(tab_id, None)
        if proc:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                pass

    def quit(self, *_):
        # Running jobs stay "running" in tabs.json, so after a restart they continue from the line.
        for proc in list(self.procs.values()):
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                pass
        for p in (self.recorder, self.whisper):
            if p and p.poll() is None:
                p.terminate()
        Gtk.main_quit()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    Kuhaku()
    Gtk.main()
