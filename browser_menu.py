"""Mado's right-click menu in Kūhaku's look: a quiet sheet with only what fits the spot.

A link offers its tabs and its address, an image its copy and download, a text field
its editing, a selection its copy and a search; anywhere else back, forward, reload,
and closing every tab (Ctrl+Shift+W).
"""

import urllib.parse

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWebEngineCore import QWebEngineContextMenuRequest, QWebEnginePage
from PyQt6.QtWidgets import QMenu

from browser_web import SEARCH

A = QWebEnginePage.WebAction
Flag = QWebEngineContextMenuRequest.EditFlag
Media = QWebEngineContextMenuRequest.MediaType


def style(night):
    """The same sheet as Miru's menu (.ctx): white or near-black, round, light Inter."""
    sheet, fg, muted, faint = (
        ("#1b1b1b", "#ececec", "#9a9a9a", "rgba(236,236,236,18)")
        if night
        else ("#ffffff", "#111111", "#666666", "rgba(17,17,17,18)")
    )
    return f"""
    QMenu {{ background:{sheet}; color:{fg}; border:1px solid {faint}; border-radius:12px; padding:8px;
             font-family:'Inter Variable','Inter',sans-serif; font-size:14px; font-weight:300; }}
    QMenu::item {{ padding:7px 22px 7px 12px; border-radius:8px; background:transparent; }}
    QMenu::item:selected {{ background:{faint}; }}
    QMenu::item:disabled {{ color:{muted}; }}
    QMenu::separator {{ height:1px; background:{faint}; margin:6px 4px; }}
    """


def short(text, n=28):
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


class Menu(QMenu):
    def __init__(self, parent, night):
        super().__init__(parent)
        # No frame or shadow from the window manager, so the corners can be round.
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(style(night))
        self.wants_line = False

    def item(self, label, fn, keys=None, enabled=True):
        if self.wants_line and self.actions():
            self.addSeparator()
        self.wants_line = False
        act = QAction(label, self)
        if keys:
            act.setShortcut(QKeySequence(keys))  # shown on the right; the window's own key does the work
            act.setShortcutVisibleInContextMenu(True)
        act.setEnabled(enabled)
        act.triggered.connect(fn)
        self.addAction(act)
        return act

    def line(self):
        self.wants_line = True


def show(browser, view, pos: QPoint):
    """Builds and shows the menu for what was right-clicked in this tab."""
    req = view.lastContextMenuRequest()
    if req is None:
        return
    page = view.page()
    act = page.triggerAction
    m = Menu(view, browser.night)
    link = req.linkUrl()
    media = req.mediaType()
    sel = req.selectedText().strip()
    flags = req.editFlags()
    private = browser.key_of_page(page) == "private"

    if link.isValid() and not link.isEmpty():
        url = link.toString()
        m.item("Open link in new tab", lambda: browser.new_tab(url, focus=False, key=browser.key_of_page(page)))
        if not private:
            m.item("Open link in private tab", lambda: browser.open([url], private=True))
        m.item("Copy link address", lambda: act(A.CopyLinkToClipboard))
        m.line()

    if media == Media.MediaTypeImage:
        src = req.mediaUrl().toString()
        m.item("Open image in new tab", lambda: browser.new_tab(src, key=browser.key_of_page(page)))
        m.item("Copy image", lambda: act(A.CopyImageToClipboard))
        m.item("Copy image address", lambda: act(A.CopyImageUrlToClipboard))
        m.item("Save image", lambda: act(A.DownloadImageToDisk))
        m.line()
    elif media in (Media.MediaTypeVideo, Media.MediaTypeAudio):
        m.item("Play or pause", lambda: act(A.ToggleMediaPlayPause))
        m.item("Copy address", lambda: act(A.CopyMediaUrlToClipboard))
        m.item("Save", lambda: act(A.DownloadMediaToDisk))
        m.line()

    if req.isContentEditable():
        m.item("Undo", lambda: act(A.Undo), "Ctrl+Z", bool(flags & Flag.CanUndo))
        m.item("Redo", lambda: act(A.Redo), "Ctrl+Shift+Z", bool(flags & Flag.CanRedo))
        m.line()
        m.item("Cut", lambda: act(A.Cut), "Ctrl+X", bool(flags & Flag.CanCut))
        m.item("Copy", lambda: act(A.Copy), "Ctrl+C", bool(flags & Flag.CanCopy))
        m.item("Paste", lambda: act(A.Paste), "Ctrl+V", bool(flags & Flag.CanPaste))
        m.item("Select all", lambda: act(A.SelectAll), "Ctrl+A", bool(flags & Flag.CanSelectAll))
        m.line()
    elif sel:
        m.item("Copy", lambda: act(A.Copy), "Ctrl+C")
        m.item(
            f"Search for “{short(sel)}”",
            lambda: browser.new_tab(SEARCH + urllib.parse.quote_plus(sel), key=browser.key_of_page(page)),
        )
        m.line()

    if not m.actions():  # the page itself
        m.item("Back", view.back, "Alt+Left", view.history().canGoBack())
        m.item("Forward", view.forward, "Alt+Right", view.history().canGoForward())
        m.item("Reload", view.reload, "Ctrl+R")
        m.line()
        m.item("New tab", browser.new_tab, "Ctrl+T")
        m.item("Close tab", lambda: browser.close_tab(view), "Ctrl+W")
        m.item("Close all tabs", browser.close_all, "Ctrl+Shift+W", len(browser.tabs) > 1)
        m.line()
        m.item("Page source", lambda: browser.new_tab("view-source:" + view.url().toString()), "Ctrl+U")

    if browser.mode == "developer":
        m.line()
        m.item("Inspect", lambda: act(A.InspectElement))

    m.popup(view.mapToGlobal(pos))
