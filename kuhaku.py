#!/usr/bin/env python3
"""Kūhaku (空白, "blank space"): a full screen home for Claude.

A WebKit window that replaces the desktop. Questions go to Claude Code
(`claude -p`) in the background; only the pulsing dot and the final answer
are shown. Everything Claude did is logged and can be opened in a real
terminal with Ctrl+` (or Ctrl+Alt+Return).
"""

import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
import threading
import re
import time
from datetime import datetime, timezone
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
from kuhaku_log import report  # faults go to the log instead of vanishing

from kuhaku_system import (  # noqa: E402  the computer: battery, Wi-Fi, power, displays, apps
    FINGER_ORDER,
    PAD_KEYS,
    POWER_KEYS,
    _limits_cache,
    battery_details,
    bluetooth_icon_state,
    bluetooth_state,
    daily_tokens,
    describe,
    launch_app,
    list_apps,
    load_json,
    note_limits,
    open_windows,
    pgrep_x,
    plan_limits,
    read_battery,
    read_displays,
    read_power,
    read_time,
    read_trackpad,
    read_unlock,
    read_wifi,
    run,
    spawn,
    wifi_connect,
    wifi_networks,
    wifi_signal,
    write_display,
    write_power,
    write_time,
    write_trackpad,
    notices,
)
from kuhaku_config import (  # noqa: E402
    CONFIG_DIR,
    CONFIG_FILE,
    DEFAULT_CONFIG,
    FIFO,
    HERE,
    HOME,
    MENU,
    STATE_DIR,
    STATE_FILE,
)
from kuhaku_panels import Panels  # noqa: E402
from kuhaku_voice import Voice  # noqa: E402
from kuhaku_settings import Settings  # noqa: E402
from kuhaku_claude import ClaudeJobs  # noqa: E402
from kuhaku_log import report  # faults go to the log instead of vanishing


class Kuhaku(Panels, Voice, Settings, ClaudeJobs):
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
        self.convo = 0  # a conversation's number while one is on (0: off); a new one outdates the old
        self.speaker = None  # the Piper worker, loaded once
        self.speaker_lock = threading.Lock()
        self.voice_proc = None  # what is listening or playing right now
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
        GLib.timeout_add(1000, self.push_tablet)
        GLib.timeout_add_seconds(15, self.schedule)
        GLib.timeout_add(1500, lambda: (self.schedule(), False)[1])
        self.last_windows = None
        GLib.timeout_add(1500, self.push_windows)
        GLib.timeout_add_seconds(60, self.push_theme)
        self.recording = None
        threading.Thread(target=self.listen_fifo, daemon=True).start()
        # The red dot over apps lives in the top bar now (corner.py), before the red line.
        self.rundot = None
        try:
            from osd import Osd

            self.osd = Osd(self.current_theme)
        except Exception as e:
            print("Kūhaku: no on-screen volume feedback:", e)
        try:
            from corner import Corner

            self.corner = Corner(
                self.current_theme,
                lambda: self.window.get_window().get_xid() if self.window.get_window() else 0,
                read_battery,
                read_wifi,
                self.open_panel,
            )
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
                self.open_terminal()  # what happened, in real Claude Code
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
            if "draft" not in cur:  # drafts used to be one for the whole screen
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
        self.send("setMicMode", self.config.get("mic_button") or "talk")
        self.send(
            "setStatus",
            {"battery": read_battery(), "wifi": read_wifi(), "bt": bluetooth_icon_state(), "signal": wifi_signal()},
        )
        return True

    def push_tablet(self):
        """Folded into a tablet: the home screen is told (kuhaku-tablet.py writes the mode)."""
        try:
            on = (Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "kuhaku-tablet-mode").read_text().strip() == "1"
        except OSError:
            on = False
        if on != getattr(self, "tablet_on", None):
            self.tablet_on = on
            self.send("setTablet", {"on": on})
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
        self.send(
            "setTheme",
            {
                "theme": theme,
                "choice": self.state.get("theme") or "auto",
                "from": self.config["night_from"],
                "until": self.config["night_until"],
            },
        )
        return True

    def save_state(self):
        STATE_FILE.write_text(json.dumps(self.state, indent=2))

    # --- messages from the page ------------------------------------------
    def on_message(self, _manager, result):
        try:
            msg = json.loads(result.get_js_value().to_string())
        except Exception:
            report()
            return
        handler = MESSAGES.get(msg.get("type"))
        if handler:
            handler(self, msg)

    def save_draft(self, msg):
        """What is typed (and the prepared next message) belongs to its tab and survives restarts."""
        tab = self.tabs.get(msg.get("tab")) or self.tabs.get(self.tabs.current)
        tab["draft"] = msg.get("text", "")[:20000]
        tab["next"] = msg.get("next", "")[:20000]
        self.tabs.save()

    def dismiss(self, _msg=None):
        self.state["dismissed"] = True
        self.save_state()

    def stop_recording(self, _msg=None):
        if self.recording:
            self.capture("record")

    def overview(self, _msg=None):
        from rundot import overview

        if not overview():
            self.js("openRuns()")

    # --- commands from keyboard shortcuts (kuhaku-capture writes to the FIFO) -
    def listen_fifo(self):
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
                    elif len(parts) >= 2 and parts[0] == "note":  # e.g. airplane mode (F8)
                        try:
                            n = json.loads(line.split(None, 1)[1])
                            GLib.idle_add(lambda n=n: self.note(n.get("title", ""), n.get("detail", "")) and False)
                        except Exception:
                            report()
                    elif parts == ["notices"]:  # F9
                        GLib.idle_add(self.open_panel, "notices")
                    elif parts == ["display"]:  # F7
                        GLib.idle_add(self.key_js, "openPage('settings', 'display')")
                    elif parts == ["talk"]:  # F10
                        GLib.idle_add(self.key_js, "if (page) closePage(); if (panel) closePanel(); talk()")
                    elif len(parts) >= 2 and parts[0] == "continue":  # from Kiroku: a past job in a new tab
                        GLib.idle_add(self.continue_thread, parts[1], " ".join(parts[2:]))
                    elif len(parts) >= 2 and parts[0] == "ask":  # from another app: a new job
                        try:
                            GLib.idle_add(self.ask_new_tab, json.loads(line.split(None, 1)[1]).get("text", ""))
                        except Exception:
                            report()

    def show_menu(self):
        """The power key: Kūhaku comes forward with its menu open (lock, log out, restart, shut down)."""
        subprocess.run(["wmctrl", "-k", "on"], capture_output=True)
        self.window.present()
        self.js("if (page) closePage(); if (panel) closePanel(); openMenu()")
        return False

    def key_js(self, code):
        """A key on the keyboard (F7, F10): Kūhaku comes forward and does it."""
        subprocess.run(["wmctrl", "-k", "on"], capture_output=True)
        self.window.present()
        self.js(code)
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
        subprocess.Popen(
            [sys.executable, str(HERE / "kuhaku-shot.py"), how],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
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
        pipeline = (
            "ximagesrc use-damage=false show-pointer=true ! video/x-raw,framerate=24/1 ! queue ! videoconvert ! "
            "video/x-raw,format=I420 ! x264enc speed-preset=ultrafast tune=zerolatency bitrate=8000 key-int-max=48 ! "
            "h264parse ! queue ! mp4mux ! filesink"
        )
        try:
            proc = subprocess.Popen(
                ["gst-launch-1.0", "-e", "-q", *pipeline.split(), f"location=\"{path}\""],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self.note("Recording", "Not available")
            return
        self.recording = (proc, path)
        self.note("Recording", "Same keys to stop")
        self.send("setRecording", True)

    # --- pictures for Claude: copied into ~/Pictures/ForClaude ------------
    def pick_pictures(self):
        dialog = Gtk.FileChooserNative.new(
            "Add a picture for Claude", self.window, Gtk.FileChooserAction.OPEN, "Add", "Cancel"
        )
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
            old.terminate()  # a sign-in left waiting: start afresh
        env = accounts.env(ident=ident)
        shim = str(HERE / "signin-bin")
        env.update(
            PATH=shim + ":" + env["PATH"],
            BROWSER=shim + "/xdg-open",
            KUHAKU_SIGNIN_TITLE="Sign in · " + accounts.label(acct),
        )
        if accounts.kind(acct) == "claude":
            env["KUHAKU_SIGNIN_BROWSER"] = "firefox"  # Cloudflare's human check stops WebKitGTK
        log = open(STATE_DIR / "signin.log", "a")
        # stdin stays open: Claude reads a pasted code from it if ever needed; Gemini asks yes/no first.
        p = subprocess.Popen(
            ["bash", "-c", "exec env " + accounts.login_command(acct)],
            env=env,
            cwd=str(HOME),
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
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
            spawn(
                [
                    term,
                    "--maximize",
                    "--title=Claude",
                    f"--working-directory={HOME}",
                    "-x",
                    "bash",
                    "-lc",
                    f"{cmd}; exec bash",
                ]
            )
        else:
            spawn(["x-terminal-emulator", "-e", "bash", "-lc", f"{cmd}; exec bash"])

    def quit(self, *_):
        # Running jobs stay "running" in tabs.json, so after a restart they continue from the line.
        for proc in list(self.procs.values()):
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                report()
        self.convo = 0
        for p in (self.recorder, self.whisper, self.speaker, self.voice_proc):
            if p and p.poll() is None:
                p.terminate()
        Gtk.main_quit()


# What the page may say (window.webkit.messageHandlers.kuhaku), and who listens.
MESSAGES = {
    "ask": lambda k, m: k.ask(m.get("text", ""), voice=bool(m.get("voice"))),
    "voice": lambda k, m: k.voice(m.get("on")),
    "cancel": lambda k, m: k.cancel(m.get("tab")),
    "tab": lambda k, m: k.tab_action(m),
    "menu": lambda k, m: k.menu(m.get("id")),
    "dictate": lambda k, m: k.dictate(m.get("on")),
    "picture": lambda k, m: k.pick_pictures(),
    "stopRecording": lambda k, m: k.stop_recording(),
    "dismiss": lambda k, m: k.dismiss(),
    "draft": lambda k, m: k.save_draft(m),
    "panel": lambda k, m: threading.Thread(target=k.panel, args=(m,), daemon=True).start(),
    "overview": lambda k, m: k.overview(),
}


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    Kuhaku()
    Gtk.main()
