"""PolySub main window."""
import os
import sys

from PySide6.QtCore import QEvent, QSettings
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QScrollArea, QTabWidget

from .. import __version__, jobs
from ..config import load
from .common import open_file, reveal, tr
from .doctor import DoctorPage
from .settings import EndpointsPage, SettingsPage
from .tasks import TasksPage


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PolySub")
        self.cfg = load()
        self.tabs = QTabWidget()
        self.tasks = TasksPage(self)
        self.settings = SettingsPage(self)
        self.endpoints = EndpointsPage(self)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(self.settings)
        self.tabs.addTab(self.tasks, tr("任务"))
        self.tabs.addTab(scroll, tr("设置"))
        self.tabs.addTab(self.endpoints, tr("接口"))
        self.doctor = DoctorPage(self)
        self.tabs.addTab(self.doctor, tr("环境"))
        self.setCentralWidget(self.tabs)
        self._menus()
        s = QSettings("PolySub", "PolySub")
        if s.value("geometry"):
            self.restoreGeometry(s.value("geometry"))
        else:
            self.resize(980, 620)

    def _menus(self):
        m = self.menuBar().addMenu(tr("文件"))
        a = QAction(tr("添加视频…"), self, shortcut=QKeySequence.Open, triggered=self.tasks.add_files); m.addAction(a)
        m.addAction(QAction(tr("添加文件夹…"), self, triggered=self.tasks.add_folder))
        m.addSeparator()
        m.addAction(QAction(tr("打开配置文件"), self, triggered=lambda: open_file(self.cfg.path)))
        m.addAction(QAction(tr("在 Finder 中显示日志"), self, triggered=lambda: reveal(jobs.LOG)))
        if getattr(sys, "frozen", False):
            m.addSeparator()
            m.addAction(QAction(tr("安装命令行工具 polysub…"), self, triggered=self.install_cli))
        h = self.menuBar().addMenu(tr("帮助"))
        h.addAction(QAction(tr("关于 PolySub"), self, triggered=lambda: QMessageBox.about(
            self, "PolySub", f"PolySub {__version__}\n{tr('视频 → 任意语言字幕')}\n\n{tr('配置')}：{self.cfg.path}\n{tr('日志')}：{jobs.LOG}")))

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
        self.tabs.setFocus()  # no focus ring on the first field / button

    def reload_config(self):
        self.cfg = load()
        self.settings_changed(rebuild=True)

    def settings_changed(self, rebuild=True):
        """Config changed somewhere: refresh the pages that show it."""
        self.tasks.sync_quality()
        if rebuild:
            self.settings.build()
            self.endpoints.load()
        if hasattr(self, "doctor"):
            self.doctor.run_checks()

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
