"""Mado's questions in Kūhaku's look, instead of Qt's stock boxes.

A web page's alert, confirm and prompt, and a site asking you to sign in, come up as a quiet
sheet over the page: the site's name small and grey, the question in light type, a hairline to
write on, and the answers as words (the main one ink, the other grey). A sign-in is always night,
as every password screen in Kūhaku is. `qt_style` also dresses whatever stock Qt window is left
(a colour picker, a save dialog, a tooltip) in the same paper and ink.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

DAY = {
    "bg": "#f7f4f1",
    "sheet": "#ffffff",
    "fg": "#111111",
    "muted": "#666666",
    "faint": "rgba(17,17,17,20)",
    "line": "rgba(17,17,17,46)",
    "accent": "#e0301e",
}
NIGHT = {
    "bg": "#121212",
    "sheet": "#1b1b1b",
    "fg": "#ececec",
    "muted": "#9a9a9a",
    "faint": "rgba(236,236,236,20)",
    "line": "rgba(236,236,236,46)",
    "accent": "#f0705a",
}
FONT = "font-family:'Inter Variable','Inter',sans-serif; font-weight:300;"


def qt_style(night):
    """Stock Qt windows in paper and ink: words for buttons, hairlines for fields, no boxes."""
    c = NIGHT if night else DAY
    return f"""
    QDialog, QMessageBox, QInputDialog, QFileDialog, QColorDialog {{ background:{c["bg"]}; color:{c["fg"]}; {FONT} font-size:14px; }}
    QDialog QLabel {{ color:{c["fg"]}; }}
    QDialog QPushButton {{ background:none; border:none; color:{c["muted"]}; padding:6px 0; margin-left:22px; font-size:15px; {FONT} }}
    QDialog QPushButton:hover, QDialog QPushButton:focus, QDialog QPushButton:default {{ color:{c["fg"]}; }}
    QDialog QLineEdit, QDialog QSpinBox, QDialog QComboBox {{ background:transparent; border:none; border-bottom:1px solid {c["line"]};
        color:{c["fg"]}; padding:4px 0; selection-background-color:{c["faint"]}; selection-color:{c["fg"]}; }}
    QDialog QLineEdit:focus {{ border-bottom:1px solid {c["fg"]}; }}
    QDialog QListView, QDialog QTreeView {{ background:{c["bg"]}; border:none; color:{c["fg"]};
        selection-background-color:{c["faint"]}; selection-color:{c["fg"]}; }}
    QDialog QHeaderView::section {{ background:{c["bg"]}; color:{c["muted"]}; border:none; border-bottom:1px solid {c["faint"]}; padding:4px 6px; }}
    QDialog QToolButton {{ background:none; border:none; }}
    QToolTip {{ background:{c["sheet"]}; color:{c["fg"]}; border:1px solid {c["faint"]}; padding:5px 9px; {FONT} font-size:12.5px; }}
    QScrollBar:vertical {{ background:transparent; width:6px; }} QScrollBar::handle:vertical {{ background:{c["line"]}; min-height:24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height:0; width:0; }}
    """


class Ask(QDialog):
    """One question on a sheet: who asks, what, maybe fields, and the answers as words."""

    def __init__(self, parent, night, who, text, answers, fields=()):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        c = NIGHT if night else DAY
        self.setStyleSheet(f"""
            QFrame#sheet {{ background:{c["sheet"]}; border:1px solid {c["faint"]}; border-radius:18px; }}
            QLabel {{ color:{c["fg"]}; {FONT} font-size:15px; background:none; border:none; }}
            QLabel#who {{ color:{c["muted"]}; font-size:12.5px; }}
            QLineEdit {{ background:transparent; border:none; border-bottom:1px solid {c["line"]}; color:{c["fg"]}; {FONT} font-size:15px;
                padding:5px 0; selection-background-color:{c["faint"]}; selection-color:{c["fg"]}; }}
            QLineEdit:focus {{ border-bottom:1px solid {c["fg"]}; }}
            QPushButton {{ background:none; border:none; color:{c["muted"]}; {FONT} font-size:15px; padding:4px 0; }}
            QPushButton:hover, QPushButton:focus {{ color:{c["fg"]}; }}
            QPushButton#main {{ color:{c["fg"]}; }}
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sheet = QFrame(self)
        sheet.setObjectName("sheet")
        outer.addWidget(sheet)
        box = QVBoxLayout(sheet)
        box.setContentsMargins(32, 26, 32, 22)
        box.setSpacing(12)
        if who:
            w = QLabel(who)
            w.setObjectName("who")
            box.addWidget(w)
        if text:
            t = QLabel(text)
            t.setWordWrap(True)
            t.setTextFormat(Qt.TextFormat.PlainText)
            t.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            box.addWidget(t)
        self.fields = []
        for placeholder, value, secret in fields:
            f = QLineEdit(value)
            f.setPlaceholderText(placeholder)
            if secret:
                f.setEchoMode(QLineEdit.EchoMode.Password)
            box.addWidget(f)
            self.fields.append(f)
        row = QHBoxLayout()
        row.setSpacing(26)
        row.setContentsMargins(0, 10, 0, 0)
        row.addStretch(1)
        for i, word in enumerate(answers):  # the last answer is the main one
            b = QPushButton(word)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if i == len(answers) - 1:
                b.setObjectName("main")
                b.setDefault(True)
                b.clicked.connect(self.accept)
            else:
                b.clicked.connect(self.reject)
            row.addWidget(b)
        box.addLayout(row)
        self.setMinimumWidth(440)
        self.setMaximumWidth(560)
        if self.fields:
            self.fields[0].setFocus()

    def showEvent(self, e):
        super().showEvent(e)
        p = self.parentWidget()
        if p:
            g = p.window().geometry()
            self.move(g.center().x() - self.width() // 2, g.top() + max(80, g.height() // 4))

    def values(self):
        return [f.text() for f in self.fields]


def who_of(url):
    host = url.host().removeprefix("www.") if url and url.host() else ""
    return host or "This page"


def alert(parent, night, url, text):
    Ask(parent, night, who_of(url), text, ["Close"]).exec()


def confirm(parent, night, url, text):
    return Ask(parent, night, who_of(url), text, ["Cancel", "Continue"]).exec() == QDialog.DialogCode.Accepted


def prompt(parent, night, url, text, default):
    d = Ask(parent, night, who_of(url), text, ["Cancel", "Continue"], [("", default or "", False)])
    ok = d.exec() == QDialog.DialogCode.Accepted
    return ok, (d.values()[0] if ok else "")


def sign_in(parent, url, realm, proxy=False):
    """A site (or proxy) asks for a name and password. Always night: it is a password screen."""
    host = url.host() if hasattr(url, "host") else str(url)
    title = f"Sign in to the proxy {host}" if proxy else f"Sign in to {host.removeprefix('www.')}"
    d = Ask(parent, True, realm or "", title, ["Cancel", "Sign in"], [("Name", "", False), ("Password", "", True)])
    if d.exec() != QDialog.DialogCode.Accepted:
        return None
    return d.values()
