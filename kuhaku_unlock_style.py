"""The look shared by Kūhaku's unlock windows (fingers, face): the lock screen's colours,
Inter in light weights, and the red line."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

COLORS = {"night": ("#121212", "#ececec", "#9a9a9a", "#f0705a"), "day": ("#f7f4f1", "#111111", "#666666", "#e0301e")}


def window_css():
    bg, fg, muted, accent = COLORS["night"]  # password screens stay dark, day or night
    css = Gtk.CssProvider()
    css.load_from_data(f"""
        window {{ background: {bg}; }}
        label {{ font-family: "Inter", sans-serif; color: {fg}; font-size: 34px; font-weight: 200; }}
        #small {{ color: {muted}; font-size: 15px; font-weight: 300; }}
        #dots {{ color: {accent}; font-size: 18px; letter-spacing: 6px; font-weight: 300; }}
        #line {{ background: {accent}; min-width: 2px; }}
        button {{ background: none; border: none; box-shadow: none; padding: 0 0 4px 0; border-radius: 0;
                  font-family: "Inter", sans-serif; font-size: 16px; font-weight: 300; color: {muted}; }}
        button label {{ font-size: 16px; font-weight: 300; color: {muted}; }}
        button:hover label, button.primary label {{ color: {fg}; }}
        button.primary {{ border-bottom: 1px solid {accent}; }}
    """.encode())
    return css
