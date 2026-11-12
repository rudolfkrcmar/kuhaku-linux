"""The computer, as Kūhaku's home screen sees it: battery, Wi-Fi, Bluetooth, power, trackpad,
time, displays, apps and windows, and Claude's usage and plan limits.

Plain functions that read and change the system through its own tools (nmcli, bluetoothctl,
xfconf-query, xrandr, wmctrl). They never touch the window; kuhaku.py shows what they return.
"""
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_accounts as accounts  # noqa: E402
from kuhaku_log import report  # noqa: E402
from kuhaku_log import report  # faults go to the log instead of vanishing

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
    subprocess.Popen(argv, start_new_session=True, cwd=HOME,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def find_battery():
    """The laptop's battery, whatever the firmware calls it (BAT0, CMB1 …)."""
    for p in sorted(Path("/sys/class/power_supply").glob("*")):
        try:
            if (p / "type").read_text().strip() == "Battery" and \
                    (not (p / "scope").exists() or (p / "scope").read_text().strip() != "Device"):
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
        out = subprocess.run(["nmcli", "-t", "-f", "TYPE,STATE", "device"],
                             capture_output=True, text=True, timeout=3).stdout
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
        out = subprocess.run(["nmcli", "-t", "-f", "IN-USE,SIGNAL", "dev", "wifi", "list", "--rescan", "no"],
                             capture_output=True, text=True, timeout=3).stdout
        return next((int(l.split(":")[1]) for l in out.splitlines() if l.startswith("*")), 0)
    except Exception:
        report()
        return 0


def run(argv, timeout=15):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                             env={**os.environ, "LC_ALL": "C"})
        return out.returncode, out.stdout, out.stderr
    except Exception as e:
        report()
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
    path = next((d for d in devs.split() if "/battery_" in d and "DisplayDevice" not in d), None)
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
                        report()
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
    """Session and weekly limits of the Claude plan, as Claude Code itself last reported them
    (its rate_limit_event while working). Kūhaku never reads or uses the Claude sign-in."""
    if _limits_cache[1] is None and LIMITS_FILE.exists():
        try:
            _limits_cache[1] = json.loads(LIMITS_FILE.read_text())   # last known numbers after a restart
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
            out[names[key]] = {"percent": w["utilization"] * 100 if w["utilization"] <= 1 else w["utilization"],
                               "resets": datetime.fromtimestamp(at, timezone.utc).isoformat() if at else None}
    if not out:
        return None
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


FINGER_ORDER = ["right-index-finger", "left-index-finger", "right-middle-finger", "right-thumb", "left-thumb",
                "left-middle-finger", "right-ring-finger", "left-ring-finger", "right-little-finger", "left-little-finger"]


def read_unlock():
    """Fingers and face for the lock screen: the reader, the known fingers, the infrared camera."""
    reader, fingers = None, []
    if shutil.which("fprintd-list"):
        try:
            out = subprocess.run(["fprintd-list", pwd.getpwuid(os.getuid()).pw_name], capture_output=True,
                                 text=True, timeout=4).stdout
            m = re.search(r"enrolled for (.+?)\.\s*$", out, re.M) or re.search(r" on (.+?) \(", out)
            if "found 0 devices" not in out and ("Using device" in out or m):
                reader = m.group(1).strip() if m else "Fingerprint reader"
            got = set(re.findall(r"#\d+:\s*(\S+)", out))
            fingers = [f for f in FINGER_ORDER if f in got]
        except Exception:
            report()
    camera = any(p.name.startswith("video") and (p / "name").read_text().strip().endswith(": Integrated I")
                 for p in Path("/sys/class/video4linux").glob("video*") if (p / "name").exists())
    return {"reader": reader, "fingers": fingers, "face": {"camera": camera,
            "enrolled": (HERE / "face" / "embeddings.npy").exists()}}


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
            report()
            continue
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
        report()


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


# --- Notifications -------------------------------------------------------------
NOTIFY_LOG = HOME / ".cache" / "xfce4" / "notifyd" / "log.sqlite"


def notices(clear=False):
    """The notifications that came in (xfce4-notifyd keeps them), newest first; seen once shown."""
    import sqlite3
    if not NOTIFY_LOG.exists():
        return []
    try:
        db = sqlite3.connect(str(NOTIFY_LOG), timeout=2)
        if clear:
            db.execute("DELETE FROM notifications")
        rows = db.execute("SELECT timestamp, app_name, summary, body, is_read FROM notifications "
                          "ORDER BY timestamp DESC LIMIT 40").fetchall()
        db.execute("UPDATE notifications SET is_read = 1 WHERE is_read = 0")
        db.commit()
        db.close()
    except Exception:
        report()
        return []
    # notifyd stores microseconds since the epoch
    return [{"t": (t or 0) // 1_000_000 if (t or 0) > 10**12 else (t or 0), "app": a or "", "title": su or "",
             "body": re.sub(r"<[^>]+>", "", b or "")[:400], "new": not r} for t, a, su, b, r in rows]
