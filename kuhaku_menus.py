"""Right-click menus in Kūhaku's own look (Tegami's spelling menu, Mado's page menu).

The system GTK theme draws them in its own light colours, which clash with the night pages
and can leave the words unreadable. One CSS provider per app, swapped when day turns to night.
"""

import gi

if "gi.repository.Gtk" not in __import__("sys").modules:  # the app has not chosen a GTK yet
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk

COLOURS = {
    "night": {"bg": "#1b1b1b", "fg": "#ececec", "muted": "#6f6f6f", "line": "rgba(236,236,236,.08)"},
    "day": {"bg": "#fbfaf8", "fg": "#111111", "muted": "#9a9a9a", "line": "rgba(17,17,17,.08)"},
}

_provider = None


def css(night: bool) -> str:
    c = COLOURS["night" if night else "day"]
    return f"""
menu, .menu, .context-menu, window.popup menu {{
  background: {c["bg"]}; color: {c["fg"]}; border: 1px solid {c["line"]}; border-radius: 12px;
  padding: 6px; font-family: "Inter Variable", Inter, sans-serif; font-weight: 300;
}}
menu menuitem {{ padding: 7px 14px; border-radius: 8px; color: {c["fg"]}; background: transparent; }}
menu menuitem label {{ color: {c["fg"]}; }}
menu menuitem:hover {{ background: {c["line"]}; color: {c["fg"]}; }}
menu menuitem:hover label {{ color: {c["fg"]}; }}
menu menuitem:disabled, menu menuitem:disabled label {{ color: {c["muted"]}; }}
menu separator {{ background: {c["line"]}; min-height: 1px; margin: 4px 8px; }}
menu arrow {{ color: {c["muted"]}; }}
window.popup, window.csd.popup {{ background: transparent; box-shadow: none; }}
popover.menu > contents {{
  background: {c["bg"]}; color: {c["fg"]}; border: 1px solid {c["line"]}; border-radius: 12px;
  padding: 6px; font-family: "Inter Variable", Inter, sans-serif; font-weight: 300; box-shadow: none;
}}
popover.menu modelbutton {{ padding: 7px 14px; border-radius: 8px; color: {c["fg"]}; }}
popover.menu modelbutton:hover {{ background: {c["line"]}; }}
popover.menu modelbutton:disabled {{ color: {c["muted"]}; }}
popover.menu separator {{ background: {c["line"]}; min-height: 1px; margin: 4px 8px; }}
popover.menu > arrow {{ background: {c["bg"]}; border-color: {c["line"]}; }}
"""


def style_menus(night: bool) -> None:
    """Applies (or re-applies, on a theme change) the menu look to every window of this app."""
    global _provider
    gtk4 = Gtk.get_major_version() == 4
    target = Gdk.Display.get_default() if gtk4 else Gdk.Screen.get_default()
    if target is None:
        return
    if _provider is None:
        _provider = Gtk.CssProvider()
        add = Gtk.StyleContext.add_provider_for_display if gtk4 else Gtk.StyleContext.add_provider_for_screen
        add(target, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    if gtk4:
        _provider.load_from_string(css(night))
    else:
        _provider.load_from_data(css(night).encode())
