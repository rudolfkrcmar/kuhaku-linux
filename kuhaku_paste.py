"""Pasting never crashes a page.

WebKitGTK for GTK 3 (2.54) dies when asked to paste while nothing owns the clipboard: after
the app that copied has closed, or before anything was copied. The home screen then goes black
until it starts again. guard(view) makes such a paste do nothing instead: the keys (Ctrl+V,
Cmd+V, Ctrl+Shift+V, Shift+Insert) are let through only when the clipboard has an owner, and
the right-click menu offers Paste only then. GTK 4's WebKit is not affected.
"""

from gi.repository import Gdk, Gtk

from kuhaku_log import report

_PASTE_KEYS = {"v", "V"}
_PASTE_ACTIONS = ("PASTE", "PASTE_AS_PLAIN_TEXT")


def clipboard_empty():
    """True when nothing owns the clipboard (a paste now would crash WebKitGTK 3)."""
    try:
        ok, _targets = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_targets()
        return not ok
    except Exception:
        report()
        return False


def is_paste(ev):
    name = Gdk.keyval_name(ev.keyval) or ""
    cmd = ev.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK)
    return bool((cmd and name in _PASTE_KEYS) or (ev.state & Gdk.ModifierType.SHIFT_MASK and name == "Insert"))


def _on_key(_view, ev):
    return is_paste(ev) and clipboard_empty()  # True: swallowed, WebKit never sees it


def _on_menu(_view, menu, *_args):
    try:
        from gi.repository import WebKit2

        actions = {getattr(WebKit2.ContextMenuAction, a) for a in _PASTE_ACTIONS}
        items = [i for i in menu.get_items() if i.get_stock_action() in actions]
        if items and clipboard_empty():
            for i in items:
                menu.remove(i)
    except Exception:
        report()
    return False


def guard(view):
    """Makes paste into this WebKit2 (GTK 3) view safe. Returns the view."""
    view.connect("key-press-event", _on_key)
    view.connect("context-menu", _on_menu)
    return view
