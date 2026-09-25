"""PolySub main window."""
import os
import sys

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QScrollArea, QTabWidget

from .. import __version__, jobs
from ..config import load
from .common import open_file, reveal, tr
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
        self.setCentralWidget(self.tabs)
        self._menus()
        self.statusBar().showMessage(tr("配置：") + self.cfg.path, 4000)
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
        h = self.menuBar().addMenu(tr("帮助"))
        h.addAction(QAction(tr("关于 PolySub"), self, triggered=lambda: QMessageBox.about(
            self, "PolySub", f"PolySub {__version__}\n{tr('视频 → 任意语言字幕')}\n\n{tr('配置')}：{self.cfg.path}\n{tr('日志')}：{jobs.LOG}")))

    def reload_config(self):
        self.cfg = load()
        self.settings_changed(rebuild=True)

    def settings_changed(self, rebuild=True):
        """Config changed somewhere: refresh the pages that show it."""
        self.tasks.sync_quality()
        if rebuild:
            self.settings.build()
            self.endpoints.load()

    def closeEvent(self, e):
        QSettings("PolySub", "PolySub").setValue("geometry", self.saveGeometry())
        super().closeEvent(e)


def main(argv=None):
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("PolySub")
    app.setApplicationDisplayName("PolySub")
    w = MainWindow()
    w.show()
    paths = [p for p in (argv or sys.argv)[1:] if os.path.exists(p)]
    if paths:
        w.tasks.add_paths(paths)
    return app.exec()
