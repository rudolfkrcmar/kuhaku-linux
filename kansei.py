#!/usr/bin/env python3
"""Kansei (管制, "mission control"): everything this computer is doing, live.

Processor, memory, disks, network, battery, temperatures, every running
program (with what it costs in memory, processor and disk), network
connections, services, start-up time and a security check. Programs can be
stopped from here. Lives under the Terminal (its bar has a Kansei button) and
as `kansei`.
"""
import json
import os
import pwd
import re
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

import psutil
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GLib, Gtk, WebKit2

HERE = Path(__file__).resolve().parent
HOME = Path.home()
ME = os.getuid()


def theme():
    def load(p):
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    cfg = load(HOME / ".config/kuhaku/config.json")
    st = load(HOME / ".local/state/kuhaku/state.json")
    choice = st.get("theme") or cfg.get("theme", "auto")
    if choice in ("day", "night"):
        return choice
    h = time.localtime().tm_hour
    return "night" if (h >= cfg.get("night_from", 20) or h < cfg.get("night_until", 7)) else "day"


def sh(argv, timeout=8):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env={**os.environ, "LC_ALL": "C"})
        return r.stdout
    except Exception:
        return ""


def read(p, default=""):
    try:
        return Path(p).read_text().strip()
    except Exception:
        return default


def app_names():
    """Executable -> friendly app name, from the installed app entries."""
    names = {}
    for d in (Path("/usr/share/applications"), HOME / ".local/share/applications"):
        for f in d.glob("*.desktop"):
            try:
                txt = f.read_text(errors="replace")
            except Exception:
                continue
            name = re.search(r"^Name=(.+)$", txt, re.M)
            ex = re.search(r"^Exec=(.+)$", txt, re.M)
            if name and ex:
                for tok in ex.group(1).split():
                    if "=" in tok and not tok.startswith("/"):
                        continue
                    names.setdefault(os.path.basename(tok.strip('"')), name.group(1))
                    break
    return names


class Collector:
    """Samples the machine once a second; heavier things (services, security, disk use) on request."""

    def __init__(self):
        self.prev_net = psutil.net_io_counters(pernic=True)
        self.prev_disk = psutil.disk_io_counters()
        self.prev_t = time.time()
        self.prev_io = {}
        self.names = app_names()
        psutil.cpu_percent(percpu=True)
        for p in psutil.process_iter():
            try:
                p.cpu_percent(None)
            except Exception:
                pass

    def sample(self):
        now = time.time()
        dt = max(0.2, now - self.prev_t)
        self.prev_t = now
        cores = psutil.cpu_percent(percpu=True)
        freq = psutil.cpu_freq(percpu=True) or []
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        net = psutil.net_io_counters(pernic=True)
        nets = []
        for nic, c in net.items():
            if nic == "lo":
                continue
            p = self.prev_net.get(nic, c)
            nets.append({"nic": nic, "rx": (c.bytes_recv - p.bytes_recv) / dt, "tx": (c.bytes_sent - p.bytes_sent) / dt,
                         "rxTotal": c.bytes_recv, "txTotal": c.bytes_sent})
        self.prev_net = net
        d = psutil.disk_io_counters()
        disk_rate = {"read": (d.read_bytes - self.prev_disk.read_bytes) / dt, "write": (d.write_bytes - self.prev_disk.write_bytes) / dt}
        self.prev_disk = d
        temps = []
        nice = {"coretemp": "Processor", "nvme": "SSD", "acpitz": "Board", "iwlwifi_1": "Wi-Fi", "BAT0": "Battery"}
        for chip, entries in (psutil.sensors_temperatures() or {}).items():
            if chip == "applesmc":
                continue            # Apple's sensor codes (TC0P …) mean little without a key
            for e in entries:
                if not (-20 < e.current < 130):
                    continue
                base = "Chipset" if chip.startswith("pch") else nice.get(chip, chip)
                lab = e.label or ""
                lab = {"Composite": "", "Package id 0": "package"}.get(lab, lab)
                temps.append({"chip": chip, "label": (base + (" " + lab if lab else "")).strip(), "c": e.current,
                              "high": e.high or e.critical or 100})
        bat = psutil.sensors_battery()
        bdir = next((p for p in sorted(Path("/sys/class/power_supply").glob("*"))
                     if (p / "type").exists() and (p / "type").read_text().strip() == "Battery"),
                    Path("/sys/class/power_supply/BAT0"))
        watts = None
        try:
            if (bdir / "power_now").exists():
                watts = int(read(bdir / "power_now", "0")) / 1e6
            else:
                watts = int(read(bdir / "current_now", "0")) * int(read(bdir / "voltage_now", "0")) / 1e12
        except Exception:
            pass
        battery = None
        if bat:
            full, design = read(bdir / "energy_full") or read(bdir / "charge_full"), read(bdir / "energy_full_design") or read(bdir / "charge_full_design")
            battery = {"pct": bat.percent, "plugged": bat.power_plugged,
                       "left": None if bat.secsleft in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) else bat.secsleft,
                       "watts": watts, "cycles": read(bdir / "cycle_count") or None,
                       "health": round(100 * int(full) / int(design)) if full and design and int(design) else None,
                       "status": read(bdir / "status")}
        load = os.getloadavg()
        return {
            "t": now, "cpu": sum(cores) / len(cores), "cores": cores,
            "freq": [round(f.current) for f in freq], "fmax": round(freq[0].max) if freq and freq[0].max else None,
            "load": load, "ctx": psutil.cpu_stats().ctx_switches, "procs": len(psutil.pids()),
            "mem": {"total": vm.total, "used": vm.total - vm.available, "cache": getattr(vm, "cached", 0) + getattr(vm, "buffers", 0),
                    "avail": vm.available, "pct": vm.percent, "swapTotal": sw.total, "swapUsed": sw.used},
            "net": nets, "disk": disk_rate, "temps": temps, "battery": battery,
            "uptime": now - psutil.boot_time(),
        }

    def processes(self):
        out, now = [], time.time()
        for p in psutil.process_iter(["pid", "ppid", "name", "username", "uids", "memory_info", "num_threads", "cmdline", "exe", "create_time", "status"]):
            try:
                i = p.info
                cpu = p.cpu_percent(None)
                rd = wr = 0
                if i["uids"] and i["uids"].real == ME:
                    try:
                        io = p.io_counters()
                        last = self.prev_io.get(i["pid"])
                        if last:
                            rd = max(0, (io.read_bytes - last[0]) / max(0.2, now - last[2]))
                            wr = max(0, (io.write_bytes - last[1]) / max(0.2, now - last[2]))
                        self.prev_io[i["pid"]] = (io.read_bytes, io.write_bytes, now)
                    except Exception:
                        pass
                exe = os.path.basename(i["exe"] or "") or i["name"]
                cmd = " ".join(i["cmdline"] or [])[:300]
                app = None
                if "python" in exe and i["cmdline"] and len(i["cmdline"]) > 1:
                    app = {"kuhaku.py": "Kūhaku", "browser.py": "Mado", "miru.py": "Miru", "tegami.py": "Tegami",
                           "tsukuru.py": "Tsukuru", "terminal.py": "Terminal", "kansei.py": "Kansei",
                           "kuhaku-gestures.py": "Kūhaku gestures",
                           "kuhaku-tablet.py": "Kūhaku keyboard"}.get(os.path.basename(i["cmdline"][1]))
                if not app and "python" in exe and i["cmdline"] and len(i["cmdline"]) > 1:
                    app = "Python: " + os.path.basename(i["cmdline"][1])
                app = app or self.names.get(exe) or self.names.get(i["name"])
                if not app and "WebKit" in i["name"]:
                    app = "Web engine"
                if not app and not i["cmdline"] and (i["ppid"] == 2 or i["pid"] == 2):
                    app = "Linux kernel"
                out.append({"pid": i["pid"], "ppid": i["ppid"], "name": i["name"], "app": app or "", "user": i["username"] or "",
                            "mine": bool(i["uids"] and i["uids"].real == ME), "cpu": cpu,
                            "rss": i["memory_info"].rss if i["memory_info"] else 0, "threads": i["num_threads"] or 0,
                            "read": rd, "write": wr, "cmd": cmd, "age": now - (i["create_time"] or now), "status": i["status"]})
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        alive = {p["pid"] for p in out}
        self.prev_io = {k: v for k, v in self.prev_io.items() if k in alive}
        return out

    def connections(self):
        names = {}
        rows = []
        try:
            conns = psutil.net_connections(kind="inet")
        except Exception:
            conns = []
        for c in conns:
            if not c.raddr and c.status != "LISTEN":
                continue
            name = ""
            if c.pid:
                if c.pid not in names:
                    try:
                        names[c.pid] = psutil.Process(c.pid).name()
                    except Exception:
                        names[c.pid] = ""
                name = names[c.pid]
            rows.append({"pid": c.pid, "name": name, "proto": "tcp" if c.type == socket.SOCK_STREAM else "udp",
                         "local": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "",
                         "remote": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "", "rip": c.raddr.ip if c.raddr else "",
                         "status": c.status, "listen": c.status == "LISTEN" or (not c.raddr and c.type == socket.SOCK_DGRAM)})
        return rows

    def wifi(self):
        info = {}
        out = sh(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,FREQ,RATE,SECURITY,CHAN", "dev", "wifi", "list", "--rescan", "no"])
        for line in out.splitlines():
            parts = re.split(r"(?<!\\):", line)
            if parts and parts[0] == "yes":
                info = {"ssid": parts[1].replace("\\:", ":"), "signal": parts[2], "freq": parts[3], "rate": parts[4],
                        "security": parts[5], "channel": parts[6] if len(parts) > 6 else ""}
        dev = sh(["nmcli", "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,GENERAL.HWADDR,GENERAL.DEVICE", "dev", "show"])
        cur = {}
        for line in dev.splitlines():
            k, _, v = line.partition(":")
            if k == "GENERAL.DEVICE":
                cur = {"device": v}
            if k.startswith("IP4.ADDRESS") and "ip" not in info and v and not v.startswith("127."):
                info["ip"], info["device"] = v, cur.get("device", "")
            if k.startswith("IP4.GATEWAY") and v and "gateway" not in info:
                info["gateway"] = v
            if k.startswith("IP4.DNS") and v:
                info.setdefault("dns", []).append(v)
        return info

    def system(self):
        os_name = re.search(r'PRETTY_NAME="([^"]+)"', read("/etc/os-release"))
        cpu = re.search(r"model name\s*:\s*(.+)", read("/proc/cpuinfo"))
        blame = []
        for line in sh(["systemd-analyze", "blame", "--no-pager"]).splitlines()[:12]:
            m = re.match(r"\s*([\d.]+(?:ms|s|min))\s+(.+)", line)
            if m:
                blame.append({"time": m.group(1), "unit": m.group(2)})
        boot = sh(["systemd-analyze", "--no-pager"]).splitlines()[:1]
        services = []
        units = sh(["systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--plain", "--no-pager"])
        names = [l.split()[0] for l in units.splitlines() if l.strip()]
        if names:
            shown = sh(["systemctl", "show", "-p", "Id,MemoryCurrent,Description,MainPID", *names])
            for block in shown.strip().split("\n\n"):
                kv = dict(l.split("=", 1) for l in block.splitlines() if "=" in l)
                mem = kv.get("MemoryCurrent", "")
                m = int(mem) if mem.isdigit() else None
                if m is None and kv.get("MainPID", "0").isdigit() and int(kv["MainPID"]) > 0:
                    try:
                        m = psutil.Process(int(kv["MainPID"])).memory_info().rss
                    except Exception:
                        pass
                services.append({"unit": kv.get("Id", ""), "desc": kv.get("Description", ""), "pid": kv.get("MainPID", ""), "mem": m})
        services.sort(key=lambda s: -(s["mem"] or 0))
        user_units = sh(["systemctl", "--user", "list-units", "--type=service", "--state=running", "--no-legend", "--plain", "--no-pager"])
        mounts = []
        for part in psutil.disk_partitions():
            if part.fstype in ("squashfs", "tmpfs", "devtmpfs", "overlay") or part.mountpoint.startswith("/snap"):
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
                mounts.append({"mount": part.mountpoint, "device": part.device, "fs": part.fstype, "total": u.total, "used": u.used, "pct": u.percent})
            except Exception:
                pass
        return {"host": socket.gethostname(), "os": os_name.group(1) if os_name else "Linux", "kernel": os.uname().release,
                "cpu": re.sub(r"\s+", " ", cpu.group(1)) if cpu else "", "cores": psutil.cpu_count(), "threads": psutil.cpu_count(),
                "model": read("/sys/class/dmi/id/product_name"), "firmware": read("/sys/class/dmi/id/bios_version"),
                "boot": boot[0] if boot else "", "blame": blame, "services": services,
                "userServices": len([l for l in user_units.splitlines() if l.strip()]), "mounts": mounts,
                "user": pwd.getpwuid(ME).pw_name}

    def disk_use(self):
        """What fills the home folder: the biggest folders, two levels deep."""
        out = sh(["du", "-x", "-b", "-d", "2", str(HOME)], timeout=120)
        rows = []
        for line in out.splitlines():
            size, _, path = line.partition("\t")
            if path and path != str(HOME) and size.isdigit():
                rows.append({"path": path.replace(str(HOME), "~", 1), "size": int(size), "depth": path[len(str(HOME)):].count("/")})
        rows.sort(key=lambda r: -r["size"])
        return rows[:40]

    def security(self):
        checks = []

        def add(name, ok, detail, level="warn"):
            checks.append({"name": name, "ok": ok, "detail": detail, "level": "ok" if ok else level})
        ufw = sh(["systemctl", "is-active", "ufw"]).strip()
        ufw_conf = read("/etc/ufw/ufw.conf")
        fw_on = ufw == "active" and "ENABLED=yes" in ufw_conf
        checks.append({"name": "Firewall", "ok": fw_on, "level": "ok" if fw_on else "warn", "fix": None if fw_on else "firewall",
                       "detail": "On: nothing from the network reaches this computer unasked" if fw_on
                       else "Off. Turn it on so nothing from the network can reach this computer unasked."})
        listening = [c for c in self.connections() if c["listen"]]
        exposed = sorted({(c["local"].rsplit(":", 1)[-1], c["name"] or "?") for c in listening
                          if not c["local"].startswith(("127.", "::1", "[::1]")) and not c["local"].startswith("::ffff:127")})
        add("Open ports", not exposed, "None open to the network" if not exposed else
            "Listening to the network: " + ", ".join(f"{p} ({n})" for p, n in exposed[:8]))
        auto = Path("/etc/apt/apt.conf.d/52kuhaku-unattended").exists() or "Unattended-Upgrade \"1\"" in read("/etc/apt/apt.conf.d/20auto-upgrades")
        add("Automatic security updates", auto, "Security fixes install by themselves" if auto else "Not set up", "bad")
        reboot = Path("/var/run/reboot-required").exists()
        add("Restart pending", not reboot, "Nothing waiting" if not reboot else "An update waits for a restart to take effect")
        pending = sh(["apt-get", "-s", "-o", "Debug::NoLocking=1", "upgrade"], timeout=30)
        n_up = len([l for l in pending.splitlines() if l.startswith("Inst ")])
        n_sec = len([l for l in pending.splitlines() if l.startswith("Inst ") and "security" in l])
        add("Updates waiting", n_sec == 0, f"{n_up} updates waiting, {n_sec} of them security" if n_up else "Everything is up to date")
        enc = any(p.fstype in ("ecryptfs", "crypto_LUKS") for p in psutil.disk_partitions(all=True)) or "crypt" in sh(["lsblk", "-o", "TYPE"])
        add("Home folder encrypted", enc, "Your files are encrypted on disk" if enc else "Files are stored unencrypted", "bad")
        sb = sh(["mokutil", "--sb-state"]).strip()
        add("Secure Boot", "enabled" in sb.lower(), sb or "Unknown (this Mac starts without UEFI Secure Boot)", "info")
        sshd = sh(["systemctl", "is-active", "ssh"]).strip() == "active" or sh(["systemctl", "is-active", "sshd"]).strip() == "active"
        add("Remote login (SSH)", not sshd, "Off" if not sshd else "On: people can log in over the network")
        admins = read("/etc/group")
        sudo = re.search(r"^sudo:[^:]*:[^:]*:(.*)$", admins, re.M)
        add("Administrators", True, ", ".join(filter(None, (sudo.group(1) if sudo else "").split(","))) or "none", "info")
        fails = sh(["journalctl", "-q", "--since", "-7d", "--no-pager", "-g", "authentication failure|Failed password"], timeout=20)
        nf = len(fails.splitlines())
        add("Failed logins (7 days)", nf < 5, f"{nf} failed password attempts" if nf else "None")
        guest = Path("/etc/lightdm/lightdm.conf.d").exists() and "allow-guest=true" in sh(["sh", "-c", "cat /etc/lightdm/lightdm.conf.d/* 2>/dev/null"])
        add("Guest account", not guest, "Off" if not guest else "On: anyone can log in as a guest")
        return checks


class Kansei:
    def __init__(self):
        self.night = theme() == "night"
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", self.night)
        self.win = Gtk.Window(title="Kansei")
        self.win.set_decorated(False)
        self.win.set_icon_name("kansei")
        self.win.maximize()
        self.win.connect("destroy", Gtk.main_quit)
        ucm = WebKit2.UserContentManager()
        ucm.register_script_message_handler("kansei")
        ucm.connect("script-message-received::kansei", self.on_message)
        self.view = WebKit2.WebView.new_with_user_content_manager(ucm)
        self.view.set_zoom_level(1.12)  # app type a little larger than the page's own, for reading
        st = self.view.get_settings()
        st.set_enable_write_console_messages_to_stdout(bool(os.environ.get("KANSEI_DEBUG")))
        bg = (0.071, 0.071, 0.071) if self.night else (0.969, 0.957, 0.945)
        self.view.set_background_color(Gdk.RGBA(*bg, 1))
        self.view.connect("context-menu", lambda *a: True)
        self.win.add(self.view)
        self.view.load_uri((HERE / "kansei" / "kansei.html").as_uri() + f"?theme={'night' if self.night else 'day'}")
        self.view.connect("load-changed", lambda v, e: e == WebKit2.LoadEvent.FINISHED and self.start())
        self.win.show_all()
        self.c = Collector()
        self.tab = "overview"
        self.started = False

    def js(self, fn, payload):
        self.view.evaluate_javascript(f"{fn}({json.dumps(payload)})", -1, None, None, None, None, None)

    def start(self):
        if self.started:
            return
        self.started = True
        self.background("system", self.c.system)
        GLib.timeout_add(1000, self.tick)
        self.tick()

    def tick(self):
        if not self.win.get_visible():
            return True
        self.js("sample", self.c.sample())
        if self.tab in ("overview", "processes"):
            self.js("processes", self.c.processes())
        # Network details take a moment (nmcli): every 3 s, off the screen's thread.
        if self.tab == "network" and not getattr(self, "net_busy", False) and time.time() - getattr(self, "net_at", 0) > 3:
            self.net_busy, self.net_at = True, time.time()

            def work():
                data = {"conns": self.c.connections(), "wifi": self.c.wifi()}
                self.net_busy = False
                GLib.idle_add(lambda: self.js("got", {"kind": "network", "data": data}) or False)
            threading.Thread(target=work, daemon=True).start()
        return True

    def background(self, kind, fn):
        def work():
            data = fn()
            GLib.idle_add(lambda: self.js("got", {"kind": kind, "data": data}) or False)
        threading.Thread(target=work, daemon=True).start()

    def on_message(self, _m, result):
        try:
            msg = json.loads(result.get_js_value().to_string())
        except Exception:
            return
        k = msg.get("type")
        if k == "tab":
            self.tab = msg.get("tab", "overview")
            if self.tab == "network":
                self.background("network", lambda: {"conns": self.c.connections(), "wifi": self.c.wifi()})
            if self.tab == "system":
                self.background("system", self.c.system)
            if self.tab == "security":
                self.background("security", self.c.security)
            if self.tab == "disk":
                self.background("system", self.c.system)
                self.background("diskuse", self.c.disk_use)
        elif k == "fix" and msg.get("what") == "firewall":
            def work():
                r = subprocess.run(["pkexec", "sh", "-c", "ufw default deny incoming && ufw default allow outgoing && ufw --force enable"],
                                   capture_output=True, text=True)
                GLib.idle_add(self.js, "toast", "Firewall on" if r.returncode == 0 else "Firewall not changed")
                data = self.c.security()
                GLib.idle_add(lambda: self.js("got", {"kind": "security", "data": data}) or False)
            threading.Thread(target=work, daemon=True).start()
        elif k == "kill":
            self.kill(int(msg.get("pid", 0)), bool(msg.get("force")))
        elif k == "close":
            self.win.destroy()
        elif k == "minimize":
            self.win.iconify()

    def kill(self, pid, force):
        if pid <= 1 or pid == os.getpid():
            return
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            name = psutil.Process(pid).name()
            os.kill(pid, sig)
            self.js("toast", f"{'Forced' if force else 'Asked'} {name} to stop")
        except PermissionError:
            # Someone else's program (often the system's): ask for the password once.
            def work():
                r = subprocess.run(["pkexec", "kill", "-9" if force else "-15", str(pid)], capture_output=True)
                GLib.idle_add(self.js, "toast", "Stopped" if r.returncode == 0 else "Not stopped")
            threading.Thread(target=work, daemon=True).start()
        except Exception as e:
            self.js("toast", f"Could not stop it: {e}"[:120])


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    Kansei()
    Gtk.main()
