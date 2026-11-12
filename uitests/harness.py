"""Drives Kūhaku's pages off-screen, the way a person would, and compares what they show.

A Page loads an HTML page in an off-screen WebKit view, listens on the same message handler
the app listens on (so what the page sends is recorded, not acted on), runs scripts, clicks,
types and takes screenshots. Screenshots are compared with uitests/shots/<name>.png:

    KUHAKU_SHOTS=update   write the current look as the expected one
    KUHAKU_SHOTS=skip     do not compare (CI: other fonts render other pixels)

Nothing here needs the rest of Kūhaku running.
"""

import json
import os
import unittest
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk, WebKit2

ROOT = Path(__file__).resolve().parent.parent
SHOTS = Path(__file__).resolve().parent / "shots"
WIDTH, HEIGHT = 1280, 800


class Page:
    def __init__(self, path, handler, html=None, query=""):
        self.messages, self.errors = [], []
        self.win = Gtk.OffscreenWindow()
        self.win.set_default_size(WIDTH, HEIGHT)
        settings = WebKit2.Settings()
        settings.set_allow_file_access_from_file_urls(True)
        ucm = WebKit2.UserContentManager()
        ucm.register_script_message_handler(handler)
        ucm.connect(f"script-message-received::{handler}", self._message)
        ucm.register_script_message_handler("uitest")
        ucm.connect("script-message-received::uitest", lambda _m, r: self.errors.append(r.get_js_value().to_string()))
        ucm.add_script(
            WebKit2.UserScript.new(
                "window.addEventListener('error', e => window.webkit.messageHandlers.uitest.postMessage("
                "String(e.message) + ' (line ' + e.lineno + ')'));",
                WebKit2.UserContentInjectedFrames.TOP_FRAME,
                WebKit2.UserScriptInjectionTime.START,
                None,
                None,
            )
        )
        # The same moment every run (a clock on the page would change the screenshots).
        ucm.add_script(
            WebKit2.UserScript.new(
                "(() => { const T = new Date(2026, 10, 12, 9, 41, 0).getTime(), D = Date;"
                " class Frozen extends D { constructor(...a) { super(...(a.length ? a : [T])); }"
                " static now() { return T; } }"
                " window.Date = Frozen; })();",
                WebKit2.UserContentInjectedFrames.TOP_FRAME,
                WebKit2.UserScriptInjectionTime.START,
                None,
                None,
            )
        )
        self.view = WebKit2.WebView(user_content_manager=ucm, web_context=WebKit2.WebContext.new_ephemeral())
        self.view.set_settings(settings)
        self.win.add(self.view)
        self.win.show_all()
        uri = (ROOT / path).as_uri()
        loop = GLib.MainLoop()
        self.view.connect("load-changed", lambda _v, e: e == WebKit2.LoadEvent.FINISHED and loop.quit())
        if html is not None:
            self.view.load_html(html, uri)
        else:
            self.view.load_uri(uri + query)
        GLib.timeout_add(15000, loop.quit)
        loop.run()
        self.wait(300)

    def _message(self, _m, result):
        try:
            self.messages.append(json.loads(result.get_js_value().to_string()))
        except ValueError:
            self.messages.append(result.get_js_value().to_string())

    def wait(self, ms):
        loop = GLib.MainLoop()
        GLib.timeout_add(ms, lambda: loop.quit() or False)
        loop.run()

    def js(self, code):
        """Runs code on the page and returns its value (JSON-able), waiting for it."""
        out, loop = {}, GLib.MainLoop()

        def done(view, res):
            try:
                out["v"] = json.loads(view.evaluate_javascript_finish(res).to_json(0) or "null")
            except Exception as e:
                out["e"] = str(e)
            loop.quit()

        self.view.evaluate_javascript(
            f"JSON.parse(JSON.stringify((() => {{ {code} }})() ?? null))", -1, None, None, None, done
        )
        loop.run()
        if "e" in out:
            raise AssertionError(f"page script failed: {out['e']}")
        return out.get("v")

    def click(self, selector, **opts):
        """A real click event on the first element matching selector (shiftKey, ctrlKey… in opts)."""
        kind = opts.pop("kind", "click")
        found = self.js(
            f"const el = document.querySelector({json.dumps(selector)}); if (!el) return false;"
            f"const r = el.getBoundingClientRect();"
            f"el.dispatchEvent(new MouseEvent({json.dumps(kind)}, {{bubbles: true, cancelable: true, clientX: r.x + 5,"
            f" clientY: r.y + 5, button: {2 if kind == 'contextmenu' else 0}, ...{json.dumps(opts)}}})); return true;"
        )
        if not found:
            raise AssertionError(f"nothing on the page matches {selector}")
        self.wait(120)

    def key(self, key, **opts):
        self.js(
            f"document.dispatchEvent(new KeyboardEvent('keydown', {{key: {json.dumps(key)}, bubbles: true,"
            f" cancelable: true, ...{json.dumps(opts)}}})); return true;"
        )
        self.wait(120)

    def type_into(self, selector, text):
        self.js(
            f"const el = document.querySelector({json.dumps(selector)}); el.focus(); el.value = {json.dumps(text)};"
            "el.dispatchEvent(new Event('input', {bubbles: true})); return true;"
        )
        self.wait(80)

    def screenshot(self):
        out, loop = {}, GLib.MainLoop()

        def done(view, res):
            out["s"] = view.get_snapshot_finish(res)
            loop.quit()

        self.view.get_snapshot(WebKit2.SnapshotRegion.VISIBLE, WebKit2.SnapshotOptions.NONE, None, done)
        loop.run()
        return out["s"]

    def close(self):
        self.win.destroy()


class PageCase(unittest.TestCase):
    def tearDown(self):
        page = getattr(self, "page", None)
        if page:
            self.assertEqual(page.errors, [], "script errors on the page")
            page.close()

    def assertLooksLike(self, name, tolerance=0.002):
        """Compares a screenshot with shots/<name>.png: at most `tolerance` of the pixels may differ."""
        mode = os.environ.get("KUHAKU_SHOTS", "")
        if mode == "skip":
            return
        surface = self.page.screenshot()
        SHOTS.mkdir(exist_ok=True)
        expected = SHOTS / f"{name}.png"
        if mode == "update" or not expected.exists():
            surface.write_to_png(str(expected))
            if mode != "update":
                self.skipTest(f"first run: saved {expected.name} as the expected look")
            return
        now = SHOTS / f"{name}.now.png"
        surface.write_to_png(str(now))
        a = GdkPixbuf.Pixbuf.new_from_file(str(expected))
        b = GdkPixbuf.Pixbuf.new_from_file(str(now))
        self.assertEqual((a.get_width(), a.get_height()), (b.get_width(), b.get_height()), "size changed")
        pa, pb = a.get_pixels(), b.get_pixels()
        step = a.get_n_channels()
        differ = sum(1 for i in range(0, len(pa), step) if max(abs(pa[i + k] - pb[i + k]) for k in range(3)) > 24)
        share = differ / (a.get_width() * a.get_height())
        if share > tolerance:
            self.fail(f"{name} looks different: {share:.2%} of pixels changed (see shots/{name}.now.png)")
        now.unlink()
