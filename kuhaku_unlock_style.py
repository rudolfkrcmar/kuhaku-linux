"""The look shared by Kūhaku's unlock windows (fingers, face): the lock screen's colours,
Inter in light weights, and the red line."""
import json
import time
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402
from kuhaku_log import report  # faults go to the log instead of vanishing

COLORS = {"night": ("#121212", "#ececec", "#9a9a9a", "#f0705a"), "day": ("#f7f4f1", "#111111", "#666666", "#e0301e")}


def theme():
    try:
        from terminal import theme as t
        return t()
    except Exception:
        report()
    home = Path.home()

    def load(p):
        try:
            return json.loads(p.read_text())
        except Exception:
            report()
            return {}
    cfg = load(home / ".config/kuhaku/config.json")
    choice = load(home / ".local/state/kuhaku/state.json").get("theme") or cfg.get("theme", "auto")
    if choice in ("day", "night"):
        return choice
    h = time.localtime().tm_hour
    return "night" if h >= cfg.get("night_from", 20) or h < cfg.get("night_until", 7) else "day"


def window_css():
    bg, fg, muted, accent = COLORS["night" if theme() == "night" else "day"]
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
