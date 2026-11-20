"""Apps' GTK windows (message boxes, Save and Open) in Kūhaku's look, by day and by night.

system/gtk/kuhaku.css is drawn for night. By day its colours are swapped for the day ones, token
for token, and written to ~/.config/gtk-3.0/kuhaku.css, which the user's gtk.css imports. GTK
apps read it when they start, so an app opened after the switch has the new look."""

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "system" / "gtk" / "kuhaku.css"
TARGET = Path.home() / ".config" / "gtk-3.0" / "kuhaku.css"
# GTK 4 and libadwaita apps (zenity's questions among them) read their own folder.
SOURCE4 = HERE / "system" / "gtk" / "kuhaku-gtk4.css"
TARGET4 = Path.home() / ".config" / "gtk-4.0" / "kuhaku.css"

# Night token -> day token: paper, ink, rest, the quieter grey, the seal, ink at any strength.
DAY = {
    "#121212": "#f7f4f1",
    "#ececec": "#111111",
    "#9a9a9a": "#666666",
    "#666666": "#9a9a9a",
    "#f0705a": "#e0301e",
    "rgba(236,236,236,": "rgba(17,17,17,",
}
_TOKEN = re.compile("|".join(re.escape(k) for k in DAY), re.I)

_written = None  # the theme last written, so the hourly theme push costs nothing


def css_for(theme, night_css):
    """The stylesheet for 'day' or 'night'."""
    if theme != "day":
        return night_css
    return _TOKEN.sub(lambda m: DAY[m.group(0).lower()], night_css)


def apply(theme, pairs=None):
    """Write the stylesheets for this theme, once per change."""
    global _written
    pairs = pairs or ((SOURCE, TARGET), (SOURCE4, TARGET4))
    if theme == _written and all(t.exists() for _s, t in pairs):
        return False
    for source, target in pairs:
        css = css_for(theme, source.read_text())
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text() != css:
            target.write_text(css)
        imports = target.parent / "gtk.css"  # the user's own gtk.css only gains an import
        if "kuhaku.css" not in (imports.read_text() if imports.exists() else ""):
            with open(imports, "a") as f:
                f.write('@import url("kuhaku.css");\n')
    _written = theme
    return True
