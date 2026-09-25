"""PolySub main window: sidebar navigation (macOS source-list style), unified
toolbar with the queue actions, pages on the right."""
import os
import sys

from PySide6.QtCore import QEvent, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QListWidgetItem, QMainWindow, QMessageBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__, jobs, watch
from ..config import load
from . import style
from .widgets import open_file, reveal, tr
from .environment import EnvironmentPage
from .endpoints import EndpointsPage
from .settings import SettingsPage
from .tasks import TasksPage
from .downloads import Downloader
from .welcome import WelcomeDialog, needs_welcome

PAGES = [("tasks", tr("任务"), "tasks"), ("environment", tr("模型"), "cube"),
         ("settings", tr("设置"), "settings"), ("endpoints", tr("自定义服务"), "server")]


def _scroll(page):
    return style.scroll_area(page)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PolySub")
        self.setUnifiedTitleAndToolBarOnMac(True)
        self.cfg = load()
        self.downloads = Downloader(lambda: self.cfg.general.download_source)
        self.tasks = TasksPage(self)
        self.settings = SettingsPage(self)
        self.endpoints = EndpointsPage(self)
        self.environment = EnvironmentPage(self)

        self.stack = QStackedWidget()
        for w in (self.tasks, _scroll(self.environment), _scroll(self.settings), self.endpoints):
            self.stack.addWidget(w)
        content = style.ContentPane(); cv = QVBoxLayout(content); cv.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self.stack)
        self.nav = style.SourceList()
        self.nav.setIconSize(style.ICON_SIZE)
        self.nav.setFocusPolicy(Qt.NoFocus)
        for key, label, ic in PAGES:
            it = QListWidgetItem(label); it.setData(Qt.UserRole, key)
            it.setData(Qt.UserRole + 1, style.TILE_COLORS[ic])
            self.nav.addItem(it)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        pane = style.SidebarPane()
        pane.setFixedWidth(style.SIDEBAR_WIDTH)
        pv = QVBoxLayout(pane); pv.setContentsMargins(0, 12, 1, 12); pv.addWidget(self.nav)

        body = QWidget(); h = QHBoxLayout(body); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        h.addWidget(pane); h.addWidget(content, 1)
        self.setCentralWidget(body)
        self._menus()
        self._restyle()
        self.nav.setCurrentRow(0)
        self.watch_timer = QTimer(self, interval=15000, timeout=self.scan_watch_dir)
        self.watch_timer.start()
        s = QSettings("PolySub", "PolySub")
        if s.value("geometry"):
            self.restoreGeometry(s.value("geometry"))
        else:
            self.resize(1060, 680)

    def _restyle(self):
        """Icons are pixmaps, so they are redrawn on a light / dark switch; everything else paints from the palette."""
        for i, (_, _, ic) in enumerate(PAGES):
            self.nav.item(i).setIcon(style.icon(ic, QColor("#FFFFFF"), size=16, width=1.7))
        self.tasks.restyle()

    def changeEvent(self, e):
        if e.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange) and not getattr(self, "_in_restyle", False):
            self._in_restyle = True  # light/dark switch: recolor the sidebar, cards and icons
            try:
                self._restyle()
            finally:
                self._in_restyle = False
        super().changeEvent(e)

    def scan_watch_dir(self):
        """Settings › 自动化 › 监视文件夹: queue videos that newly appear there."""
        folder = self.cfg.general.watch_dir
        if folder:
            new = watch.scan(folder)
            if new:
                self.tasks.add_paths(new)

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
        if not getattr(self, "_welcomed", False):
            self._welcomed = True
            if needs_welcome(self.cfg):
                QTimer.singleShot(300, self._welcome)

    def _welcome(self):
        # an application-modal window, not a sheet: macOS refuses to close a window that has
        # a sheet attached, which silently cancels ⌘Q while the dialog is open
        d = WelcomeDialog(self)
        d.setWindowModality(Qt.ApplicationModal)
        d.setWindowFlag(Qt.Sheet, False)
        d.show()

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
        # an open sheet (e.g. the first-run dialog) makes macOS refuse to close the window,
        # which cancels ⌘Q; close them first
        for d in self.findChildren(QDialog):
            if d.isVisible():
                d.done(QDialog.Rejected)
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
