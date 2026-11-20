#!/usr/bin/env python3
"""Kūhaku's file picker as its own window, for apps that need a file: Tegami's attachments,
Mado's uploads. The same calm grid as the home screen's "Add a file" (pick/picker.js).

    kuhaku-pick.py [--title T] [--verb V] [--single] [--images] [--accept "image/*,.pdf"]

Prints the chosen paths as a JSON list on stdout ([] when closed without a choice)."""

import argparse
import json
import sys
import threading
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import kuhaku_pick
from kuhaku_log import report
from kuhaku_webapp import GTK, WebApp


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
            work(lambda: app.later(app.js, "setPeek", kuhaku_pick.peek(m.get("path") or "")))
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
