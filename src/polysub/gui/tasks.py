"""Task page: drop videos, pick subtitle languages, watch the background queue."""
import os
import time

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPalette, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QMenu, QMessageBox, QPushButton, QStackedWidget, QStyledItemDelegate,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import jobs, langs
from ..config import quality_of as quality_of_cfg
from ..config import save, with_quality
from ..media import is_media
from . import style
from .widgets import MINE_LABEL, QUALITY, lang_label, open_file, reveal, tr

STATUS = {"pending": tr("等待"), "running": tr("处理中"), "done": tr("完成"), "failed": tr("失败"),
          "cancelled": tr("已取消"), "skipped": tr("已跳过")}
COLS = [tr("视频"), tr("字幕语言"), tr("状态"), tr("进度"), tr("用时"), tr("说明")]

STATUS_COLOR = {"done": style.GREEN, "failed": style.RED, "running": QColor("#0A84FF"),
                "pending": style.GREY, "cancelled": style.ORANGE, "skipped": style.GREY}
PERCENT = Qt.UserRole + 1


class LangPicker(QPushButton):
    """Pull-down button with a checkable language menu (the platform draws the arrow)."""

    def __init__(self, selected, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(150)
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
        text = "、".join(langs.label(c) for c in sel)
        self.setText(text if len(text) <= 18 else tr("{n} 种语言").format(n=len(sel)))
        self.setToolTip("、".join(langs.label(c) for c in sel))


class ProgressDelegate(QStyledItemDelegate):
    """Thin rounded progress bar with the percentage, drawn in the cell."""

    def paint(self, p, opt, index):
        super().paint(p, opt, index)
        val = index.data(PERCENT)
        if val is None:
            return
        r = opt.rect.adjusted(8, 0, -8, 0)
        text_w = 38
        track = QRectF(r.left(), r.center().y() - 2.5, max(10, r.width() - text_w), 5)
        pal = opt.palette
        selected = bool(opt.state & opt.state.State_Selected)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        base = QColor(pal.color(QPalette.HighlightedText if selected else QPalette.Text)); base.setAlpha(60 if selected else 35)
        p.setBrush(base); p.drawRoundedRect(track, 2.5, 2.5)
        if val > 0:
            fill = QRectF(track); fill.setWidth(max(5.0, track.width() * min(val, 100) / 100))
            p.setBrush(pal.color(QPalette.HighlightedText) if selected else
                       (index.data(Qt.UserRole + 2) or STATUS_COLOR["running"]))
            p.drawRoundedRect(fill, 2.5, 2.5)
        p.setPen(pal.color(QPalette.HighlightedText) if selected else pal.color(QPalette.PlaceholderText))
        p.drawText(QRectF(track.right(), r.top(), text_w, r.height()), Qt.AlignRight | Qt.AlignVCenter, f"{val}%")
        p.restore()


def _fmt_secs(s: float) -> str:
    s = int(s)
    return f"{s // 60}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


class DropZone(QFrame):
    """Empty state: picture, title, hint and the two add buttons."""

    def __init__(self, page):
        super().__init__(objectName="dropzone")
        v = QVBoxLayout(self); v.setAlignment(Qt.AlignCenter); v.setSpacing(10)
        self.pic = QLabel(alignment=Qt.AlignCenter)
        v.addWidget(self.pic)
        v.addWidget(QLabel(tr("把视频或文件夹拖到这里"), objectName="emptyTitle", alignment=Qt.AlignCenter))
        hint = style.secondary(tr("支持常见视频和音频格式；文件夹会包含子文件夹里的视频"), small=False)
        hint.setAlignment(Qt.AlignCenter); v.addWidget(hint)
        row = QHBoxLayout(); row.addStretch(1)
        for act in (page.add_act, page.folder_act):
            b = QPushButton(act.text()); b.clicked.connect(act.trigger); row.addWidget(b)
        row.addStretch(1)
        v.addSpacing(6); v.addLayout(row)
        self.restyle()

    def restyle(self):
        c = QColor(self.palette().color(QPalette.PlaceholderText))
        self.pic.setPixmap(style.icon("film", c, size=64, width=2.2).pixmap(64, 64))

    def set_hover(self, on: bool):
        self.setProperty("hover", on)
        self.style().unpolish(self); self.style().polish(self)


class TasksPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self.setAcceptDrops(True)
        cfg = window.cfg
        self._make_actions()

        top = QHBoxLayout(); top.setSpacing(8)
        top.addWidget(style.page_title(tr("任务")))
        top.addStretch(1)
        top.addWidget(style.secondary(tr("字幕语言"), small=False))
        self.langs = LangPicker(cfg.general.target_langs)
        top.addWidget(self.langs)
        top.addSpacing(12)
        top.addWidget(style.secondary(tr("翻译质量"), small=False))
        self.quality = QComboBox()
        for k, (label, _, _) in QUALITY.items():
            self.quality.addItem(label, k)
        self.quality.addItem(MINE_LABEL, "mine")
        self.quality.addItem(tr("自定义（见设置）"), "custom")
        self.quality.setToolTip(tr("快速：不思考，速度最快\n"
                                   "标准：少量思考，更准确，约慢一倍\n"
                                   "精细：不限思考，最慢\n"
                                   "我的模型：用「设置 › 高级 › 我的模型」里配好的组合"))
        self.quality.currentIndexChanged.connect(self._quality_changed)
        top.addWidget(self.quality)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for i, w in ((1, 120), (2, 90), (3, 170), (4, 70)):
            h.setSectionResizeMode(i, QHeaderView.Interactive)
            self.table.setColumnWidth(i, w)
        h.setSectionResizeMode(5, QHeaderView.Stretch)
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        h.setHighlightSections(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.setFocusPolicy(Qt.StrongFocus)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setItemDelegateForColumn(3, ProgressDelegate(self.table))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.cellDoubleClicked.connect(lambda r, c: self._open_row(r))
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        self.select_none_act.triggered.connect(self.table.clearSelection)
        for keys, fn in ((QKeySequence("Ctrl+Backspace"), self.remove_sel), (QKeySequence.Delete, self.remove_sel),
                         (QKeySequence("Ctrl+R"), self.retry_sel), (QKeySequence("Ctrl+."), self.cancel_sel)):
            QShortcut(keys, self.table, fn, context=Qt.WidgetShortcut)
        card = QFrame(objectName="card"); cl = QVBoxLayout(card); cl.setContentsMargins(1, 1, 1, 1)
        cl.addWidget(self.table)

        self.hint = DropZone(self)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.hint)
        self.stack.addWidget(card)

        bottom = QHBoxLayout(); bottom.setSpacing(6)
        self.state = style.secondary(small=False, wrap=False)
        bottom.addWidget(self.state, 1)

        table_keys = {self.cancel_act: "Ctrl+.", self.retry_act: "Ctrl+R", self.remove_act: "Ctrl+Backspace"}

        def button(act):
            b = style.mini(QPushButton(act.text())); b.clicked.connect(act.trigger)
            keys = QKeySequence(table_keys[act]) if act in table_keys else act.shortcut()
            keys = keys.toString(QKeySequence.NativeText)
            b.setToolTip(f"{act.text()}  {keys}" if keys else act.text())
            bottom.addWidget(b)
            return b
        self.select_btns = [button(a) for a in (self.select_all_act, self.select_none_act)]
        bottom.addSpacing(10)
        self.sel_btns = [button(a) for a in (self.cancel_act, self.retry_act, self.remove_act)]  # need a selection
        bottom.addSpacing(10)
        self.clear_btn = button(self.clear_act)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 12); lay.setSpacing(12)
        lay.addLayout(top)
        lay.addWidget(self.stack, 1)
        lay.addLayout(bottom)

        self.flash_until = 0.0
        self.rows = {}      # job id -> row
        self.jobs = []
        self.sync_quality()
        self.restyle()
        self.timer = QTimer(self, interval=1000, timeout=self.refresh)
        self.timer.start()
        self.refresh()

    def _make_actions(self):
        A = lambda text, fn, keys=None: QAction(text, self, triggered=fn, shortcut=keys) if keys else \
            QAction(text, self, triggered=fn)  # noqa: E731
        self.add_act = A(tr("添加视频…"), self.add_files, QKeySequence.Open)
        self.folder_act = A(tr("添加文件夹…"), self.add_folder, QKeySequence("Ctrl+Shift+O"))
        self.pause_act = A(tr("暂停"), self.toggle_pause)
        # no shortcuts here: these also sit in the menu bar, whose key equivalents fire in every window on
        # macOS (⌘⌫ in a text field must not remove jobs); the table has its own shortcuts, see below
        self.cancel_act = A(tr("取消"), self.cancel_sel)
        self.retry_act = A(tr("重试"), self.retry_sel)
        self.remove_act = A(tr("移除"), self.remove_sel)
        self.clear_act = A(tr("清除已结束"), self.clear_done)
        self.add_act.setToolTip(tr("添加视频（也可以直接拖进窗口）"))
        self.folder_act.setToolTip(tr("添加文件夹（包含子文件夹里的视频）"))
        # ⌘A / Esc (Ctrl+A / Esc elsewhere)
        self.select_all_act = A(tr("全选"), self.select_all, QKeySequence.SelectAll)
        self.select_none_act = QAction(tr("取消全选"), self, shortcut=QKeySequence.Cancel)
        self.addActions([self.select_all_act, self.select_none_act])

    def restyle(self):
        """Icons follow the text color (light / dark mode)."""
        c = self.palette().color(QPalette.Text)
        self.add_act.setIcon(style.icon("add", c))
        self.folder_act.setIcon(style.icon("folder", c))
        self._pause_icons = (style.icon("pause", c), style.icon("play", c))
        self.hint.restyle()
        self._dots = {k: QIcon(style.dot_pixmap(v)) for k, v in STATUS_COLOR.items()}
        if hasattr(self, "timer"):
            self.refresh()

    # ---- settings shortcuts ------------------------------------------------
    def sync_quality(self):
        k = quality_of_cfg(self.win.cfg)
        self.quality.blockSignals(True)
        self.quality.setCurrentIndex(self.quality.findData(k))
        self.quality.blockSignals(False)

    def _quality_changed(self):
        k = self.quality.currentData()
        cfg = self.win.cfg
        if k == "custom":
            return
        if k == "mine" and not cfg.mine.configured:
            QMessageBox.information(self, "PolySub", tr("还没有配置「我的模型」：到「设置」打开「显示高级设置」，"
                                                       "在「我的模型」里选好翻译服务和模型。"))
            self.sync_quality()
            if hasattr(self.win, "show_page"):
                self.win.show_page("settings")
            return
        new = with_quality(cfg, k)
        cfg.general.use_mine = new.general.use_mine
        cfg.translate.think, cfg.translate.think_budget = new.translate.think, new.translate.think_budget
        save(cfg)
        self.win.settings_changed()

    def flash(self, msg: str, seconds: float = 5):
        """Show a short message in the bottom-left status label."""
        self.flash_until = time.time() + seconds
        self.state.setText(msg)

    # ---- adding ------------------------------------------------------------
    def add_paths(self, paths):
        targets = self.langs.selected()
        cfg = self.win.cfg
        if cfg.general.target_langs != targets:  # remember the choice
            cfg.general.target_langs = targets
            save(cfg)
        added = jobs.add(paths, targets)
        if not added:
            self.flash(tr("没有新的视频（可能已在队列中，或不是视频文件）"))
        else:
            self.flash(tr("已加入 {n} 个视频").format(n=len(added)))
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
            self.hint.set_hover(True)

    def dragLeaveEvent(self, e):
        self.hint.set_hover(False)

    def dropEvent(self, e):
        self.hint.set_hover(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if os.path.isdir(p) or is_media(p)]
        if paths:
            self.add_paths(paths)

    # ---- selection ---------------------------------------------------------
    def selected_jobs(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        return [self.jobs[r] for r in sorted(rows) if r < len(self.jobs)]

    def select_all(self):
        self.table.selectAll()
        self.table.setFocus()  # show the active highlight

    def _select_ids(self, ids):
        """Select exactly the rows of these job ids (rows move when jobs are removed)."""
        sel, model, last = QItemSelection(), self.table.model(), self.table.columnCount() - 1
        for r, j in enumerate(self.jobs):
            if j.id in ids:
                sel.select(model.index(r, 0), model.index(r, last))
        self.table.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)

    def _sync_buttons(self):
        has_sel = self.table.selectionModel().hasSelection()
        for act in (self.cancel_act, self.retry_act, self.remove_act, self.select_none_act):
            act.setEnabled(has_sel)
        self.select_all_act.setEnabled(bool(self.jobs))
        self.clear_act.setEnabled(any(j.status not in ("pending", "running") for j in self.jobs))
        for b, act in zip(self.select_btns + self.sel_btns + [self.clear_btn],
                          (self.select_all_act, self.select_none_act, self.cancel_act, self.retry_act, self.remove_act,
                           self.clear_act)):
            b.setEnabled(act.isEnabled())
            b.setVisible(bool(self.jobs))  # nothing to act on while the queue is empty

    # ---- queue actions -----------------------------------------------------
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

    def requeue(self, quality: str):
        n = 0
        for j in self.selected_jobs():
            if j.status not in ("running", "pending"):
                jobs.requeue(j.id, quality); n += 1
        if n and not jobs.is_paused():
            jobs.start_background()
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
        m = QMenu(self)
        if sel:
            j = sel[0]
            m.addAction(tr("在 Finder 中显示视频"), lambda: reveal(j.video))
            for lang, path in (j.outputs or {}).items():
                if os.path.exists(path):
                    m.addAction(tr("预览和编辑字幕（{lang}）").format(lang=langs.label(lang)), lambda l=lang: self.edit(j, l))
                    m.addAction(tr("用默认程序打开字幕（{lang}）").format(lang=langs.label(lang)), lambda p=path: open_file(p))
                    m.addAction(tr("在 Finder 中显示字幕（{lang}）").format(lang=langs.label(lang)), lambda p=path: reveal(p))
            again = m.addMenu(tr("用其他质量重新翻译"))
            for k, (label, _, _) in list(QUALITY.items()) + [("mine", (MINE_LABEL, "", 0))]:
                a = again.addAction(label, lambda k=k: self.requeue(k))
                a.setEnabled(k != "mine" or self.win.cfg.mine.configured)
            again.setEnabled(any(x.status not in ("running", "pending") for x in sel))
            m.addSeparator()
            m.addAction(self.cancel_act)
            m.addAction(self.retry_act)
            m.addAction(self.remove_act)
            m.addSeparator()
        m.addAction(self.select_all_act)
        m.addAction(self.select_none_act)
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
        old_ids, keep = [j.id for j in self.jobs], {j.id for j in self.selected_jobs()}
        try:
            self.jobs = jobs.list_jobs()
        except Exception as e:  # noqa: BLE001 - lock contention, try next tick
            self.state.setText(str(e)[:80])
            return
        self.stack.setCurrentIndex(1 if self.jobs else 0)
        if self.table.rowCount() != len(self.jobs):
            self.table.setRowCount(len(self.jobs))
        now = time.time()
        for r, j in enumerate(self.jobs):
            name = os.path.basename(j.video)
            level = MINE_LABEL if j.quality == "mine" else QUALITY.get(j.quality, ("",))[0].replace(tr("（推荐）"), "")
            cells = [name, "、".join(langs.label(c) for c in j.targets) + (f" · {level}" if level else ""), STATUS.get(j.status, j.status), None,
                     _fmt_secs((j.finished or now) - j.started) if j.started else "",
                     j.error or (j.stage if j.status == "running" else j.notes)]
            for c, text in enumerate(cells):
                item = self.table.item(r, c)
                if item is None:
                    item = QTableWidgetItem(); self.table.setItem(r, c, item)
                if c == 3:
                    val = 100 if j.status in ("done", "skipped") else int(j.percent)
                    if item.data(PERCENT) != val:
                        item.setData(PERCENT, val)
                    color = STATUS_COLOR["done"] if j.status == "done" else \
                        STATUS_COLOR["failed"] if j.status == "failed" else None
                    item.setData(Qt.UserRole + 2, color)
                    continue
                if item.text() != text:
                    item.setText(text)
                if c == 0:
                    item.setToolTip(j.video)
                if c == 5:
                    item.setToolTip(j.error or j.notes)
                if c == 2 and item.data(Qt.UserRole) != j.status:
                    item.setData(Qt.UserRole, j.status)
                    item.setIcon(self._dots.get(j.status, self._dots["pending"]))
        if [j.id for j in self.jobs] != old_ids:  # rows moved: selection follows the jobs, not row numbers
            self._select_ids(keep)
        self._sync_buttons()
        paused, running = jobs.is_paused(), jobs.worker_running()
        pending = sum(1 for j in self.jobs if j.status in ("pending", "running"))
        if paused:
            s = tr("已暂停（当前任务做完后停止）") if running else tr("已暂停")
        elif running:
            s = tr("后台处理中，还剩 {n} 个").format(n=pending)
        else:
            s = tr("空闲") if not pending else tr("有 {n} 个等待中，点「继续」开始").format(n=pending)
        if time.time() >= self.flash_until:
            self.state.setText(s)
        resume = paused or (pending and not running)
        self.pause_act.setEnabled(bool(pending) or paused)
        self.pause_act.setText(tr("继续") if resume else tr("暂停"))
        self.pause_act.setIcon(self._pause_icons[1 if resume else 0])
        self.pause_act.setToolTip(tr("继续处理队列") if resume else tr("当前任务做完后暂停"))
