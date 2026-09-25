"""Look and feel: macOS-style building blocks (sidebar, page header, grouped cards,
switches) on top of the native controls.

No style sheets: a style sheet on a parent switches every child control to Qt's
style-sheet renderer, which loses the native look (scroll bars with arrow buttons,
square check boxes). Containers paint themselves instead, reading the palette at
paint time, so light / dark mode follows the system without any restyling.

Spacing and sizes come from the constants below; pages use them so every page
lines up the same way."""
import math

from PySide6.QtCore import Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QApplication, QFrame, QHBoxLayout, QLabel, QListWidget, QScrollArea,
                               QSizePolicy, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

# ---- design tokens ----------------------------------------------------------

PAGE_MARGINS = (28, 20, 28, 20)   # left, top, right, bottom of every page
SECTION_SPACING = 22              # between sections on a page
FORM_WIDTH = 720                  # max width of form pages (settings, models, service editor)
CARD_RADIUS = 10
CARD_PADDING = 16                 # left / right inside a card
ROW_PADDING = 10                  # top / bottom of a card row
CONTROL_WIDTH = 220               # popup buttons and short fields in card rows
SIDEBAR_WIDTH = 200
ICON_SIZE = QSize(18, 18)

# macOS system colors (light, dark)
_SYSTEM = {"green": ("#28A745", "#30D158"), "red": ("#E5484D", "#FF453A"), "orange": ("#F29100", "#FF9F0A"),
           "blue": ("#007AFF", "#0A84FF"), "grey": ("#8E8E93", "#98989D")}


def is_dark(pal: QPalette = None) -> bool:
    pal = pal or QApplication.palette()
    return pal.color(QPalette.Window).lightness() < 128


def system_color(name: str, pal: QPalette = None) -> QColor:
    return QColor(_SYSTEM[name][1 if is_dark(pal) else 0])


def separator_color(pal: QPalette = None) -> QColor:
    return QColor(255, 255, 255, 28) if is_dark(pal) else QColor(0, 0, 0, 20)


def content_color(pal: QPalette = None) -> QColor:
    """Background of the pages and dialogs: near white (light), near black (dark)."""
    return QColor("#1E1E20") if is_dark(pal) else QColor("#FAFAFC")


def card_color(pal: QPalette = None) -> QColor:
    return QColor("#2B2B2E") if is_dark(pal) else QColor("#FFFFFF")


def sidebar_color(pal: QPalette = None) -> QColor:
    return QColor("#262629") if is_dark(pal) else QColor("#F0F0F4")


def accent_color(pal: QPalette = None) -> QColor:
    pal = pal or QApplication.palette()
    return pal.color(QPalette.Highlight)


# sidebar icon tiles (System Settings style): white glyph on a colored rounded square
TILE_COLORS = {"tasks": "#0A84FF", "cube": "#AF52DE", "settings": "#8E8E93", "server": "#5E5CE6"}


# ---- text -------------------------------------------------------------------

def _font(size_delta: float = 0, weight=QFont.Normal) -> QFont:
    f = QFont(QApplication.font())
    f.setPointSizeF(f.pointSizeF() + size_delta)
    f.setWeight(weight)
    return f


def page_title(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setFont(_font(9, QFont.Bold))
    return lab


def section_title(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setFont(_font(0, QFont.DemiBold))
    lab.setContentsMargins(CARD_PADDING - 6, 0, 0, 0)
    return lab


def secondary(text: str = "", small: bool = True, wrap: bool = True) -> QLabel:
    """Grey helper text (the palette's secondary label color, light and dark)."""
    lab = QLabel(text)
    lab.setForegroundRole(QPalette.PlaceholderText)
    lab.setWordWrap(wrap)
    if small:
        lab.setFont(_font(-1.5))
    return lab


class ElidedLabel(QLabel):
    """One-line secondary label that shortens long text (paths) in the middle; full text as tooltip."""

    def __init__(self, text: str = "", small: bool = True):
        super().__init__()
        self.setForegroundRole(QPalette.PlaceholderText)
        if small:
            self.setFont(_font(-1.5))
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._full = ""
        self.setText(text)

    def setText(self, text: str):
        self._full = text or ""
        self.setToolTip(self._full)
        self._elide()

    def fullText(self) -> str:
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        w = max(40, self.width())
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideMiddle, w))


def mini(w):
    """Small control size on macOS (no effect elsewhere)."""
    w.setAttribute(Qt.WA_MacSmallSize, True)
    return w


# ---- containers --------------------------------------------------------------

class ContentPane(QWidget):
    """Plain widget painted with the content background (the page area and dialogs)."""

    def paintEvent(self, e):
        QPainter(self).fillRect(self.rect(), content_color(self.palette()))


def paint_content_background(widget: QWidget):
    """Give a dialog / top-level widget the content background (call from its paintEvent)."""
    QPainter(widget).fillRect(widget.rect(), content_color(widget.palette()))


class Hairline(QWidget):
    """1 px separator line (horizontal by default)."""

    def __init__(self, vertical: bool = False, inset: int = 0):
        super().__init__()
        self.vertical, self.inset = vertical, inset
        if vertical:
            self.setFixedWidth(1)
        else:
            self.setFixedHeight(1)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect().adjusted(self.inset, 0, 0, 0), separator_color(self.palette()))


def hairline() -> Hairline:
    return Hairline()


class Panel(QFrame):
    """Rounded container with a hairline border (cards, the task table, lists)."""

    def __init__(self, parent=None, radius: int = CARD_RADIUS):
        super().__init__(parent)
        self.radius = radius

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -1.5)
        if not is_dark(pal):   # soft shadow under the card
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 10))
            p.drawRoundedRect(r.translated(0, 1), self.radius, self.radius)
        p.setPen(QPen(separator_color(pal), 1))
        p.setBrush(card_color(pal))
        p.drawRoundedRect(r, self.radius, self.radius)


class Card(Panel):
    """A rounded group with rows separated by hairlines (System Settings style).
    add_row(label, widget, hint) puts the label (and a grey hint below it) on the left
    and the control on the right; stretch=True lets the control take the free width."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(CARD_PADDING, 2, CARD_PADDING, 2)
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
        row.setMinimumHeight(44)
        h = QHBoxLayout(row); h.setContentsMargins(0, ROW_PADDING, 0, ROW_PADDING); h.setSpacing(16)
        left = QVBoxLayout(); left.setSpacing(2); left.setContentsMargins(0, 0, 0, 0)
        left.addStretch(1)   # label (and hint) centered against the control
        lab = QLabel(label); left.addWidget(lab)
        row.hint = None
        if hint:
            row.hint = secondary(hint)
            left.addWidget(row.hint)
        left.addStretch(1)
        if widget is None:
            h.addLayout(left, 1)
        elif stretch:
            lab.setMinimumWidth(96)
            h.addLayout(left, 0)
            widget.setSizePolicy(QSizePolicy.Expanding, widget.sizePolicy().verticalPolicy())
            widget.setMinimumWidth(min(widget.minimumWidth() or 280, 280))
            h.addWidget(widget, 1, Qt.AlignVCenter)
        else:
            h.addLayout(left, 1)
            h.addWidget(widget, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._lay.addWidget(row)
        row.label = lab
        return row

    def add_widget(self, widget: QWidget, margins=(0, ROW_PADDING, 0, ROW_PADDING)):
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
    v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(8)
    if title:
        v.addWidget(section_title(title))
    v.addWidget(card)
    if hint:
        s = secondary(hint); s.setContentsMargins(CARD_PADDING - 6, 0, CARD_PADDING - 6, 0); v.addWidget(s)
    return w


class PageHeader(QWidget):
    """Page title (and an optional grey subtitle) on the left, accessory controls on the right."""

    def __init__(self, title: str, subtitle: str = "", *accessories: QWidget):
        super().__init__()
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(10)
        left = QVBoxLayout(); left.setSpacing(4)
        self.title = page_title(title)
        left.addWidget(self.title)
        self.subtitle = None
        if subtitle:
            self.subtitle = secondary(subtitle, small=False)
            left.addWidget(self.subtitle)
        h.addLayout(left, 1)
        for a in accessories:
            h.addWidget(a, 0, Qt.AlignVCenter)


def form_page(page: QWidget, width: int = FORM_WIDTH) -> QVBoxLayout:
    """Lay out a form page: the standard margins and a column of at most `width`,
    kept to the left like System Settings' detail pane. Returns the column layout."""
    outer = QHBoxLayout(page)
    outer.setContentsMargins(*PAGE_MARGINS)
    col = QWidget()
    col.setMaximumWidth(width)
    lay = QVBoxLayout(col); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(SECTION_SPACING)
    outer.addWidget(col, 1)
    outer.addStretch(0)
    return lay


def scroll_area(page: QWidget) -> QScrollArea:
    """Frameless scroll area that shows the window background (no white viewport)."""
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setFrameShape(QFrame.NoFrame)
    s.setWidget(page)
    for w in (s, s.viewport(), page):
        w.setAutoFillBackground(False)
    return s


# ---- controls -----------------------------------------------------------------

class Switch(QAbstractButton):
    """macOS-style on / off switch (drop-in for a check box: isChecked, setChecked, toggled)."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self.toggled.connect(self._animate)

    def sizeHint(self):
        return QSize(38, 22)

    def _get(self):
        return self._pos

    def _set(self, v):
        self._pos = v
        self.update()

    knob = Property(float, _get, _set)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on):
        super().setChecked(on)
        if not self.signalsBlocked():
            return
        self._pos = 1.0 if on else 0.0   # no animation when set programmatically with signals blocked
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        r = QRectF(0, 0, 38, 22).translated((self.width() - 38) / 2, (self.height() - 22) / 2)
        off = QColor(255, 255, 255, 40) if is_dark(pal) else QColor(0, 0, 0, 30)
        on = pal.color(QPalette.Highlight)
        track = QColor(off.red() + (on.red() - off.red()) * self._pos, off.green() + (on.green() - off.green()) * self._pos,
                       off.blue() + (on.blue() - off.blue()) * self._pos, int(off.alpha() + (255 - off.alpha()) * self._pos))
        if not self.isEnabled():
            track.setAlpha(track.alpha() // 2)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, 11, 11)
        d = 18
        x = r.left() + 2 + (r.width() - d - 4) * self._pos
        knob = QRectF(x, r.top() + 2, d, d)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(knob.translated(0, 0.6))
        p.setBrush(QColor("#FFFFFF") if self.isEnabled() else QColor("#DDDDDD"))
        p.drawEllipse(knob)
        if self.hasFocus():
            p.setPen(QPen(pal.color(QPalette.Highlight), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(-2, -2, 2, 2), 13, 13)


class StatusDot(QLabel):
    """Small colored dot for ok / error / neutral / busy states."""

    COLORS = {True: "green", False: "red", None: "grey", "busy": "orange"}

    def __init__(self, state=None, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.state = state

    def set(self, state):
        self.state = state
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(system_color(self.COLORS[self.state], self.palette()))
        p.drawEllipse(QRectF(2, 2, 10, 10))


def dot_pixmap(color: QColor, size: int = 10) -> QPixmap:
    ratio = 2
    pm = QPixmap(size * ratio, size * ratio); pm.fill(Qt.transparent); pm.setDevicePixelRatio(ratio)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen); p.setBrush(color); p.drawEllipse(QRectF(1, 1, size - 2, size - 2)); p.end()
    return pm


class DropFrame(QFrame):
    """Dashed rounded frame for the empty drop target; highlighted while something is dragged over it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hover = False

    def set_hover(self, on: bool):
        self.hover = on
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        c = pal.color(QPalette.Highlight) if self.hover else QColor(pal.color(QPalette.PlaceholderText))
        if not self.hover:
            c.setAlpha(110)
        pen = QPen(c, 2, Qt.DashLine)
        pen.setDashPattern([4, 3])
        p.setPen(pen)
        fill = QColor(pal.color(QPalette.Highlight)); fill.setAlpha(18)
        p.setBrush(fill if self.hover else Qt.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 14, 14)


# ---- sidebar (source list) ------------------------------------------------------

class SidebarPane(QWidget):
    """Sidebar background with a hairline on its right edge."""

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), sidebar_color(self.palette()))
        p.fillRect(self.width() - 1, 0, 1, self.height(), separator_color(self.palette()))


class _SourceDelegate(QStyledItemDelegate):
    def __init__(self, parent, tint_icons: bool):
        super().__init__(parent)
        self.tint = tint_icons

    def sizeHint(self, opt, index):
        return QSize(0, 34)

    def paint(self, p, opt, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        pal = opt.palette
        r = QRectF(opt.rect).adjusted(10, 1, -10, -1)
        selected = bool(opt.state & QStyle.State_Selected)
        if selected:   # accent-colored selection, as in the System Settings sidebar
            p.setPen(Qt.NoPen)
            p.setBrush(accent_color(pal))
            p.drawRoundedRect(r, 7, 7)
        x = r.left() + 6
        ic = index.data(Qt.DecorationRole)
        tile = index.data(Qt.UserRole + 1)
        if isinstance(ic, QIcon) and not ic.isNull():
            box = QRectF(x, r.center().y() - 11, 22, 22)
            if tile:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(tile))
                p.drawRoundedRect(box, 6, 6)
                ic.paint(p, box.adjusted(3, 3, -3, -3).toRect())
            else:
                ic.paint(p, box.toRect())
            x += 32
        p.setPen(QColor("#FFFFFF") if selected else pal.color(QPalette.Text))
        p.setFont(opt.font)
        p.drawText(QRectF(x, r.top(), r.right() - x - 6, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   opt.fontMetrics.elidedText(index.data(Qt.DisplayRole) or "", Qt.ElideRight, int(r.right() - x - 6)))
        p.restore()


class SourceList(QListWidget):
    """Transparent list with rounded grey selection rows (Finder / System Settings sidebar)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setItemDelegate(_SourceDelegate(self, True))
        self.setAutoFillBackground(False)
        self.viewport().setAutoFillBackground(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setUniformItemSizes(True)


# ---- icons (simple line drawings, tinted) ---------------------------------------

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
    elif name == "cube":  # models: a box
        top = [QPointF(0.5 * s, 0.14 * s), QPointF(0.84 * s, 0.32 * s), QPointF(0.5 * s, 0.5 * s),
               QPointF(0.16 * s, 0.32 * s)]
        path = QPainterPath(top[0])
        for pt in top[1:] + [top[0]]:
            path.lineTo(pt)
        p.drawPath(path)
        p.drawLine(top[3], QPointF(0.16 * s, 0.68 * s))
        p.drawLine(QPointF(0.16 * s, 0.68 * s), QPointF(0.5 * s, 0.86 * s))
        p.drawLine(QPointF(0.5 * s, 0.86 * s), QPointF(0.84 * s, 0.68 * s))
        p.drawLine(QPointF(0.84 * s, 0.68 * s), top[1])
        p.drawLine(top[2], QPointF(0.5 * s, 0.86 * s))
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
