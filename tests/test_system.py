"""kuhaku_system.py: the readers of nmcli, bluetoothctl, xrandr, upower, xfconf-query and timedatectl,
with every command's output mocked, and the pure helpers (describe, short, note_limits, daily_tokens)."""

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import kuhaku_system as ks
from tests.util import TempDirCase, fake_completed


class FakeRun:
    """Stands in for ks.run: answers by the longest matching start of the command line."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, argv, timeout=15):
        self.calls.append(list(argv))
        line = " ".join(argv)
        best = max((k for k in self.table if line.startswith(k)), key=len, default=None)
        if best is None:
            return 1, "", "unknown"
        v = self.table[best]
        return (0, v, "") if isinstance(v, str) else v


def with_run(table):
    fake = FakeRun(table)
    return mock.patch.object(ks, "run", fake), fake


class Wifi(unittest.TestCase):
    SAVED = "Home:802-11-wireless\nWired connection 1:802-3-ethernet\nCafe\\:Guest:802-11-wireless\n"
    LIST = (
        "*:Home:70:WPA2:6\n"
        " :Home:40:WPA2:44\n"
        " :Cafe\\:Guest:55::11\n"
        " :Neighbour 5G:80:WPA1 WPA2:149\n"
        " :Neighbour 5G:30:WPA1 WPA2:1\n"
        " ::90:WPA2:6\n"
        " :Open:20:--:3\n"
        "garbage\n"
    )

    def test_networks(self):
        p, fake = with_run(
            {
                "nmcli radio wifi": "enabled\n",
                "nmcli -t -f NAME,TYPE connection show": self.SAVED,
                "nmcli -t -e yes -f IN-USE,SSID,SIGNAL,SECURITY,CHAN device wifi list": self.LIST,
            }
        )
        with p:
            out = ks.wifi_networks(rescan=False)
        self.assertTrue(out["enabled"])
        nets = {n["ssid"]: n for n in out["networks"]}
        self.assertEqual([n["ssid"] for n in out["networks"]], ["Home", "Neighbour 5G", "Cafe:Guest", "Open"])
        self.assertEqual(
            nets["Home"],
            {"ssid": "Home", "signal": 70, "secure": True, "active": True, "known": True, "band": "2.4 GHz"},
        )  # the one in use wins
        self.assertEqual(nets["Neighbour 5G"]["signal"], 80)  # else the strongest
        self.assertEqual(nets["Neighbour 5G"]["band"], "5 GHz")
        self.assertFalse(nets["Neighbour 5G"]["known"])
        self.assertTrue(nets["Cafe:Guest"]["known"])
        self.assertFalse(nets["Cafe:Guest"]["secure"])
        self.assertFalse(nets["Open"]["secure"])
        self.assertIn("no", fake.calls[-1])

    def test_rescan_flag_and_radio_off(self):
        p, fake = with_run({"nmcli radio wifi": "enabled\n"})
        with p:
            ks.wifi_networks(rescan=True)
        self.assertEqual(fake.calls[-1][-1], "yes")
        p, _ = with_run({"nmcli radio wifi": "disabled\n"})
        with p:
            self.assertEqual(ks.wifi_networks(True), {"enabled": False, "networks": []})

    def test_saved(self):
        p, _ = with_run({"nmcli -t -f NAME,TYPE connection show": self.SAVED})
        with p:
            self.assertTrue(ks.wifi_saved("Cafe:Guest"))
            self.assertTrue(ks.wifi_saved("Home"))
            self.assertFalse(ks.wifi_saved("Wired connection 1"))

    def test_connect(self):
        p, fake = with_run(
            {
                "nmcli -t -f NAME,TYPE connection show": self.SAVED,
                "nmcli connection up": "ok",
                "nmcli connection modify": "ok",
            }
        )
        with p:
            self.assertEqual(ks.wifi_connect("Home", "newpass"), {"ok": True})
        self.assertEqual(fake.calls[1][:4], ["nmcli", "connection", "modify", "id"])  # never a second copy
        p, fake = with_run(
            {
                "nmcli -t -f NAME,TYPE connection show": "",
                "nmcli device wifi connect": (4, "", "Error: Secrets were required, but not provided."),
            }
        )
        with p:
            self.assertEqual(ks.wifi_connect("New", "bad"), {"ok": False, "needPassword": True})
        p, fake = with_run(
            {
                "nmcli connection up": (10, "", "no such connection"),
                "nmcli device wifi connect": (10, "", "No network with SSID 'x' found."),
            }
        )
        with p:
            self.assertEqual(ks.wifi_connect("x", ""), {"ok": False, "error": "Could not connect."})
        self.assertEqual(fake.calls[-1], ["nmcli", "device", "wifi", "connect", "x"])

    def test_read_vpn(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            net = Path(d)
            (net / "tailscale0").mkdir()
            (net / "tailscale0" / "uevent").write_text("INTERFACE=tailscale0\n")
            with mock.patch.object(ks.subprocess, "run", return_value=fake_completed("Office:802-11-wireless:wlp1\n")):
                self.assertIsNone(ks.read_vpn(net))  # Tailscale alone is not a VPN in use
            with mock.patch.object(ks.subprocess, "run", return_value=fake_completed("Work:vpn:\n")):
                self.assertEqual(ks.read_vpn(net), {"name": "Work", "app": ""})
            (net / "wg0").mkdir()
            (net / "wg0" / "uevent").write_text("DEVTYPE=wireguard\n")
            with (
                mock.patch.object(ks.subprocess, "run", return_value=fake_completed("")),
                mock.patch.object(ks.Path, "exists", return_value=True),
                mock.patch.object(ks, "defguard_location", return_value="hq"),
            ):
                self.assertEqual(ks.read_vpn(net), {"name": "hq", "app": "defguard-client.desktop"})

    def test_vpn_list_and_switch(self):
        nm = "Home WG:0b6a2b1e-1111-2222-3333-444455556666:wireguard:yes\nNord\\: Prague:0b6a2b1e-1111-2222-3333-444455556667:vpn:no\nOffice:0b6a2b1e-1111-2222-3333-444455556668:802-11-wireless:yes\n"
        with (
            mock.patch.object(ks, "run", return_value=(0, nm, "")),
            mock.patch.object(ks.shutil, "which", return_value=None),
            mock.patch.object(ks, "DEFGUARD_DB", Path("/nonexistent/defguard.db")),
        ):
            got = ks.vpn_list()
        self.assertEqual([(v["name"], v["on"]) for v in got], [("Home WG", True), ("Nord: Prague", False)])
        with mock.patch.object(ks, "run", return_value=(0, "", "")) as r:
            self.assertEqual(ks.vpn_switch("nm:0b6a2b1e-1111-2222-3333-444455556667", True), {"ok": True})
        self.assertEqual(r.call_args[0][0][:3], ["nmcli", "connection", "up"])
        with mock.patch.object(ks, "run", return_value=(4, "", "Secrets were required")):
            self.assertEqual(
                ks.vpn_switch("nm:0b6a2b1e-1111-2222-3333-444455556667", True)["error"], "It needs a password first."
            )
        self.assertFalse(ks.vpn_switch("nm:; rm -rf /", True)["ok"])
        with mock.patch.object(ks, "launch_app") as launch:
            self.assertTrue(ks.vpn_switch("defguard:2", True)["opened"])
        launch.assert_called_once_with("defguard-client.desktop")

    def test_read_wifi_and_signal(self):
        for out, want in [
            ("wifi:connected\nethernet:unavailable\n", "on"),
            ("wifi:disconnected\nethernet:connected\n", "wired"),
            ("wifi:disconnected\nloopback:connected (externally)\n", "off"),
            ("", "off"),
        ]:
            with self.subTest(out=out), mock.patch.object(ks.subprocess, "run", return_value=fake_completed(out)):
                self.assertEqual(ks.read_wifi(), want)
        with mock.patch.object(ks.subprocess, "run", side_effect=FileNotFoundError("nmcli")):
            self.assertIsNone(ks.read_wifi())
        with mock.patch.object(ks.subprocess, "run", return_value=fake_completed(" :30\n*:64\n")):
            self.assertEqual(ks.wifi_signal(), 64)
        with mock.patch.object(ks.subprocess, "run", return_value=fake_completed(" :30\n")):
            self.assertEqual(ks.wifi_signal(), 0)


class Bluetooth(unittest.TestCase):
    SHOW = "Controller 11:22:33:44:55:66 (public)\n\tName: laptop\n\tPowered: yes\n"
    DEVICES = (
        "Device AA:BB:CC:DD:EE:01 Keyboard\n"
        "Device AA:BB:CC:DD:EE:02 AA-BB-CC-DD-EE-02\n"
        "Device AA:BB:CC:DD:EE:03 Headphones\n"
        "Device AA:BB:CC:DD:EE:04 AA-BB-CC-DD-EE-04\n"
        "Device AA:BB:CC:DD:EE:05 Phone\n"
        "garbage line\n"
    )
    INFO = {
        "01": "Paired: yes\nConnected: no\nIcon: input-keyboard\n",
        "02": "Paired: no\nConnected: no\n",
        "03": "Paired: yes\nConnected: yes\nIcon: audio-headphones\n",
        "04": "Paired: yes\nConnected: no\n",
        "05": "Paired: no\nConnected: no\nIcon: phone\n",
    }

    def table(self, show=SHOW):
        t = {
            "bluetoothctl show": show,
            "bluetoothctl devices": self.DEVICES,
            "bluetoothctl devices Connected": "Device AA:BB:CC:DD:EE:03 Headphones\n",
        }
        for k, v in self.INFO.items():
            t[f"bluetoothctl info AA:BB:CC:DD:EE:{k}"] = v
        return t

    def test_state(self):
        p, fake = with_run(self.table())
        with p, mock.patch.object(ks.shutil, "which", return_value="/usr/bin/bluetoothctl"):
            st = ks.bluetooth_state()
        self.assertTrue(st["present"])
        self.assertTrue(st["on"])
        names = [d["name"] for d in st["devices"]]
        # connected first, then paired, then by name; a nameless stranger is left out
        self.assertEqual(names, ["Headphones", "AA:BB:CC:DD:EE:04", "Keyboard", "Phone"])
        hp = st["devices"][0]
        self.assertEqual(
            hp,
            {
                "mac": "AA:BB:CC:DD:EE:03",
                "name": "Headphones",
                "paired": True,
                "connected": True,
                "kind": "audio-headphones",
            },
        )
        self.assertEqual(st["devices"][1]["kind"], "")
        self.assertNotIn(["bluetoothctl", "--timeout", "8", "scan", "on"], fake.calls)

    def test_scan_only_when_on(self):
        p, fake = with_run(self.table())
        with p, mock.patch.object(ks.shutil, "which", return_value="/x"):
            ks.bluetooth_state(scan=True)
        self.assertIn(["bluetoothctl", "--timeout", "8", "scan", "on"], fake.calls)
        p, fake = with_run(self.table(show="Controller x\n\tPowered: no\n"))
        with p, mock.patch.object(ks.shutil, "which", return_value="/x"):
            self.assertFalse(ks.bluetooth_state(scan=True)["on"])
        self.assertNotIn(["bluetoothctl", "--timeout", "8", "scan", "on"], fake.calls)

    def test_absent(self):
        with mock.patch.object(ks.shutil, "which", return_value=None):
            self.assertEqual(ks.bluetooth_state(), {"present": False})
            self.assertEqual(ks.bluetooth_icon_state(), "none")
        p, _ = with_run({"bluetoothctl show": "No default controller available\n"})
        with p, mock.patch.object(ks.shutil, "which", return_value="/x"):
            self.assertEqual(ks.bluetooth_state(), {"present": False})
            self.assertEqual(ks.bluetooth_icon_state(), "none")

    def test_icon_state(self):
        with mock.patch.object(ks.shutil, "which", return_value="/x"):
            p, _ = with_run(self.table())
            with p:
                self.assertEqual(ks.bluetooth_icon_state(), "connected")
            p, _ = with_run({**self.table(), "bluetoothctl devices Connected": ""})
            with p:
                self.assertEqual(ks.bluetooth_icon_state(), "on")
            p, _ = with_run(self.table(show="Controller x\n\tPowered: no\n"))
            with p:
                self.assertEqual(ks.bluetooth_icon_state(), "off")


XRANDR = """Screen 0: minimum 8 x 8, current 4480 x 1440, maximum 32767 x 32767
eDP-1 connected primary 2560x1600+0+0 (normal left inverted right x axis y axis) 286mm x 179mm
   2560x1600     59.97*+
   1920x1200     59.95
   1680x1050     59.88
   1280x800      59.81
   1024x768      60.00
   800x600       60.32
DP-1 disconnected (normal left inverted right x axis y axis)
HDMI-1 connected 1920x1080+2560+0 (normal left inverted right x axis y axis) 527mm x 296mm
   1920x1080     60.00*+  50.00    59.94
   1920x1080i    60.00    50.00
   1680x1050     59.88
   1280x1024     60.02
   1280x720      60.00    50.00
   1024x768      60.00
HDMI-2 connected (normal left inverted right x axis y axis)
   3840x2160     30.00 +
   2560x1440     59.95
"""


class Displays(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        desk = mock.patch.object(ks, "DESKTOP", Path(tmp.name) / "desktop.json")
        desk.start()
        self.addCleanup(desk.stop)

    def test_read(self):
        ks.DESKTOP.write_text('{"dpi": 144}')
        p, _ = with_run({"xrandr --query": XRANDR})
        with p:
            d = ks.read_displays()
        self.assertEqual(d["dpi"], 144)
        outs = {o["name"]: o for o in d["outputs"]}
        self.assertEqual(list(outs), ["eDP-1", "HDMI-1", "HDMI-2"])
        e = outs["eDP-1"]
        self.assertTrue(e["primary"] and e["active"] and e["builtin"])
        self.assertEqual(e["current"], "2560x1600")
        self.assertEqual(e["modes"], ["2560x1600", "1920x1200", "1680x1050", "1280x800"])  # same shape, >= 1280
        h = outs["HDMI-1"]
        self.assertFalse(h["primary"] or h["builtin"])
        self.assertEqual(h["x"], 2560)
        self.assertEqual(h["current"], "1920x1080")
        self.assertEqual(h["modes"], ["1920x1080", "1280x720"])  # 1920x1080i is the same size
        off = outs["HDMI-2"]
        self.assertFalse(off["active"])
        self.assertIsNone(off["current"])
        self.assertEqual(off["modes"], ["3840x2160", "2560x1440"])

    def test_dpi_fallback(self):
        for dpi in ["", "{}", '{"dpi": -1}', '{"dpi": "abc"}', '{"dpi": 0}']:
            ks.DESKTOP.write_text(dpi)
            p, _ = with_run({"xrandr --query": ""})
            with self.subTest(dpi=dpi), p:
                self.assertEqual(ks.read_displays(), {"outputs": [], "dpi": 96})

    def test_arrange(self):
        p, fake = with_run({"xrandr --query": XRANDR, "xfconf-query": "96"})
        with p:
            ks.write_display({"arrange": "left"})
        self.assertEqual(
            fake.calls[-1],
            [
                "xrandr",
                "--output",
                "eDP-1",
                "--auto",
                "--primary",
                "--output",
                "HDMI-1",
                "--auto",
                "--left-of",
                "eDP-1",
            ],
        )
        p, fake = with_run({"xrandr --query": XRANDR, "xfconf-query": "96"})
        with p:
            ks.write_display({"arrange": "sideways"})
        self.assertFalse(any(c[:2] == ["xrandr", "--output"] for c in fake.calls))

    def test_set_dpi_keeps_the_rest(self):
        ks.DESKTOP.write_text('{"pointers": {"Mouse": {"libinput Accel Speed": -0.2}}}')
        p, fake = with_run({})
        with p:
            ks.write_display({"dpi": 120.0})
        self.assertEqual(fake.calls, [])
        saved = json.loads(ks.DESKTOP.read_text())
        self.assertEqual(saved["dpi"], 120)
        self.assertEqual(saved["pointers"]["Mouse"]["libinput Accel Speed"], -0.2)


class Power(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        desk = mock.patch.object(ks, "DESKTOP", Path(tmp.name) / "desktop.json")
        desk.start()
        self.addCleanup(desk.stop)

    def test_read_power(self):
        self.assertEqual(
            ks.read_power(), {"screen_off": 10, "sleep_after": 0, "lid": 1, "charge": 80, "full_once": 0}
        )  # nothing chosen yet
        ks.DESKTOP.write_text('{"power": {"screen_off": 15, "sleep_after": 10, "lid": "oops"}}')
        self.assertEqual(ks.read_power(), {"screen_off": 15, "sleep_after": 0, "lid": 1, "charge": 80, "full_once": 0})
        ks.DESKTOP.write_text('{"power": {"sleep_after": 30}}')
        self.assertEqual(ks.read_power(), {"screen_off": 10, "sleep_after": 30, "lid": 1, "charge": 80, "full_once": 0})

    def test_write_power(self):
        ks.DESKTOP.write_text('{"dpi": 120}')
        ks.write_power("lid", 3)
        ks.write_power("unknown", 3)
        ks.write_power("screen_off", -5)
        saved = json.loads(ks.DESKTOP.read_text())
        self.assertEqual(saved, {"dpi": 120, "power": {"lid": 3, "screen_off": 0}})  # clamped: never off


class Time(unittest.TestCase):
    def test_read_time(self):
        p, _ = with_run({"timedatectl show": "Timezone=Europe/Lisbon\nNTP=yes\nNTPSynchronized=no\nCanNTP=yes\n"})
        with p:
            t = ks.read_time()
        self.assertEqual((t["auto"], t["synced"], t["can_auto"], t["zone"]), (True, False, True, "Europe/Lisbon"))

    def test_write_time_validates(self):
        p, fake = with_run({"timedatectl": ""})
        with p:
            ks.write_time("time_zone", "America/Argentina/Buenos_Aires")
            ks.write_time("time_zone", "Europe/Lisbon; rm -rf ~")
            ks.write_time("time_zone", "../etc")
            ks.write_time("time_set", "2026-11-12T08:30")
            ks.write_time("time_set", "tomorrow")
            ks.write_time("time_auto", "true")
        self.assertEqual(
            fake.calls,
            [
                ["timedatectl", "set-timezone", "America/Argentina/Buenos_Aires"],
                ["timedatectl", "set-ntp", "false"],
                ["timedatectl", "set-time", "2026-11-12 08:30:00"],
                ["timedatectl", "set-ntp", "true"],
            ],
        )


class Battery(TempDirCase):
    def bat(self, **files):
        d = self.tmp / "BAT0"
        d.mkdir(exist_ok=True)
        for k, v in files.items():
            (d / k).write_text(v)
        return d

    def test_against_todays_capacity(self):
        d = self.bat(energy_now="40000000", energy_full="50000000", capacity="60", status="Discharging")
        with (
            mock.patch.object(ks, "find_battery", return_value=d),
            mock.patch.object(ks, "on_mains", return_value=False),
        ):
            self.assertEqual(ks.read_battery(), {"level": 80, "charging": False, "limit": 100})

    def test_plugged_in_at_a_charge_limit(self):
        d = self.bat(capacity="80", status="Not charging")
        with (
            mock.patch.object(ks, "find_battery", return_value=d),
            mock.patch.object(ks, "on_mains", return_value=True),
        ):
            self.assertEqual(ks.read_battery(), {"level": 80, "charging": True, "limit": 100})
        (d / "charge_control_end_threshold").write_text("80\n")
        with (
            mock.patch.object(ks, "find_battery", return_value=d),
            mock.patch.object(ks, "on_mains", return_value=True),
        ):
            self.assertEqual(ks.read_battery()["limit"], 80)

    def test_capacity_fallback_and_full(self):
        d = self.bat(capacity="104\n", status="Full\n")
        with mock.patch.object(ks, "find_battery", return_value=d):
            self.assertEqual(ks.read_battery(), {"level": 100, "charging": True, "limit": 100})

    def test_no_battery(self):
        with mock.patch.object(ks, "find_battery", return_value=None):
            self.assertIsNone(ks.read_battery())
        with mock.patch.object(ks, "find_battery", return_value=self.tmp / "gone"):
            self.assertIsNone(ks.read_battery())

    def test_details(self):
        info = """  native-path:          BAT0
  vendor:               ACME
  model:                kuhaku-test-model-xyz
  battery
    present:             yes
    state:               discharging
    energy:              30,5 Wh
    energy-full:         45 Wh
    energy-full-design:  50 Wh
    energy-rate:         7.2 W
    voltage:             11.4 V
    charge-cycles:       0
    time to empty:       4,2 hours
    percentage:          68%
    temperature:         31.5 degrees C
    technology:          lithium-polymer
  History (charge):
    1700000000	68.000	discharging
"""
        p, _ = with_run(
            {
                "upower -e": "/org/freedesktop/UPower/devices/line_power_AC\n"
                "/org/freedesktop/UPower/devices/battery_BAT0\n"
                "/org/freedesktop/UPower/devices/DisplayDevice\n",
                "upower -i /org/freedesktop/UPower/devices/battery_BAT0": info,
                "powerprofilesctl list": "  performance:\n    Driver: x\n\n* balanced:\n    Driver: x\n\n"
                "  power-saver:\n    Driver: x\n",
            }
        )
        with p:
            d = ks.battery_details()
        self.assertEqual(d["level"], 68.0)
        self.assertEqual(d["state"], "discharging")
        self.assertEqual(d["energy"], 30.5)
        self.assertEqual(d["health"], 90)
        self.assertEqual(d["toEmpty"], 252)
        self.assertIsNone(d["toFull"])
        self.assertIsNone(d["cycles"])
        self.assertEqual(d["temperature"], 31.5)
        self.assertEqual(d["name"], "ACME kuhaku-test-model-xyz")
        self.assertEqual(d["technology"], "lithium polymer")
        self.assertEqual(d["profiles"], ["performance", "balanced", "power-saver"])
        self.assertEqual(d["profile"], "balanced")
        self.assertEqual(d["history"], [])

    def test_details_without_battery(self):
        p, _ = with_run({"upower -e": "/org/freedesktop/UPower/devices/DisplayDevice\n"})
        with p:
            self.assertIsNone(ks.battery_details())


class Describe(unittest.TestCase):
    def test_short(self):
        self.assertEqual(ks.short("  a\n  b  "), "a b")
        self.assertEqual(ks.short(None), "")
        self.assertEqual(ks.short("x" * 10, 5), "xxxx…")
        self.assertEqual(len(ks.short("y" * 500)), 140)

    def test_steps(self):
        ev = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "  Let me look.  "},
                    {"type": "text", "text": "   "},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la", "description": "List files"}},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/a/b/notes.txt"}},
                    {"type": "tool_use", "name": "Grep", "input": {"pattern": "TODO"}},
                    {"type": "tool_use", "name": "Glob", "input": {}},
                    {"type": "tool_use", "name": "mcp__claude_ai_Gmail__search_threads", "input": {}},
                    {"type": "tool_use", "name": "Agent", "input": {}},
                ]
            },
        }
        self.assertEqual(
            [s["text"] for s in ks.describe(ev)],
            [
                "Let me look.",
                "List files",
                "ls",
                "Reading notes.txt",
                "Searching for “TODO”",
                "Looking for files",
                "claude ai Gmail search threads",
                "Asking a helper",
            ],
        )
        self.assertEqual(ks.describe(ev)[0]["kind"], "thought")
        self.assertEqual(ks.describe({"type": "user"}), [])
        self.assertEqual(ks.describe({"type": "assistant"}), [])


class Limits(TempDirCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(ks, "LIMITS_FILE", self.tmp / "plan-limits.json")
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(ks, "_limits_cache", [0, None])
        p.start()
        self.addCleanup(p.stop)

    def test_note_and_read_back(self):
        out = ks.note_limits(
            {
                "unifiedWindows": {
                    "five_hour": {"utilization": 0.25, "resetsAt": 1800000000},
                    "seven_day": {"utilization": 63},
                    "other": {"utilization": 1},
                    "seven_day_opus": {"utilization": None},
                }
            }
        )
        self.assertEqual(out["session"]["percent"], 25)
        self.assertEqual(out["session"]["resets"], datetime.fromtimestamp(1800000000, UTC).isoformat())
        self.assertEqual(out["week"], {"percent": 63, "resets": None})
        self.assertEqual(set(out), {"session", "week"})
        self.assertEqual(json.loads((self.tmp / "plan-limits.json").read_text()), out)
        ks._limits_cache[:] = [0, None]
        self.assertEqual(ks.plan_limits(), out)  # after a restart, from the file

    def test_nothing_to_note(self):
        self.assertIsNone(ks.note_limits(None))
        self.assertIsNone(ks.note_limits({"unifiedWindows": {}}))
        self.assertIsNone(ks.plan_limits())


class Tokens(TempDirCase):
    def test_daily_tokens(self):
        proj = self.tmp / "projects/p"
        proj.mkdir(parents=True)

        def line(mid, rid, ts, out, inp, created, read):
            return json.dumps(
                {
                    "type": "assistant",
                    "timestamp": ts,
                    "requestId": rid,
                    "message": {
                        "id": mid,
                        "usage": {
                            "output_tokens": out,
                            "input_tokens": inp,
                            "cache_creation_input_tokens": created,
                            "cache_read_input_tokens": read,
                        },
                    },
                }
            )

        (proj / "a.jsonl").write_text(
            "\n".join(
                [
                    line("m1", "r1", "2026-11-10T12:00:00Z", 10, 5, 1, 100),
                    line("m1", "r1", "2026-11-10T12:00:00Z", 10, 5, 1, 100),  # the same reply twice: once
                    line("m2", "r2", "2026-11-10T13:00:00Z", 20, 0, 0, 0),
                    '{"type": "assistant", "usage": "broken"',
                    '{"type": "user", "message": {"content": "hi"}}',
                ]
            )
        )
        (proj / "b.jsonl").write_text(
            line("m1", "r1", "2026-11-10T12:00:00Z", 10, 5, 1, 100)
            + "\n"
            + line("m3", "r3", "2026-11-11T12:00:00Z", 1, 1, 1, 1)
        )
        with (
            mock.patch.object(ks.accounts, "config_dir", return_value=self.tmp),
            mock.patch.dict(ks._token_cache, clear=True),
        ):
            days = ks.daily_tokens()
        day1 = datetime.fromisoformat("2026-11-10T12:00:00+00:00").astimezone().strftime("%Y-%m-%d")
        day3 = datetime.fromisoformat("2026-11-11T12:00:00+00:00").astimezone().strftime("%Y-%m-%d")
        self.assertEqual(days[day1], {"written": 30, "fresh": 6, "cached": 100, "replies": 2})
        self.assertEqual(days[day3], {"written": 1, "fresh": 2, "cached": 1, "replies": 1})


class LoadJson(TempDirCase):
    def test_defaults(self):
        f = self.tmp / "c.json"
        self.assertEqual(ks.load_json(f, {"a": 1}), {"a": 1})
        f.write_text('{"b": 2}')
        self.assertEqual(ks.load_json(f, {"a": 1}), {"a": 1, "b": 2})


if __name__ == "__main__":
    unittest.main()


class OpenedTest(unittest.TestCase):
    """opened(): what Claude put on screen or made, so the answer can offer it again."""

    def use(self, name, **inp):
        return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}

    def bash(self, command):
        return ks.opened(self.use("Bash", command=command))

    def test_pages_files_and_folders(self):
        self.assertEqual(
            self.bash('setsid -f mado "https://example.com/a b" >/dev/null 2>&1'),
            [{"app": "mado", "url": "https://example.com/a b"}],
        )
        self.assertEqual(
            self.bash("export DISPLAY=:0; setsid -f mado file:///tmp/x.html; sleep 4"),
            [{"app": "mado", "url": "file:///tmp/x.html"}],
        )
        self.assertEqual(self.bash("mado '/tmp/my page.html'"), [{"app": "mado", "url": "file:///tmp/my%20page.html"}])
        self.assertEqual(self.bash("setsid -f miru /srv/photos"), [{"app": "miru", "url": "file:///srv/photos"}])
        self.assertEqual(self.bash("xdg-open /srv/a.pdf"), [{"app": "", "url": "file:///srv/a.pdf"}])
        self.assertEqual(self.bash("gio open /srv/b.png"), [{"app": "", "url": "file:///srv/b.png"}])

    def test_apps_on_their_own(self):
        self.assertEqual(self.bash("setsid -f tegami >/dev/null 2>&1"), [{"app": "tegami", "url": ""}])
        self.assertEqual(self.bash("nohup python3 x.py &"), [])

    def test_files_made(self):
        home = str(ks.HOME)
        self.assertEqual(
            ks.opened(self.use("Write", file_path=home + "/Documents/k.html")),
            [{"app": "", "url": "file://" + home + "/Documents/k.html"}],
        )
        for p in (home + "/Projects/a/b.py", home + "/.config/x", "/tmp/a"):
            self.assertEqual(ks.opened(self.use("Write", file_path=p)), [])

    def test_nothing_else(self):
        self.assertEqual(self.bash("echo hi"), [])
        self.assertEqual(self.bash("ls /tmp && grep x /etc/hosts"), [])
        self.assertEqual(self.bash("mado 'unclosed"), [])
        self.assertEqual(ks.opened({"type": "user"}), [])
