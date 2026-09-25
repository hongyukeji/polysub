"""PolySub main window: sidebar navigation (macOS source-list style), unified
toolbar with the queue actions, pages on the right."""
import os
import sys

from PySide6.QtCore import QEvent, QSettings, QSize, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QScrollArea, QSizePolicy, QStackedWidget, QToolBar, QVBoxLayout,
                               QWidget)

from .. import __version__, jobs
from ..config import load
from . import style
from .widgets import open_file, reveal, tr
from .environment import EnvironmentPage
from .endpoints import EndpointsPage
from .settings import SettingsPage
from .tasks import TasksPage

PAGES = [("tasks", tr("任务"), "tasks"), ("settings", tr("设置"), "settings"),
         ("endpoints", tr("模型服务"), "server"), ("environment", tr("环境检查"), "check")]


def _scroll(page):
    s = QScrollArea(objectName="pageScroll")
    s.setWidgetResizable(True); s.setFrameShape(QFrame.NoFrame); s.setWidget(page)
    return s


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PolySub")
        self.setUnifiedTitleAndToolBarOnMac(True)
        self.cfg = load()
        self.tasks = TasksPage(self)
        self.settings = SettingsPage(self)
        self.endpoints = EndpointsPage(self)
        self.environment = EnvironmentPage(self)

        self.stack = QStackedWidget()
        for w in (self.tasks, _scroll(self.settings), self.endpoints, _scroll(self.environment)):
            self.stack.addWidget(w)
        self.nav = QListWidget(objectName="sidebar")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setFocusPolicy(Qt.NoFocus)
        for key, label, _ in PAGES:
            it = QListWidgetItem(label); it.setData(Qt.UserRole, key); it.setSizeHint(QSize(0, 32))
            self.nav.addItem(it)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        pane = QWidget(objectName="sidebarPane"); pane.setAttribute(Qt.WA_StyledBackground, True)
        pane.setFixedWidth(190)
        pv = QVBoxLayout(pane); pv.setContentsMargins(0, 10, 0, 10); pv.addWidget(self.nav)
        line = QFrame(objectName="sidebarLine"); line.setFixedWidth(1)

        body = QWidget(); h = QHBoxLayout(body); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        h.addWidget(pane); h.addWidget(line); h.addWidget(self.stack, 1)
        self.setCentralWidget(body)
        self._toolbar()
        self._menus()
        self._restyle()
        self.nav.setCurrentRow(0)
        s = QSettings("PolySub", "PolySub")
        if s.value("geometry"):
            self.restoreGeometry(s.value("geometry"))
        else:
            self.resize(1060, 680)

    def _toolbar(self):
        tb = QToolBar(tr("工具栏"), objectName="toolbar")
        tb.setMovable(False); tb.setFloatable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.setIconSize(QSize(16, 16))
        tb.setContextMenuPolicy(Qt.PreventContextMenu)
        tb.addAction(self.tasks.add_act)
        tb.addAction(self.tasks.folder_act)
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        tb.addAction(self.tasks.pause_act)
        self.addToolBar(tb)
        self.toolbar = tb

    def _restyle(self):
        self.setStyleSheet(style.stylesheet())
        for i, (_, _, ic) in enumerate(PAGES):
            self.nav.item(i).setIcon(style.icon(ic, self.palette().color(self.palette().ColorRole.Highlight)))
        self.tasks.restyle()

    def changeEvent(self, e):
        if e.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange) and not getattr(self, "_in_restyle", False):
            self._in_restyle = True  # light/dark switch: recolor the sidebar, cards and icons
            try:
                self._restyle()
            finally:
                self._in_restyle = False
        super().changeEvent(e)

    def show_page(self, key: str):
        self.nav.setCurrentRow([k for k, _, _ in PAGES].index(key))

    def _menus(self):
        m = self.menuBar().addMenu(tr("文件"))
        m.addAction(self.tasks.add_act)
        m.addAction(self.tasks.folder_act)
        m.addSeparator()
        m.addAction(QAction(tr("打开配置文件"), self, triggered=lambda: open_file(self.cfg.path)))
        m.addAction(QAction(tr("在 Finder 中显示日志"), self, triggered=lambda: reveal(jobs.LOG)))
        if getattr(sys, "frozen", False):
            m.addSeparator()
            m.addAction(QAction(tr("安装命令行工具 polysub…"), self, triggered=self.install_cli))
        # macOS moves these two into the application menu (PolySub → 设置… ⌘, / 关于 PolySub)
        prefs = QAction(tr("设置…"), self, shortcut=QKeySequence.Preferences, triggered=lambda: self.show_page("settings"))
        prefs.setMenuRole(QAction.PreferencesRole); m.addAction(prefs)

        q = self.menuBar().addMenu(tr("队列"))
        q.addAction(self.tasks.pause_act)
        q.addSeparator()
        for a in (self.tasks.cancel_act, self.tasks.retry_act, self.tasks.remove_act, self.tasks.clear_act):
            q.addAction(a)

        v = self.menuBar().addMenu(tr("显示"))
        for i, (key, label, _) in enumerate(PAGES):
            v.addAction(QAction(label, self, shortcut=QKeySequence(f"Ctrl+{i + 1}"),
                                triggered=lambda _=False, k=key: self.show_page(k)))

        h = self.menuBar().addMenu(tr("帮助"))
        about = QAction(tr("关于 PolySub"), self, triggered=lambda: QMessageBox.about(
            self, "PolySub", f"PolySub {__version__}\n{tr('视频 → 任意语言字幕')}\n\n{tr('配置')}：{self.cfg.path}\n{tr('日志')}：{jobs.LOG}"))
        about.setMenuRole(QAction.AboutRole); h.addAction(about)

    def install_cli(self):
        """Link ~/.local/bin/polysub to this app's executable (it understands CLI arguments)."""
        bin_dir = os.path.expanduser("~/.local/bin")
        link = os.path.join(bin_dir, "polysub")
        try:
            os.makedirs(bin_dir, exist_ok=True)
            if os.path.islink(link) or os.path.exists(link):
                os.remove(link)
            os.symlink(sys.executable, link)
        except OSError as e:
            QMessageBox.warning(self, "PolySub", str(e))
            return
        on_path = bin_dir in os.environ.get("PATH", "").split(os.pathsep)
        QMessageBox.information(self, "PolySub", tr("已安装：{l}\n在终端里运行 polysub --help 查看用法。").format(l=link) +
                                ("" if on_path else "\n" + tr("注意：{d} 不在 PATH 里，需要加到 shell 配置中。").format(d=bin_dir)))

    def flash(self, msg: str):
        self.tasks.flash(msg)

    def showEvent(self, e):
        super().showEvent(e)
        self.tasks.table.setFocus()  # no focus ring on the first field / button

    def reload_config(self):
        self.cfg = load()
        self.settings_changed(rebuild=True)

    def settings_changed(self, rebuild=True):
        """Config changed somewhere: refresh the pages that show it."""
        self.tasks.sync_quality()
        if rebuild:
            self.settings.build()
            self.endpoints.load()
        self.environment.run_checks()

    def closeEvent(self, e):
        QSettings("PolySub", "PolySub").setValue("geometry", self.saveGeometry())
        super().closeEvent(e)


class App(QApplication):
    """Receives files dropped on the Dock icon / opened with the app (macOS FileOpen events)."""

    def __init__(self, argv):
        super().__init__(argv)
        self.window = None
        self.pending = []

    def event(self, e):
        if e.type() == QEvent.FileOpen:
            path = e.file()
            if self.window:
                self.window.tasks.add_paths([path])
                self.window.raise_(); self.window.activateWindow()
            else:
                self.pending.append(path)
            return True
        return super().event(e)


def main(argv=None):
    app = App(sys.argv if argv is None else argv)
    app.setApplicationName("PolySub")
    app.setApplicationDisplayName("PolySub")
    w = MainWindow()
    app.window = w
    w.show()
    if app.pending:
        w.tasks.add_paths(app.pending)
    paths = [p for p in (argv or sys.argv)[1:] if os.path.exists(p)]
    if paths:
        w.tasks.add_paths(paths)
    return app.exec()
