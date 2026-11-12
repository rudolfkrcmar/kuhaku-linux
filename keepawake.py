"""While Claude works in the background, the computer does not fall asleep from being idle.

Asks the power manager (org.freedesktop.PowerManagement.Inhibit) to hold off idle sleep;
the hold ends when the work ends, or by itself if the program exits. Closing the lid does
not interrupt the work either: while Claude works the lid only turns the screen off, and the
computer goes to sleep as soon as the work is done if the lid is still closed by then.
On a Mac, caffeinate does the same.
"""

import os
import subprocess
import sys
import threading
from kuhaku_log import report  # faults go to the log instead of vanishing
from kuhaku_log import report  # faults go to the log instead of vanishing

MAC = sys.platform == "darwin"
if not MAC:
    from gi.repository import Gio, GLib

_lock = threading.Lock()
_count = 0
_cookie = None

# The lid, while Claude works: the power manager's lid action is set to "screen off" and the
# person's own choice is kept, put back when the last program working (Kūhaku, Tegami) is done,
# or at the next start should one of them stop in between.
LID_DIR = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "kuhaku", "lid")
LID_SAVED = os.path.join(LID_DIR, "saved")
LID_PROPS = ("lid-action-on-battery", "lid-action-on-ac")


def _xfpm(prop, value=None):
    path = "/xfce4-power-manager/" + prop
    if value is None:
        r = subprocess.run(["xfconf-query", "-c", "xfce4-power-manager", "-p", path], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    if (
        subprocess.run(
            ["xfconf-query", "-c", "xfce4-power-manager", "-p", path, "-s", value], capture_output=True
        ).returncode
        != 0
    ):
        subprocess.run(
            ["xfconf-query", "-c", "xfce4-power-manager", "-p", path, "-n", "-t", "uint", "-s", value],
            capture_output=True,
        )


def _holders():
    """Programs still working (one file per process id); files of ended ones are cleared."""
    alive = []
    try:
        for name in os.listdir(LID_DIR):
            if name.isdigit():
                if os.path.exists(f"/proc/{name}"):
                    alive.append(name)
                else:
                    os.unlink(os.path.join(LID_DIR, name))
    except FileNotFoundError:
        pass
    return alive


def _lid_keep_awake():
    try:
        os.makedirs(LID_DIR, exist_ok=True)
        if not os.path.exists(LID_SAVED):
            with open(LID_SAVED, "w") as f:
                f.write(" ".join(_xfpm(p) or "1" for p in LID_PROPS))
        open(os.path.join(LID_DIR, str(os.getpid())), "w").close()
        for p in LID_PROPS:
            _xfpm(p, "0")  # 0: the lid only switches the screen off
    except Exception:
        report()


def _lid_release():
    """This program is done; the last one done puts the person's lid action back.
    True when that happened with the lid closed (so it is time to sleep)."""
    try:
        os.unlink(os.path.join(LID_DIR, str(os.getpid())))
    except OSError:
        pass
    if _holders() or not os.path.exists(LID_SAVED):
        return False
    try:
        with open(LID_SAVED) as f:
            saved = f.read().split()
        for p, v in zip(LID_PROPS, saved):
            _xfpm(p, v if v.isdigit() else "1")
        os.unlink(LID_SAVED)
    except Exception:
        report()
        return False
    try:
        import glob

        return any("closed" in open(p).read() for p in glob.glob("/proc/acpi/button/lid/*/state"))
    except Exception:
        report()
        return False


def _call(method, args):
    """The session's power manager: Inhibit keeps the computer from sleeping when idle."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    return bus.call_sync(
        "org.freedesktop.PowerManagement",
        "/org/freedesktop/PowerManagement/Inhibit",
        "org.freedesktop.PowerManagement.Inhibit",
        method,
        args,
        None,
        0,
        3000,
        None,
    )


def hold(why="Claude is working"):
    global _count, _cookie
    with _lock:
        _count += 1
        if _count == 1:
            try:
                if MAC:  # held until released, or until this program ends
                    _cookie = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
                else:
                    _cookie = _call("Inhibit", GLib.Variant("(ss)", ("Kūhaku", why))).unpack()[0]
            except Exception:
                _cookie = None
            if not MAC:
                _lid_keep_awake()


def release():
    global _count, _cookie
    with _lock:
        _count = max(0, _count - 1)
        if _count == 0 and _cookie is not None:
            try:
                if MAC:
                    _cookie.terminate()
                else:
                    _call("UnInhibit", GLib.Variant("(u)", (_cookie,)))
            except Exception:
                report()
            _cookie = None
        if _count == 0 and not MAC and _lid_release():
            # The lid was closed while Claude worked: the work is done, so now it sleeps.
            subprocess.Popen(
                ["sh", "-c", "sleep 3; grep -qs closed /proc/acpi/button/lid/*/state && systemctl suspend"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )


class Awake:
    """with Awake(): ...  keeps the computer awake for the block."""

    def __init__(self, why="Claude is working"):
        self.why = why

    def __enter__(self):
        hold(self.why)

    def __exit__(self, *exc):
        release()


# Kūhaku stopped while Claude worked last time: the lid sleeps again as the person chose.
if not MAC and os.path.exists(LID_SAVED) and not _holders():
    _lid_release()
