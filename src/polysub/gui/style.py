"""Look and feel: a macOS-style layout (sidebar, page titles, grouped cards) on top
of the native controls. Only containers and labels are styled; buttons, combos
and fields keep the platform look. Colors come from the palette, so light and
dark mode both work."""
import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
                               QWidget)

GREEN, RED, ORANGE, GREY = QColor("#30A14E"), QColor("#E5484D"), QColor("#E8900C"), QColor("#8E8E93")


def is_dark(pal: QPalette = None) -> bool:
    pal = pal or QApplication.palette()
    return pal.color(QPalette.Window).lightness() < 128


def sidebar_color(pal: QPalette = None) -> QColor:
    pal = pal or QApplication.palette()
    w = pal.color(QPalette.Window)
    return w.lighter(118) if is_dark(pal) else w.darker(104)


def stylesheet() -> str:
    pal = QApplication.palette()
    dark = is_dark(pal)
    card = pal.color(QPalette.Base) if not dark else pal.color(QPalette.Window).lighter(125)
    line = QColor(255, 255, 255, 28) if dark else QColor(0, 0, 0, 22)
    rgba = lambda c: f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"  # noqa: E731
    return f"""
QWidget#sidebarPane {{ background: {sidebar_color(pal).name()}; }}
QFrame#sidebarLine {{ background: {rgba(line)}; border: none; }}
QListWidget#sidebar {{ background: transparent; border: none; outline: none; }}
QListWidget#sidebar::item {{ padding: 5px 6px; margin: 1px 10px; border-radius: 6px; }}
QListWidget#sidebar::item:selected {{ background: {rgba(line.darker(100) if dark else QColor(0, 0, 0, 30))};
    color: palette(text); }}
QFrame#card {{ background: {card.name()}; border: 1px solid {rgba(line)}; border-radius: 10px; }}
QFrame#hairline {{ background: {rgba(line)}; border: none; }}
QLabel#pageTitle {{ font-size: 20px; font-weight: 700; }}
QLabel#sectionTitle {{ font-weight: 600; padding-left: 4px; }}
QLabel[secondary="true"] {{ color: palette(placeholder-text); }}
QScrollArea#pageScroll {{ background: transparent; border: none; }}
QScrollArea#pageScroll > QWidget > QWidget {{ background: transparent; }}
QLabel#emptyTitle {{ font-size: 17px; font-weight: 600; }}
QFrame#dropzone {{ border: 2px dashed {rgba(line.darker(100) if dark else QColor(0, 0, 0, 45))}; border-radius: 14px; }}
QFrame#dropzone[hover="true"] {{ border-color: palette(highlight); }}
"""


# ---- building blocks --------------------------------------------------------

def secondary(text: str = "", small: bool = True, wrap: bool = True) -> QLabel:
    """Grey helper text."""
    lab = QLabel(text)
    lab.setProperty("secondary", True)
    lab.setWordWrap(wrap)
    if small:
        f = lab.font(); f.setPointSizeF(max(9.0, f.pointSizeF() - 1.5)); lab.setFont(f)
    return lab


def hairline() -> QFrame:
    f = QFrame(objectName="hairline")
    f.setFixedHeight(1)
    return f


def mini(w):
    """Small control size on macOS (no effect elsewhere)."""
    w.setAttribute(Qt.WA_MacSmallSize, True)
    return w


def page_title(text: str) -> QLabel:
    return QLabel(text, objectName="pageTitle")


class Card(QFrame):
    """A rounded group with rows separated by hairlines (System Settings style).
    add_row(label, widget, hint) puts the label on the left and the control on the right."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(14, 4, 14, 4)
        self._lay.setSpacing(0)
        self.title = title
        self._rows = 0

    def _sep(self):
        if self._rows:
            self._lay.addWidget(hairline())
        self._rows += 1

    def add_row(self, label: str, widget: QWidget = None, hint: str = "", stretch: bool = False) -> QWidget:
        self._sep()
        row = QWidget()
        h = QHBoxLayout(row); h.setContentsMargins(0, 9, 0, 9); h.setSpacing(12)
        left = QVBoxLayout(); left.setSpacing(2)
        lab = QLabel(label); left.addWidget(lab)
        if hint:
            left.addWidget(secondary(hint))
        h.addLayout(left, 1 if not stretch else 0)
        if widget is not None:
            if stretch:
                widget.setSizePolicy(QSizePolicy.Expanding, widget.sizePolicy().verticalPolicy())
                h.addWidget(widget, 2)
            else:
                h.addWidget(widget, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._lay.addWidget(row)
        row.label = lab
        return row

    def add_widget(self, widget: QWidget, margins=(0, 9, 0, 9)):
        self._sep()
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(*margins); v.addWidget(widget)
        self._lay.addWidget(box)
        return box

    def clear(self):
        while self._lay.count():
            w = self._lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._rows = 0


def section(title: str, card: QWidget, hint: str = "") -> QWidget:
    """Section title above a card, optional footnote below."""
    w = QWidget()
    v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(6)
    if title:
        v.addWidget(QLabel(title, objectName="sectionTitle"))
    v.addWidget(card)
    if hint:
        s = secondary(hint); s.setContentsMargins(4, 0, 4, 0); v.addWidget(s)
    return w


class StatusDot(QLabel):
    """Small colored dot for ok / error / neutral states."""

    def __init__(self, state=None, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.set(state)

    def set(self, state):
        self.setPixmap(dot_pixmap({True: GREEN, False: RED, None: GREY, "busy": ORANGE}[state]))


def dot_pixmap(color: QColor, size: int = 10) -> QPixmap:
    ratio = 2
    pm = QPixmap(size * ratio, size * ratio); pm.fill(Qt.transparent); pm.setDevicePixelRatio(ratio)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen); p.setBrush(color); p.drawEllipse(QRectF(1, 1, size - 2, size - 2)); p.end()
    return pm


# ---- icons (simple line drawings, tinted) -------------------------------------

def _draw(name: str, p: QPainter, s: float):
    """Draw icon `name` in a box of size s (line icons, round caps)."""
    if name == "settings":  # gear
        c = QPointF(s / 2, s / 2)
        path = QPainterPath()
        teeth, r1, r2 = 8, 0.30 * s, 0.40 * s
        for i in range(teeth * 2):
            a0 = math.pi * 2 * i / (teeth * 2)
            a1 = math.pi * 2 * (i + 1) / (teeth * 2)
            r = r2 if i % 2 == 0 else r1
            for a in (a0, a1):
                pt = QPointF(c.x() + r * math.cos(a), c.y() + r * math.sin(a))
                path.lineTo(pt) if path.elementCount() else path.moveTo(pt)
        path.closeSubpath()
        p.drawPath(path)
        p.drawEllipse(c, 0.12 * s, 0.12 * s)
    elif name == "server":
        for y in (0.16, 0.54):
            p.drawRoundedRect(QRectF(0.14 * s, y * s, 0.72 * s, 0.30 * s), 0.07 * s, 0.07 * s)
            p.drawPoint(QPointF(0.28 * s, (y + 0.15) * s))
    elif name == "check":  # checkmark in a circle
        p.drawEllipse(QPointF(s / 2, s / 2), 0.38 * s, 0.38 * s)
        p.drawPolyline([QPointF(0.33 * s, 0.52 * s), QPointF(0.45 * s, 0.64 * s), QPointF(0.68 * s, 0.38 * s)])
    elif name == "add":  # document with plus
        p.drawRoundedRect(QRectF(0.2 * s, 0.1 * s, 0.6 * s, 0.8 * s), 0.08 * s, 0.08 * s)
        p.drawLine(QPointF(0.5 * s, 0.34 * s), QPointF(0.5 * s, 0.66 * s))
        p.drawLine(QPointF(0.34 * s, 0.5 * s), QPointF(0.66 * s, 0.5 * s))
    elif name == "folder":
        path = QPainterPath(QPointF(0.1 * s, 0.26 * s))
        path.lineTo(0.38 * s, 0.26 * s); path.lineTo(0.46 * s, 0.34 * s); path.lineTo(0.9 * s, 0.34 * s)
        path.lineTo(0.9 * s, 0.8 * s); path.lineTo(0.1 * s, 0.8 * s); path.closeSubpath()
        p.drawPath(path)
    elif name == "pause":
        p.drawLine(QPointF(0.38 * s, 0.25 * s), QPointF(0.38 * s, 0.75 * s))
        p.drawLine(QPointF(0.62 * s, 0.25 * s), QPointF(0.62 * s, 0.75 * s))
    elif name == "play":
        path = QPainterPath(QPointF(0.34 * s, 0.22 * s))
        path.lineTo(0.78 * s, 0.5 * s); path.lineTo(0.34 * s, 0.78 * s); path.closeSubpath()
        p.drawPath(path)
    elif name == "film":  # empty-state picture: film frame with a play mark
        p.drawRoundedRect(QRectF(0.1 * s, 0.2 * s, 0.8 * s, 0.6 * s), 0.08 * s, 0.08 * s)
        path = QPainterPath(QPointF(0.44 * s, 0.38 * s))
        path.lineTo(0.6 * s, 0.5 * s); path.lineTo(0.44 * s, 0.62 * s); path.closeSubpath()
        p.drawPath(path)


def icon(name: str, color: QColor = None, size: int = 18, width: float = 1.6) -> QIcon:
    color = color or QApplication.palette().color(QPalette.Text)
    ic = QIcon()
    for ratio in (1, 2):
        pm = QPixmap(size * ratio, size * ratio); pm.fill(Qt.transparent); pm.setDevicePixelRatio(ratio)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(color, width); pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        if name == "tasks":
            _draw_tasks(p, size, color, width)
        else:
            _draw(name, p, size)
        p.end()
        ic.addPixmap(pm)
    return ic


def _draw_tasks(p: QPainter, s: float, color: QColor, width: float):
    for y in (0.28, 0.5, 0.72):
        p.setPen(Qt.NoPen); p.setBrush(color)
        p.drawEllipse(QPointF(0.2 * s, y * s), 0.07 * s, 0.07 * s)
        pen = QPen(color, width); pen.setCapStyle(Qt.RoundCap); p.setPen(pen); p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(0.36 * s, y * s), QPointF(0.84 * s, y * s))


ICON_SIZE = QSize(18, 18)
