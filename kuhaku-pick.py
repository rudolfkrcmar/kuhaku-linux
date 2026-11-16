#!/usr/bin/env python3
"""Kūhaku's file picker as its own window, for apps that need a file: Tegami's attachments,
Mado's uploads. The same calm grid as the home screen's "Add a picture" (pick/picker.js).

    kuhaku-pick.py [--title T] [--verb V] [--single] [--images] [--accept "image/*,.pdf"]

Prints the chosen paths as a JSON list on stdout ([] when closed without a choice)."""

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import kuhaku_pick
from kuhaku_log import report
from kuhaku_webapp import GTK, WebApp

TEXT_SHOWN = 8000  # characters of a text file shown when looking closer
PAGES = tempfile.TemporaryDirectory(prefix="kuhaku-pick-")  # first pages of PDFs, gone when the picker closes


def peek(path):
    """A closer look at a file that is not a picture: its first lines, or a PDF's first page."""
    import miru_files

    p = Path(path)
    out = {"path": path}
    try:
        mime, _ = miru_files.mime_of(p)
        kind = miru_files.kind_of(p, mime)
        if kind == "text":
            with open(p, encoding="utf-8", errors="replace") as f:
                out["text"] = f.read(TEXT_SHOWN)
        elif kind == "pdf":
            dest = Path(PAGES.name) / f"page{abs(hash(path))}"
            subprocess.run(
                ["pdftoppm", "-png", "-f", "1", "-l", "1", "-scale-to", "1400", "-singlefile", str(p), str(dest)],
                capture_output=True,
                timeout=20,
            )
            if dest.with_suffix(".png").exists():
                out["image"] = dest.with_suffix(".png").as_uri()
    except FileNotFoundError:
        pass  # no pdftoppm: the file's card is shown instead
    except Exception:
        report()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="Choose a file")
    ap.add_argument("--verb", default="Choose")
    ap.add_argument("--single", action="store_true")
    ap.add_argument("--images", action="store_true")
    ap.add_argument("--accept", default="")
    a = ap.parse_args()
    accept = [x for x in a.accept.split(",") if x.strip()]
    images = a.images or (bool(accept) and all(x.strip().lower().startswith("image/") for x in accept))
    chosen: list[str] = []
    app: WebApp

    def work(fn, *args):
        def run():
            try:
                fn(*args)
            except Exception:
                report()

        threading.Thread(target=run, daemon=True).start()

    def on_message(m):
        action = m.get("action")
        if action == "list":
            work(
                lambda: app.later(
                    app.js,
                    "setListing",
                    kuhaku_pick.listing(m.get("folder") or "", m.get("path") or "", images, accept),
                )
            )
        elif action == "peek":
            work(lambda: app.later(app.js, "setPeek", peek(m.get("path") or "")))
        elif action == "add":
            chosen[:] = [p for p in m.get("paths") or [] if Path(p).is_file()]
        elif action == "close":
            app.quit()

    app = WebApp("space.kuhaku.Pick", a.title, "pick", on_message, right_click=False)
    if app.win is not None and GTK == 3:
        app.win.set_keep_above(True)
    query = urllib.parse.urlencode(
        {
            "title": a.title,
            "verb": a.verb,
            "single": "1" if a.single else "",
            "start": "Screenshots" if images else "Recent",
        }
    )
    app.load((HERE / "pick" / "pick.html").as_uri() + "?" + query)
    app.run()
    print(json.dumps(chosen), flush=True)


if __name__ == "__main__":
    main()
