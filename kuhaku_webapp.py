"""The window every web-page app sits in, on GTK 4 (WebKitGTK 6) or, where that is missing, GTK 3.

An app is a page that draws and Python that works (ARCHITECTURE.md). This is the part between:
one undecorated window, one WebKit view, one message handler, links out to the browser. Apps that
use it run outside Kūhaku too, on any Linux desktop, X11 or Wayland, and in a Flatpak.

    from kuhaku_webapp import WebApp
    app = WebApp("space.kuhaku.Tegami", "Tegami", "mail", on_message=handle)
    app.load(page_uri)
    app.run()

Which toolkit: GTK 4 when its WebKit is installed, else GTK 3. KUHAKU_GTK=3 forces the old one.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import gi


def _pick():
    if os.environ.get("KUHAKU_GTK") != "3":
        try:
            gi.require_version("Gtk", "4.0")
            gi.require_version("Gdk", "4.0")
            gi.require_version("WebKit", "6.0")
            from gi.repository import WebKit  # noqa: F401  (the typelib may be listed but its library missing)

            return 4
        except (ValueError, ImportError):
            pass
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("WebKit2", "4.1")
    return 3


GTK = _pick()
from gi.repository import Gdk, Gio, GLib, Gtk

if GTK == 4:
    from gi.repository import WebKit
else:
    from gi.repository import WebKit2 as WebKit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from kuhaku_log import report
from kuhaku_pick import pick_command

HOME = Path.home()
DAY_BG, NIGHT_BG = (0.969, 0.957, 0.945), (0.071, 0.071, 0.071)


def in_kuhaku():
    """True inside the Kūhaku session; elsewhere the app follows the desktop's own light or dark."""
    return os.environ.get("KUHAKU_SESSION") == "1" or (HOME / ".config/kuhaku/config.json").exists()


def _portal_dark():
    """The desktop's light or dark preference (freedesktop portal: GNOME, KDE, Flatpak). None if unknown."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        r = bus.call_sync(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Settings",
            "ReadOne",
            GLib.Variant("(ss)", ("org.freedesktop.appearance", "color-scheme")),
            None,
            Gio.DBusCallFlags.NONE,
            500,
            None,
        )
        v = r.unpack()[0]
        return {1: True, 2: False}.get(v)
    except GLib.Error:  # no portal (a bare X11 session): fall back to the clock
        return None


def theme():
    """'day' or 'night': Kūhaku's choice inside Kūhaku, the desktop's preference elsewhere."""
    if in_kuhaku():
        from kuhaku_sun import is_night

        try:
            cfg = json.loads((HOME / ".config/kuhaku/config.json").read_text())
        except FileNotFoundError:
            cfg = {}
        except Exception:
            report()
            cfg = {}
        try:
            st = json.loads((HOME / ".local/state/kuhaku/state.json").read_text())
        except FileNotFoundError:
            st = {}
        except Exception:
            report()
            st = {}
        choice = st.get("theme") or cfg.get("theme", "auto")
        if choice in ("day", "night"):
            return choice
        return "night" if is_night(cfg) else "day"
    dark = _portal_dark()
    if dark is None:
        h = __import__("time").localtime().tm_hour
        dark = h >= 20 or h < 7
    return "night" if dark else "day"


def open_url(uri):
    """Links leave the app: to Mado inside Kūhaku, to the default browser elsewhere."""
    mado = HOME / ".local/bin/mado"
    if mado.exists() and in_kuhaku():
        subprocess.Popen([str(mado), uri], start_new_session=True)
        return
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
    except Exception:
        report()


class WebApp:
    """One window with one page. on_message(dict) gets what the page posts to its handler."""

    def __init__(
        self,
        app_id,
        title,
        handler,
        on_message,
        zoom=1.0,
        spelling=None,
        menus=False,
        on_ready=None,
        right_click=True,
        media=False,
        schemes=None,
        pick_words=("Choose a file", "Choose"),
        fixed_zoom=False,
    ):
        """right_click: the spelling menu where text is written (False: never a menu, the page has its own).
        media: video and sound play. schemes: {name: handler(request)} for the app's own addresses.
        pick_words: the title and button of Kūhaku's file picker when the page asks for a file.
        fixed_zoom: the page never zooms (no pinch, no Ctrl+scroll); it stays at zoom."""
        self.app_id, self.title, self.handler = app_id, title, handler
        self.on_message, self.on_ready = on_message, on_ready
        self.zoom, self.spelling, self.menus = zoom, spelling, menus
        self.right_click, self.media = right_click, media
        self.pick_words = pick_words
        self.fixed_zoom = fixed_zoom
        self.night = theme() == "night"
        self.uri = None
        self.html: tuple[str, str] | None = None
        ctx = WebKit.WebContext.get_default()
        for name, fn in (schemes or {}).items():
            ctx.register_uri_scheme(name, fn)
            ctx.get_security_manager().register_uri_scheme_as_local(name)
        self.win: Any = None
        self.view: Any = None
        self.on_theme = []  # callbacks(night) when day turns to night or back
        if GTK == 4:
            self.app = Gtk.Application(application_id=app_id, flags=Gio.ApplicationFlags.NON_UNIQUE)
            self.app.connect("activate", lambda _a: self._build())
        else:
            self.app = None
            self._build()

    # --- building ---------------------------------------------------------------------------
    def _build(self):
        self._dark(self.night)
        ucm = WebKit.UserContentManager()
        ucm.connect(f"script-message-received::{self.handler}", self._message)
        if GTK == 4:
            ucm.register_script_message_handler(self.handler, None)
            self.view = WebKit.WebView(user_content_manager=ucm)
            self.win = Gtk.ApplicationWindow(application=self.app, title=self.title)
            self.win.set_default_size(1200, 800)
        else:
            ucm.register_script_message_handler(self.handler)
            self.view = WebKit.WebView.new_with_user_content_manager(ucm)
            import kuhaku_paste

            kuhaku_paste.guard(self.view)  # GTK 3's WebKit crashes pasting from an empty clipboard
            self.win = Gtk.Window(title=self.title)
            self.win.connect("destroy", Gtk.main_quit)
        self.win.set_icon_name(self.title.lower())
        if in_kuhaku():
            self.win.set_decorated(False)  # Kūhaku draws no frames; elsewhere the desktop's frame stays
        self.win.maximize()
        self.view.set_zoom_level(self.zoom)
        if self.fixed_zoom:
            self._hold_zoom()
        st = self.view.get_settings()
        st.set_allow_file_access_from_file_urls(True)
        st.set_enable_write_console_messages_to_stdout(bool(os.environ.get("KUHAKU_DEBUG")))
        if self.media:
            st.set_enable_media(True)
            st.set_enable_mediasource(True)
            st.set_media_playback_requires_user_gesture(False)
        st.set_enable_smooth_scrolling(True)
        self._paint()
        if self.spelling:
            ctx = self.view.get_context()
            ctx.set_spell_checking_enabled(True)
            ctx.set_spell_checking_languages(self.spelling)

        # Right-click: only where text is written (the spelling fixes); the page has its own menus.
        def menu(*a):
            return not (self.right_click and a[-1].context_is_editable())

        self.view.connect("context-menu", menu)
        self.view.connect("decide-policy", self._policy)
        self.view.connect("run-file-chooser", self._choose_files)
        self.view.connect("web-process-terminated", lambda *_: self._reload())
        self.view.connect("load-changed", self._loaded)
        if self.menus:
            import kuhaku_menus

            kuhaku_menus.style_menus(self.night)
            self.on_theme.append(kuhaku_menus.style_menus)
        if GTK == 4:
            self.win.set_child(self.view)
            self.win.present()
        else:
            self.win.add(self.view)
            self.win.show_all()
        GLib.timeout_add_seconds(30, self._follow_theme)
        self._reload()

    def _hold_zoom(self):
        """No pinch zoom (it slides the app's own bar off screen) and no Ctrl+scroll zoom."""
        self.view.connect(
            "notify::zoom-level", lambda v, _p: v.get_zoom_level() != self.zoom and v.set_zoom_level(self.zoom)
        )
        if GTK == 4:
            pinch = Gtk.GestureZoom()
            pinch.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)  # before WebKit's own
            pinch.connect("begin", lambda g, _s: g.set_state(Gtk.EventSequenceState.CLAIMED))
            self.view.add_controller(pinch)
            wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
            wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            wheel.connect("scroll", lambda c, _x, _y: bool(c.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK))
            self.view.add_controller(wheel)

    def _dark(self, night):
        s = Gtk.Settings.get_default()
        if s is not None:
            s.set_property("gtk-application-prefer-dark-theme", night)

    def _paint(self):
        self.view.set_background_color(Gdk.RGBA(*(NIGHT_BG if self.night else DAY_BG), 1))

    def _follow_theme(self):
        """Day and night change while the app is open, not only when it was opened."""
        night = theme() == "night"
        if night != self.night:
            self.night = night
            self._dark(night)
            self._paint()
            self.eval(f"document.body.classList.toggle('night', {'true' if night else 'false'})")
            for f in self.on_theme:
                try:
                    f(night)
                except Exception:
                    report()
        return True

    # --- the page ---------------------------------------------------------------------------
    def load(self, uri):
        """uri: the page; ?theme=day|night is added so it opens in the right light."""
        self.uri, self.html = uri, None
        if self.view is not None:
            sep = "&" if "?" in uri else "?"
            self.view.load_uri(f"{uri}{sep}theme={'night' if self.night else 'day'}")

    def load_html(self, html, base_uri):
        """A page given as text, with base_uri where its CSS and scripts sit."""
        self.html, self.uri = (html, base_uri), None
        if self.view is not None:
            self.view.load_html(html, base_uri)

    def _reload(self):
        """After the window is built, or when WebKit's page process ended on its own."""
        if self.html:
            self.load_html(*self.html)
        elif self.uri:
            self.load(self.uri)

    def _loaded(self, _v, event):
        if event == WebKit.LoadEvent.FINISHED and self.on_ready:
            try:
                self.on_ready()
            except Exception:
                report()

    def eval(self, script):
        self.view.evaluate_javascript(script, -1, None, None, None, None, None)

    def js(self, fn, payload=None):
        self.eval(f"{fn}({json.dumps(payload)})")

    def _message(self, _m, value):
        try:
            v = value if GTK == 4 else value.get_js_value()
            msg = json.loads(v.to_string())
        except Exception:
            report()
            return
        self.on_message(msg)

    def _policy(self, _v, decision, kind):
        P = WebKit.PolicyDecisionType
        if kind in (P.NAVIGATION_ACTION, P.NEW_WINDOW_ACTION):
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri.startswith(("http://", "https://", "mailto:")):
                if uri.startswith("mailto:"):
                    self.js("composeTo", uri[7:].split("?")[0])
                else:
                    open_url(uri)
                decision.ignore()
                return True
        return False

    def _choose_files(self, _v, request):
        """A page asks for a file: inside Kūhaku its own picker, not the system's dialog."""
        args = pick_command(
            self.pick_words, single=not request.get_select_multiple(), accept=request.get_mime_types() or []
        )
        if not args:
            return False  # elsewhere the desktop's own dialog

        def run():
            try:
                out = subprocess.run(args, capture_output=True, text=True).stdout
                paths = json.loads(out or "[]")
            except Exception:
                report()
                paths = []
            GLib.idle_add(lambda: (request.select_files(paths) if paths else request.cancel()) and False)

        __import__("threading").Thread(target=run, daemon=True).start()
        return True

    # --- the window -------------------------------------------------------------------------
    def later(self, f, *args):
        GLib.idle_add(lambda: f(*args) and False)

    def set_title(self, title):
        self.win.set_title(title)

    def present(self):
        self.win.present()

    def copy_text(self, text):
        """Puts text on the clipboard (Cmd+V pastes it in any app)."""
        if GTK == 4:
            self.win.get_clipboard().set(text)
        else:
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)

    def minimize(self):
        if GTK == 4:
            self.win.minimize()
        else:
            self.win.iconify()

    def quit(self):
        if GTK == 4:
            self.app.quit()
        else:
            self.win.destroy()

    def run(self):
        if GTK == 4:
            self.app.run([sys.argv[0]])
        else:
            Gtk.main()
