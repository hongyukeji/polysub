"""Task page: drop videos, pick subtitle languages, watch the background queue."""
import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QMenu, QMessageBox, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
                               QToolButton, QVBoxLayout, QWidget)

from .. import jobs, langs
from ..config import save
from ..media import is_media
from .common import QUALITY, lang_label, open_file, quality_of, reveal, tr

STATUS = {"pending": tr("等待"), "running": tr("处理中"), "done": tr("完成"), "failed": tr("失败"),
          "cancelled": tr("已取消"), "skipped": tr("已跳过")}
COLS = [tr("视频"), tr("字幕语言"), tr("状态"), tr("进度"), tr("用时"), tr("说明")]


class LangPicker(QToolButton):
    """Button with a checkable language menu."""

    def __init__(self, selected, parent=None):
        super().__init__(parent)
        self.setPopupMode(QToolButton.InstantPopup)
        self.menu_ = QMenu(self)
        self.actions_ = {}
        for code in langs.LANGS:
            a = QAction(lang_label(code), self.menu_, checkable=True)
            a.setChecked(code in selected)
            a.toggled.connect(self._changed)
            self.menu_.addAction(a)
            self.actions_[code] = a
        self.setMenu(self.menu_)
        self._changed()

    def selected(self):
        return [c for c, a in self.actions_.items() if a.isChecked()]

    def _changed(self, *_):
        sel = self.selected()
        if not sel:  # keep at least one
            self.actions_["zh-Hans"].setChecked(True)
            return
        self.setText("、".join(langs.label(c) for c in sel) + "  ▾")


def _fmt_secs(s: float) -> str:
    s = int(s)
    return f"{s // 60}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


class TasksPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self.setAcceptDrops(True)
        cfg = window.cfg

        top = QHBoxLayout()
        top.addWidget(QLabel(tr("字幕语言：")))
        self.langs = LangPicker(cfg.general.target_langs)
        top.addWidget(self.langs)
        top.addSpacing(16)
        top.addWidget(QLabel(tr("翻译质量：")))
        self.quality = QComboBox()
        for k, (label, _, _) in QUALITY.items():
            self.quality.addItem(label, k)
        self.quality.addItem(tr("自定义（见设置）"), "custom")
        self.quality.setToolTip(tr("快速：不思考，一部 2 小时的片子约 5 分钟\n"
                                   "标准：少量思考，约 10 分钟，质量接近精细\n"
                                   "精细：不限思考，约 30 分钟"))
        self.quality.currentIndexChanged.connect(self._quality_changed)
        top.addWidget(self.quality)
        top.addStretch(1)
        b = QPushButton(tr("添加视频…")); b.clicked.connect(self.add_files); top.addWidget(b)
        b = QPushButton(tr("添加文件夹…")); b.clicked.connect(self.add_folder); top.addWidget(b)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3, 4):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setColumnWidth(3, 160)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.cellDoubleClicked.connect(lambda r, c: self._open_row(r))

        self.hint = QLabel(tr("把视频或文件夹拖到这里，或点右上角「添加视频」"))
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setStyleSheet("color: gray; padding: 24px;")

        bottom = QHBoxLayout()
        self.state = QLabel()
        bottom.addWidget(self.state)
        bottom.addStretch(1)
        self.pause_btn = QPushButton(); self.pause_btn.clicked.connect(self.toggle_pause)
        bottom.addWidget(self.pause_btn)
        for text, fn in ((tr("取消所选"), self.cancel_sel), (tr("重试所选"), self.retry_sel),
                         (tr("移除所选"), self.remove_sel), (tr("清除已结束"), self.clear_done)):
            b = QPushButton(text); b.clicked.connect(fn); bottom.addWidget(b)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.hint)
        lay.addLayout(bottom)

        self.rows = {}      # job id -> row
        self.jobs = []
        self.sync_quality()
        self.timer = QTimer(self, interval=1000, timeout=self.refresh)
        self.timer.start()
        self.refresh()

    # ---- settings shortcuts ------------------------------------------------
    def sync_quality(self):
        t = self.win.cfg.translate
        k = quality_of(t.think, t.think_budget)
        self.quality.blockSignals(True)
        self.quality.setCurrentIndex(self.quality.findData(k))
        self.quality.blockSignals(False)

    def _quality_changed(self):
        k = self.quality.currentData()
        if k == "custom":
            return
        _, think, budget = QUALITY[k]
        self.win.cfg.translate.think, self.win.cfg.translate.think_budget = think, budget
        save(self.win.cfg)
        self.win.settings_changed()

    # ---- adding ------------------------------------------------------------
    def add_paths(self, paths):
        targets = self.langs.selected()
        cfg = self.win.cfg
        if cfg.general.target_langs != targets:  # remember the choice
            cfg.general.target_langs = targets
            save(cfg)
        added = jobs.add(paths, targets)
        if not added:
            self.win.statusBar().showMessage(tr("没有新的视频（可能已在队列中，或不是视频文件）"), 5000)
        else:
            self.win.statusBar().showMessage(tr("已加入 {n} 个视频").format(n=len(added)), 5000)
            if not jobs.is_paused():
                jobs.start_background()
        self.refresh()

    def add_files(self):
        exts = " ".join(f"*{e}" for e in sorted({".mp4", ".mkv", ".mov", ".m4v", ".avi", ".wmv", ".flv", ".ts",
                                                   ".webm", ".mpg", ".mp3", ".m4a", ".wav", ".flac"}))
        files, _ = QFileDialog.getOpenFileNames(self, tr("选择视频"), "", f"{tr('视频和音频')} ({exts});;{tr('所有文件')} (*)")
        if files:
            self.add_paths(files)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, tr("选择文件夹（包含子文件夹里的视频）"))
        if d:
            self.add_paths([d])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if os.path.isdir(p) or is_media(p)]
        if paths:
            self.add_paths(paths)

    # ---- queue actions -----------------------------------------------------
    def selected_jobs(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        return [self.jobs[r] for r in sorted(rows) if r < len(self.jobs)]

    def cancel_sel(self):
        for j in self.selected_jobs():
            jobs.cancel(j.id)
        self.refresh()

    def retry_sel(self):
        n = 0
        for j in self.selected_jobs():
            if j.status in ("failed", "cancelled"):
                jobs.retry(j.id); n += 1
        if n and not jobs.is_paused():
            jobs.start_background()
        self.refresh()

    def remove_sel(self):
        sel = self.selected_jobs()
        if any(j.status == "running" for j in sel):
            QMessageBox.information(self, "PolySub", tr("正在处理的任务要先取消才能移除。"))
        jobs.remove([j.id for j in sel])
        self.refresh()

    def clear_done(self):
        jobs.clear()
        self.refresh()

    def toggle_pause(self):
        pending = any(j.status in ("pending", "running") for j in self.jobs)
        if jobs.is_paused() or (pending and not jobs.worker_running()):
            jobs.set_paused(False)
            jobs.start_background()
        else:
            jobs.set_paused(True)
        self.refresh()

    def _menu(self, pos):
        sel = self.selected_jobs()
        if not sel:
            return
        j = sel[0]
        m = QMenu(self)
        m.addAction(tr("在 Finder 中显示视频"), lambda: reveal(j.video))
        for lang, path in (j.outputs or {}).items():
            if os.path.exists(path):
                m.addAction(tr("预览和编辑字幕（{lang}）").format(lang=langs.label(lang)), lambda l=lang: self.edit(j, l))
                m.addAction(tr("用默认程序打开字幕（{lang}）").format(lang=langs.label(lang)), lambda p=path: open_file(p))
                m.addAction(tr("在 Finder 中显示字幕（{lang}）").format(lang=langs.label(lang)), lambda p=path: reveal(p))
        m.addSeparator()
        m.addAction(tr("取消"), self.cancel_sel)
        m.addAction(tr("重试"), self.retry_sel)
        m.addAction(tr("移除"), self.remove_sel)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _open_row(self, row):
        if row >= len(self.jobs):
            return
        j = self.jobs[row]
        outs = [l for l, p in (j.outputs or {}).items() if os.path.exists(p)]
        if outs:
            self.edit(j, outs[0])
        else:
            reveal(j.video)

    def edit(self, job, lang):
        from .editor import SubtitleEditor
        SubtitleEditor(self.win, job, lang).show()

    # ---- refresh -----------------------------------------------------------
    def refresh(self):
        try:
            self.jobs = jobs.list_jobs()
        except Exception as e:  # noqa: BLE001 - lock contention, try next tick
            self.state.setText(str(e)[:80])
            return
        self.hint.setVisible(not self.jobs)
        if self.table.rowCount() != len(self.jobs):
            self.table.setRowCount(len(self.jobs))
        now = time.time()
        for r, j in enumerate(self.jobs):
            name = os.path.basename(j.video)
            cells = [name, "、".join(langs.label(c) for c in j.targets), STATUS.get(j.status, j.status), None,
                     _fmt_secs((j.finished or now) - j.started) if j.started else "",
                     j.error or (j.stage if j.status == "running" else j.notes)]
            for c, text in enumerate(cells):
                if c == 3:
                    bar = self.table.cellWidget(r, 3)
                    if not isinstance(bar, QProgressBar):
                        bar = QProgressBar(); bar.setRange(0, 100); bar.setTextVisible(True)
                        self.table.setCellWidget(r, 3, bar)
                    val = 100 if j.status in ("done", "skipped") else int(j.percent)
                    bar.setValue(val)
                    continue
                item = self.table.item(r, c)
                if item is None:
                    item = QTableWidgetItem(); self.table.setItem(r, c, item)
                if item.text() != text:
                    item.setText(text)
                if c == 0:
                    item.setToolTip(j.video)
                if c == 5:
                    item.setToolTip(j.error or j.notes)
                if c == 2:
                    item.setForeground(Qt.red if j.status == "failed" else Qt.darkGreen if j.status == "done"
                                       else self.palette().text().color())
        paused, running = jobs.is_paused(), jobs.worker_running()
        pending = sum(1 for j in self.jobs if j.status in ("pending", "running"))
        if paused:
            s = tr("已暂停（当前任务做完后停止）") if running else tr("已暂停")
        elif running:
            s = tr("后台处理中，还剩 {n} 个").format(n=pending)
        else:
            s = tr("空闲") if not pending else tr("有 {n} 个等待中，点「继续」开始").format(n=pending)
        self.state.setText(s)
        self.pause_btn.setText(tr("继续") if paused or (pending and not running) else tr("暂停"))
