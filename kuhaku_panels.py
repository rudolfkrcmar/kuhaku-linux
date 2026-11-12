"""The home screen's panels and pages answering the page: Wi-Fi, Bluetooth, battery, calendar,
apps and windows, notifications, unlock, accounts and preferences.

The page sends {"type": "panel", "action": ..., ...}; panel() looks the action up in PANEL and
runs its handler on a worker thread. A handler returns the data for the page (sent back as
panelData under the same action), Reply(action, data) to answer under another action, or
QUIET when there is nothing to send. Part of the Kuhaku window class (kuhaku.py).
"""

import os
import pwd
import re
import subprocess
import time
from collections import namedtuple

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

import claude_accounts as accounts
from kuhaku_base import Home
from kuhaku_config import HERE, HOME, MENU
from kuhaku_log import note
from kuhaku_system import (
    FINGER_ORDER,
    battery_details,
    bluetooth_state,
    daily_tokens,
    launch_app,
    list_apps,
    notices,
    plan_limits,
    run,
    spawn,
    wifi_connect,
    wifi_networks,
)

Reply = namedtuple("Reply", "action data")
QUIET = object()
MAC = re.compile(r"[0-9A-F:]{17}")
XID = re.compile(r"0x[0-9a-fA-F]+")


def later(fn, *args):
    """Run on the GTK thread, once."""
    GLib.idle_add(lambda: fn(*args) and False)


class Panels(Home):
    def panel(self, msg):
        action = msg.get("action")
        handler = PANEL.get(action)
        if handler is None:
            note(f"panel: unknown action {action!r}")
            return
        out = handler(self, msg)
        if out is QUIET:
            return
        if isinstance(out, Reply):
            action, out = out
        GLib.idle_add(self.send, "panelData", {"action": action, "data": out})
        later(self.push_status)

    # --- Wi-Fi -------------------------------------------------------------------------
    def _wifi(self, msg):
        return wifi_networks(msg.get("rescan", False))

    def _wifi_connect(self, msg):
        return {**wifi_connect(msg.get("ssid", ""), msg.get("password")), "ssid": msg.get("ssid")}

    def _wifi_forget(self, msg):
        run(["nmcli", "connection", "delete", "id", msg.get("ssid", "")])
        return Reply("wifi", wifi_networks(False))

    def _wifi_disconnect(self, msg):
        run(["nmcli", "connection", "down", "id", msg.get("ssid", "")])
        return {"ok": True}

    def _wifi_radio(self, msg):
        on = bool(msg.get("on"))
        run(["nmcli", "radio", "wifi", "on" if on else "off"])
        time.sleep(2 if on else 0)
        return wifi_networks(on)

    # --- Bluetooth ---------------------------------------------------------------------
    def _bt(self, msg):
        return bluetooth_state(msg.get("scan", False))

    def _bt_power(self, msg):
        run(["bluetoothctl", "power", "on" if msg.get("on") else "off"], timeout=8)
        later(self.push_status)
        return bluetooth_state()

    def _bt_connect(self, msg):
        mac, ok = msg.get("mac", ""), False
        if MAC.fullmatch(mac):
            if "Paired: yes" not in run(["bluetoothctl", "info", mac], timeout=5)[1]:
                run(["bluetoothctl", "pair", mac], timeout=30)
                run(["bluetoothctl", "trust", mac], timeout=5)
            ok = "successful" in run(["bluetoothctl", "connect", mac], timeout=20)[1].lower()
        later(self.push_status)
        return {**bluetooth_state(), "note": "" if ok else "Could not connect. Is it in pairing mode?", "mac": mac}

    def _bt_disconnect(self, msg, forget=False):
        mac = msg.get("mac", "")
        if MAC.fullmatch(mac):
            run(["bluetoothctl", "remove" if forget else "disconnect", mac], timeout=10)
        later(self.push_status)
        return bluetooth_state()

    def _bt_forget(self, msg):
        return self._bt_disconnect(msg, forget=True)

    # --- battery and calendar ----------------------------------------------------------
    def _battery(self, _msg):
        return battery_details()

    def _power_profile(self, msg):
        run(["powerprofilesctl", "set", msg.get("profile", "balanced")])
        return battery_details()

    def _calendar(self, _msg):
        return {"days": daily_tokens(), "limits": plan_limits()}

    def _cal_events(self, msg):
        import calendars

        y, m = int(msg.get("year", 0)), int(msg.get("month", 0))
        GLib.idle_add(self.send, "panelData", {"action": "cal-events", "data": calendars.month_view(y, m)})
        calendars.refresh()  # the copy on disk came first; now fresh, if online
        return calendars.month_view(y, m)

    def _cal_add(self, msg):
        import calendars

        try:
            return {"ok": True, "name": calendars.add(msg.get("url", ""))}
        except Exception as e:
            bad_address = "calendar feed" in str(e) or "HTTP" in str(e) or "unknown url" in str(e).lower()
            return {
                "ok": False,
                "error": "That address did not work. Copy the secret address in iCal format again."
                if bad_address
                else str(e)[:160],
            }

    def _cal_remove(self, msg):
        import calendars

        calendars.remove(int(msg.get("index", -1)))
        return {"ok": True}

    # --- apps, windows, notifications --------------------------------------------------
    def _settings(self, msg):
        spawn(MENU.get(msg.get("which"), ["xfce4-settings-manager"]))
        return QUIET

    def _apps(self, _msg):
        return list_apps()

    def _launch(self, msg):
        launch_app(msg.get("id", ""))
        return QUIET

    def _focus(self, msg):
        if XID.fullmatch(msg.get("xid", "")):
            spawn(["wmctrl", "-i", "-a", msg["xid"]])
        return QUIET

    def _close(self, msg):
        if XID.fullmatch(msg.get("xid", "")):
            spawn(["wmctrl", "-i", "-c", msg["xid"]])  # the polite way: the app may still ask to save
        return QUIET

    def _talk(self, _msg):
        # The Claude app's own voice chat: a new chat, its mic ready.
        spawn(["claude-desktop", "claude://claude.ai/new?surface=chat&source=desktop_action"])
        return QUIET

    def _notices(self, msg):
        return Reply("notices", notices(clear=msg.get("action") == "notices-clear"))

    # --- Settings: preferences, unlock, accounts ---------------------------------------
    def _prefs(self, _msg):
        return self.read_prefs()

    def _pref(self, msg):
        self.write_pref(msg.get("key"), msg.get("value"))
        return QUIET if msg.get("quiet") else Reply("prefs", self.read_prefs())

    def _unlock(self, msg):
        # Fingers and face: their own windows do the teaching; Settings is told again when they close.
        what, finger = msg.get("what"), str(msg.get("finger") or "")
        teach = {
            "add-finger": ["python3", str(HERE / "kuhaku-finger.py")],
            "face-setup": ["python3", str(HERE / "kuhaku-face.py"), "enroll"],
            "guided": ["python3", str(HERE / "kuhaku-unlock-setup.py")],
        }
        if what in teach:
            p = subprocess.Popen(
                teach[what], start_new_session=True, cwd=HOME, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

            def closed():
                if p.poll() is None:
                    return True
                self.send("panelData", {"action": "prefs", "data": self.read_prefs()})
                return False

            GLib.timeout_add_seconds(1, closed)
            return QUIET
        me = pwd.getpwuid(os.getuid()).pw_name
        if what == "forget-finger" and finger in FINGER_ORDER:
            subprocess.run(["fprintd-delete", me, "-f", finger], capture_output=True, timeout=15)
        elif what == "forget-fingers":
            subprocess.run(["fprintd-delete", me], capture_output=True, timeout=15)
        elif what == "face-forget":
            (HERE / "face" / "embeddings.npy").unlink(missing_ok=True)
        return Reply("prefs", self.read_prefs())

    def _account(self, msg):
        action = msg.get("action")
        if action == "ai-signin":
            ident = accounts.ensure(str(msg.get("set") or "Personal"), str(msg.get("kind") or "claude"))
            later(self.sign_in, ident)
        elif action == "ai-set-add":
            accounts.add_set(msg.get("name"))
        elif action == "ai-set-remove":
            accounts.remove_set(msg.get("name"))
            later(self.push_status)
        elif action == "account-add":
            later(self.switch_account, accounts.add(msg.get("name", ""), msg.get("kind") or "claude"))
        elif action == "account-use":
            later(self.switch_account, msg.get("id"))
        elif action == "account-cycle":
            later(self.switch_account)
        elif action == "account-signin":
            later(self.sign_in, msg.get("id"))
        elif action == "account-remove":
            accounts.remove(msg.get("id"))
            later(self.push_status)
        time.sleep(0.3)
        return Reply("prefs", self.read_prefs())


# Every action the page may ask for, and who answers it.
PANEL = {
    "wifi": Panels._wifi,
    "wifi-connect": Panels._wifi_connect,
    "wifi-forget": Panels._wifi_forget,
    "wifi-disconnect": Panels._wifi_disconnect,
    "wifi-radio": Panels._wifi_radio,
    "bt": Panels._bt,
    "bt-power": Panels._bt_power,
    "bt-connect": Panels._bt_connect,
    "bt-disconnect": Panels._bt_disconnect,
    "bt-forget": Panels._bt_forget,
    "battery": Panels._battery,
    "power-profile": Panels._power_profile,
    "calendar": Panels._calendar,
    "cal-events": Panels._cal_events,
    "cal-add": Panels._cal_add,
    "cal-remove": Panels._cal_remove,
    "settings": Panels._settings,
    "apps": Panels._apps,
    "launch": Panels._launch,
    "focus": Panels._focus,
    "close": Panels._close,
    "talk": Panels._talk,
    "notices": Panels._notices,
    "notices-clear": Panels._notices,
    "prefs": Panels._prefs,
    "pref": Panels._pref,
    "unlock": Panels._unlock,
    **{
        a: Panels._account
        for a in (
            "account-use",
            "account-cycle",
            "account-add",
            "account-signin",
            "account-remove",
            "ai-signin",
            "ai-set-add",
            "ai-set-remove",
        )
    },
}
