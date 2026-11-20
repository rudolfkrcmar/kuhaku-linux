#!/usr/bin/env python3
"""Builds design/library.html, the inventory beside the design manual (design/index.html): every icon Kūhaku draws, gathered from the pages themselves,
shown at its real size and large, at rest and on, by day and by night, with a quiet audit
against the icon grammar in design/MANUAL.md.

    python3 dev/design.py          write design/library.html
    python3 dev/design.py --check  fail if it is out of date (tests run this)
"""

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "design" / "library.html"

# Where icons live, and the name each place goes by in the library.
SOURCES = [
    ("Home screen", "index.html"),
    ("Home screen", "home/panels.js"),
    ("Home screen", "home/tabs.js"),
    ("Mado", "browser.html"),
    ("Mado", "browser/comments.html"),
    ("Tsukuru", "ide/tsukuru.html"),
    ("Kansei", "kansei/kansei.html"),
    ("Tegami", "mail/tegami.html"),
    ("Miru", "miru/miru.html"),
    ("Miru", "miru/miru.js"),
    ("Picker", "pick/picker.js"),
]

SVG = re.compile(r"<svg\b[^>]*>.*?</svg>", re.S)
LABEL = re.compile(r'(?:aria-label|title|data-id|id)="([^"$]{1,80})"')
TOKEN = re.compile(r"--([a-z]+):\s*(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))")


# Ids that say little on their own.
NAMES = {"acctHome": "Account · home", "acctWork": "Account · work", "batteryWrap": "Battery"}

# Signs drawn in CSS, not SVG: the top row's own language. Kept here by hand, beside the icons.
SIGNS = """
<figure class="card"><div class="pair"><div class="tile on"><i class="s-line"></i></div><div class="tile on"><i class="s-line open"></i></div></div>
<figcaption><b>Menu</b><span class="kind">Sign · one red line, 20 by 2; longer on hover, heavier when open</span></figcaption></figure>
<figure class="card"><div class="pair"><div class="tile on"><i class="s-line"></i><i class="s-dot" style="margin-left:10px;background:var(--fg)"></i></div><div class="tile"></div></div>
<figcaption><b>Open apps</b><span class="kind">Sign · an ink dot after the line, only while apps are open; the strokes of the marks say which</span></figcaption></figure>
<figure class="card"><div class="pair"><div class="tile on"><i class="s-bar"></i></div><div class="tile"></div></div>
<figcaption><b>Composer</b><span class="kind">Sign · one upright red line, the caret: where you write. No dot</span></figcaption></figure>
<figure class="card"><div class="pair"><div class="tile on"><i class="s-batt"><b style="width:66%"></b></i></div><div class="tile on"><i class="s-batt"><b style="width:12%;background:var(--accent)"></b></i></div></div>
<div class="pair" style="margin-top:6px"><div class="tile on"><i class="s-batt"><b style="width:50%"></b><u style="position:absolute;left:79%;top:0;width:1px;height:100%;background:var(--muted)"></u></i></div><div class="tile"></div></div>
<figcaption><b>Battery</b><span class="kind">Sign · a bar, 18 by 6: at rest, low (red), plugged in (a tick where charging stops)</span></figcaption></figure>
"""


def label_for(text, start):
    """The name of an icon: the nearest label on the tag that holds it, else on the svg."""
    before = text[max(0, start - 500) : start]
    tag = before[before.rfind("<") :] if "<" in before else ""
    svg_open = text[start : text.find(">", start) + 1]
    key = re.search(r"([A-Za-z]\w*)\s*[:=]\s*(?:\([^)]*\)\s*=>\s*)?['`\"]$", before)
    if key:  # an icon kept in a script under a name: pen: '<svg …'
        return key.group(1)
    for part in (tag, svg_open, before[-260:]):
        found = LABEL.findall(part)
        if found:
            return found[-1].split(" (")[0]
    return "unnamed"


def audit(svg, sizes=()):
    """Notes where an icon steps outside the grammar. Notes, not rules: some are deliberate."""
    notes = []
    box = re.search(r'viewBox="([^"]+)"', svg)
    if not box or box.group(1) != "0 0 24 24":
        notes.append(f"grid {box.group(1) if box else 'none'} (not 24)")
    accents = len(re.findall(r'class="(?:[^"]*\s)?(?:hot|acc)(?:\s[^"]*)?"', svg))
    if accents > 1:
        notes.append(f"{accents} accent parts")
    if re.search(r'stroke-linecap="round"|stroke-linejoin="round"|\srx="', svg):
        notes.append("soft ends or corners (the canon is cut)")
    colours = set(re.findall(r"#[0-9a-fA-F]{3,6}\b", svg))
    if colours:
        notes.append("fixed colour " + " ".join(sorted(colours)))
    widths = set(re.findall(r'stroke-width="([\d.]+)"', svg))
    if len(widths) > 1:
        notes.append("mixed strokes " + "/".join(sorted(widths)))
    for size in sizes:
        for w in widths:
            seen = float(w) * size / 24
            if size <= 24 and not 0.8 <= seen <= 1.1:
                notes.append(f"line {seen:.2f}px at {size}px (aim for about 1px)")
    marks = len(re.findall(r"<(?:path|circle|rect|line|polyline|polygon|ellipse)\b", svg))
    if marks > 5:
        notes.append(f"{marks} marks (busy?)")
    return notes


def is_mark(svg):
    """A mark has one part that can turn red; a glyph is one ink only."""
    return bool(re.search(r'class="(?:[^"]*\s)?(?:hot|acc|stem)(?:\s[^"]*)?"', svg))


def kind(svg):
    widths = sorted(set(re.findall(r'stroke-width="([\d.]+)"', svg)))
    return ("Mark" if is_mark(svg) else "Glyph") + " · stroke " + ("/".join(widths) or "none")


def sizes_of(i):
    return " · " + "/".join(f"{s:g}" for s in sorted(i["sizes"])) + "px" if i["sizes"] else ""


def gather():
    icons = {}
    for place, rel in SOURCES:
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for m in SVG.finditer(text):
            svg = m.group(0)
            if "${" in svg:  # drawn from data at run time: not a fixed icon
                continue
            head, body = svg[: svg.find(">") + 1], svg[svg.find(">") + 1 :]
            head = re.sub(r'\s+(?:width|height|id|class|aria-hidden|hidden|style)="[^"]*"', "", head)
            key = re.sub(r"\s+", " ", head + body).strip()
            line = text.count("\n", 0, m.start()) + 1
            name = label_for(text, m.start())
            name = NAMES.get(name, name)
            entry = icons.setdefault(key, {"svg": key, "names": [], "where": [], "sizes": [], "place": place})
            if name not in entry["names"]:
                entry["names"].append(name)
            entry["where"].append(f"{rel}:{line}")
            size = re.search(r'\swidth="([\d.]+)"', svg[: svg.find(">")])
            if size and float(size.group(1)) not in entry["sizes"]:
                entry["sizes"].append(float(size.group(1)))
    return list(icons.values())


def tokens():
    css = (ROOT / "home" / "home.css").read_text(encoding="utf-8")
    day = css[css.find(":root") : css.find("}", css.find(":root"))]
    night = css[css.find("body.night") : css.find("}", css.find("body.night"))]
    return TOKEN.findall(day), dict(TOKEN.findall(night))


def render():
    icons = gather()
    day, night = tokens()
    places = []
    for p, _ in SOURCES:
        if p not in places:
            places.append(p)
    swatches = "".join(
        f'<div class="sw"><i style="background:{html.escape(v)}"></i><i style="background:{html.escape(night.get(k, v))}"></i>'
        f"<b>--{k}</b><span>{html.escape(v)} · {html.escape(night.get(k, ''))}</span></div>"
        for k, v in day
    )
    sections = []
    total = 0
    for place in places:
        group = [i for i in icons if i["place"] == place]
        group.sort(key=lambda i: not is_mark(i["svg"]))
        if not group:
            continue
        total += len(group)
        cards = []
        for i in group:
            notes = list(dict.fromkeys(audit(i["svg"], i["sizes"])))
            big = re.sub(r"<svg\b", '<svg width="72" height="72"', i["svg"], count=1)
            small = re.sub(r"<svg\b", '<svg width="18" height="18"', i["svg"], count=1)
            cards.append(
                '<figure class="card">'
                f'<div class="pair"><div class="tile rest">{big}</div><div class="tile on">{big}</div></div>'
                f'<div class="real"><span class="rest">{small}</span><span class="on">{small}</span>'
                f'<span class="rest night">{small}</span><span class="on night">{small}</span></div>'
                f"<figcaption><b>{html.escape(' · '.join(i['names']))}</b>"
                f'<span class="kind">{kind(i["svg"])}{sizes_of(i)}</span>'
                f'<span class="where">{html.escape(", ".join(i["where"]))}</span>'
                + (f'<span class="notes">{html.escape(" · ".join(notes))}</span>' if notes else "")
                + "</figcaption></figure>"
            )
        sections.append(
            f'<section><h2>{html.escape(place)}<small>{len(group)}</small></h2><div class="grid">{"".join(cards)}</div></section>'
        )
    return PAGE.format(signs=SIGNS, swatches=swatches, sections="".join(sections), total=total)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Kūhaku · Icon inventory</title>
<!-- Written by dev/design.py from the pages themselves. Do not edit by hand. -->
<style>
:root {{ --bg:#f7f4f1; --fg:#111; --muted:#666; --faint:rgba(17,17,17,.08); --accent:#e0301e; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font-family:'Inter Variable','Inter',system-ui,sans-serif;
       font-weight:300; -webkit-font-smoothing:antialiased; padding:72px 8vw 120px; }}
header {{ display:flex; align-items:center; gap:14px; margin-bottom:12px; }}
header .line {{ width:20px; height:2px; background:var(--accent); }}
h1 {{ margin:0; font-size:30px; font-weight:200; letter-spacing:-.02em; }}
p.lede {{ max-width:620px; color:var(--muted); font-size:15px; line-height:1.6; margin:0 0 64px 34px; }}
h2 {{ font-size:22px; font-weight:200; letter-spacing:-.01em; margin:72px 0 24px; display:flex; gap:12px; align-items:baseline; }}
h2 small {{ font-size:13px; color:var(--muted); }}
.swatches {{ display:flex; flex-wrap:wrap; gap:28px; }}
.sw {{ display:grid; grid-template-columns:28px 28px; width:150px; gap:0 6px; font-size:12.5px; }}
.sw i {{ height:56px; border-radius:6px; box-shadow:0 0 0 1px var(--faint); }}
.sw b, .sw span {{ grid-column:1 / 3; font-weight:400; margin-top:8px; }}
.sw span {{ color:var(--muted); font-weight:300; margin-top:2px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(230px, 1fr)); gap:40px 28px; }}
.card {{ margin:0; }}
.pair {{ display:grid; grid-template-columns:1fr 1fr; gap:6px; }}
.tile {{ aspect-ratio:1; display:grid; place-items:center; border-radius:14px; color:var(--muted);
        background-image:linear-gradient(var(--faint) 1px, transparent 1px), linear-gradient(90deg, var(--faint) 1px, transparent 1px);
        background-size:12.5% 12.5%; background-position:center; box-shadow:0 0 0 1px var(--faint); }}
.tile.on {{ color:var(--fg); }}
.real {{ display:flex; gap:0; margin-top:6px; }}
.real span {{ flex:1; display:grid; place-items:center; height:38px; color:var(--muted); }}
.real span.on {{ color:var(--fg); }}
.real .night {{ background:#121212; color:#9a9a9a; }}
.real .night.on {{ color:#ececec; }}
.real span:nth-child(3) {{ border-radius:8px 0 0 8px; }}
.real span:nth-child(4) {{ border-radius:0 8px 8px 0; }}
svg [class]:not([stroke]) {{ stroke:currentColor; }}
.on svg .hot, .on svg .acc, .on svg .stem, svg .acc {{ stroke:var(--accent); }}
.night.on svg .hot, .night svg .acc, .night.on svg .stem {{ stroke:#f0705a; }}
.s-line {{ width:40px; height:4px; background:var(--accent); }}
.s-line.open {{ width:52px; height:6px; }}
.s-dot {{ width:12px; height:12px; border-radius:99px; background:var(--accent); }}
.s-bar {{ width:3px; height:60px; background:var(--accent); }}
.tile i {{ display:inline-block; }}
.tile:has(i + i) {{ display:flex; justify-content:center; align-items:center; }}
.s-batt {{ width:72px; height:24px; background:rgba(17,17,17,.18); display:flex !important; position:relative; }}
.s-batt b {{ display:block; height:100%; background:var(--fg); }}
figcaption {{ display:flex; flex-direction:column; gap:3px; margin-top:12px; font-size:13px; }}
figcaption b {{ font-weight:400; }}
.kind {{ color:var(--muted); font-size:11.5px; }}
.where {{ color:var(--muted); font-size:11.5px; word-break:break-all; }}
.notes {{ color:var(--accent); font-size:11.5px; }}
</style>
</head>
<body>
<header><span class="line"></span><h1>Icon inventory</h1></header>
<p class="lede">Every icon Kūhaku draws ({total}), taken from the pages each time it is built. Large on the 24 grid at rest and on,
then at real size by day and by night. Red notes are where an icon steps outside the grammar in the <a href="index.html" style="color:inherit">design manual</a>: a question, not a verdict.</p>
<h2>Colour</h2>
<div class="swatches">{swatches}</div>
<h2>Signs<small>drawn in CSS</small></h2>
<div class="grid">{signs}</div>
{sections}
</body>
</html>
"""


def main():
    page = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != page:
            sys.exit("design/library.html is out of date: run python3 dev/design.py")
        return
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
