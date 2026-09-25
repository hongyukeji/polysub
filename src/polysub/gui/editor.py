"""Subtitle preview / editor: source and translation side by side, manual edits,
re-translating selected lines, saving back to the subtitle file."""
import os
import threading

import pysubs2
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import langs, pipeline, subtitle
from ..api import ChatClient, Usage
from ..asr import Cue
from ..translate import Translator
from .widgets import open_file, reveal, run_async, tr

EDITED = QColor(255, 190, 0, 70)  # translucent amber: readable in light and dark mode


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000 // 100}"


class SubtitleEditor(QDialog):
    def __init__(self, window, job, lang: str):
        super().__init__(window)
        self.win, self.job, self.lang = window, job, lang
        self.path = job.outputs.get(lang, "")
        self.setWindowTitle(f"{os.path.basename(self.path)} — PolySub")
        self.resize(1100, 700)
        cache = job.cache or pipeline._cache_dir(job.video, window.cfg)
        self.data = pipeline.load_edit_data(cache, lang)
        self.cache = cache
        if not self.data:  # older run: only the subtitle file is known
            subs = pysubs2.load(self.path)
            self.data = {"video": job.video, "output": self.path, "lang": lang, "source_lang": "",
                         "lines": [{"start": e.start / 1000, "end": e.end / 1000, "src": "", "tr": e.plaintext}
                                   for e in subs]}
        self.lines = self.data["lines"]
        self.dirty = False

        self.search = QLineEdit(placeholderText=tr("搜索原文或译文…"))
        self.search.textChanged.connect(self.filter)
        info = QLabel(tr("{src} → {tgt}，共 {n} 行。双击译文可以修改；改过的行会标黄。").format(
            src=langs.label(self.data.get("source_lang") or "?"), tgt=langs.label(lang), n=len(self.lines)))
        info.setStyleSheet("color: palette(placeholder-text);")

        self.table = QTableWidget(len(self.lines), 4)
        self.table.setHorizontalHeaderLabels([tr("开始"), tr("结束"), tr("原文"), tr("译文")])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setWordWrap(True)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self._fill()
        self.table.itemChanged.connect(self._edited)

        btns = QHBoxLayout()
        self.retr = QPushButton(tr("重新翻译所选行")); self.retr.clicked.connect(self.retranslate)
        self.retr.setEnabled(any(l["src"] for l in self.lines))
        btns.addWidget(self.retr)
        b = QPushButton(tr("打开视频")); b.clicked.connect(lambda: open_file(self.job.video)); btns.addWidget(b)
        b = QPushButton(tr("在 Finder 中显示")); b.clicked.connect(lambda: reveal(self.path)); btns.addWidget(b)
        btns.addStretch(1)
        self.status = QLabel(); btns.addWidget(self.status)
        b = QPushButton(tr("关闭")); b.clicked.connect(self.close); btns.addWidget(b)
        self.save_btn = QPushButton(tr("保存")); self.save_btn.setDefault(True); self.save_btn.clicked.connect(self.save)
        btns.addWidget(self.save_btn)

        lay = QVBoxLayout(self)
        top = QHBoxLayout(); top.addWidget(info, 1); top.addWidget(self.search)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addLayout(btns)
        self.table.setFocus()

    def _fill(self):
        self.table.blockSignals(True)
        for r, l in enumerate(self.lines):
            for c, text in enumerate((_ts(l["start"]), _ts(l["end"]), l["src"], l["tr"])):
                it = QTableWidgetItem(text)
                if c != 3:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, c, it)
        self.table.resizeRowsToContents()
        self.table.blockSignals(False)

    def _edited(self, item):
        if item.column() != 3:
            return
        self.lines[item.row()]["tr"] = item.text()
        self.table.blockSignals(True)
        item.setBackground(EDITED)
        self.table.blockSignals(False)
        self.dirty = True
        self.status.setText(tr("有未保存的修改"))

    def filter(self, text):
        t = text.strip().lower()
        for r, l in enumerate(self.lines):
            self.table.setRowHidden(r, bool(t) and t not in l["src"].lower() and t not in l["tr"].lower())

    def retranslate(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        rows = [r for r in rows if self.lines[r]["src"]]
        if not rows:
            return
        cfg = self.win.cfg
        t = cfg.translate
        brief = ""
        for f in os.listdir(self.cache) if os.path.isdir(self.cache) else []:
            if f.startswith("brief-"):
                brief = (pipeline._load_json(os.path.join(self.cache, f)) or {}).get("brief", "")
        client = ChatClient(cfg.endpoint(t.endpoint), t.model, t.think, Usage(), threading.Event(),
                            think_budget=t.think_budget)
        tr_ = Translator(client, self.data.get("source_lang", ""), self.lang, brief, t.batch_lines(), t.context_lines)
        srcs = [l["src"] for l in self.lines]
        self.retr.setEnabled(False)
        self.status.setText(tr("正在重新翻译 {n} 行…").format(n=len(rows)))

        def work():
            out = {}
            for r in rows:  # each line with its neighbours as context
                ctx = [(srcs[j], self.lines[j]["tr"]) for j in range(max(0, r - 4), r)]
                out[r] = tr_._batch([srcs[r]], ctx)[0]
            return out

        def done(out):
            self.retr.setEnabled(True)
            for r, text in out.items():
                self.table.item(r, 3).setText(text)  # triggers _edited
            self.table.resizeRowsToContents()
            self.status.setText(tr("已重新翻译 {n} 行，记得保存").format(n=len(out)))

        def fail(msg):
            self.retr.setEnabled(True)
            self.status.setText(tr("重新翻译失败：") + msg[:120])

        run_async(work, done, fail)

    def save(self):
        fmt = os.path.splitext(self.path)[1].lstrip(".").lower() or "srt"
        cues = [Cue(l["start"], l["end"], l["src"]) for l in self.lines]
        texts = [l["tr"] for l in self.lines]
        subtitle.write(subtitle.build(cues, texts, self.lang, self.win.cfg.general.bilingual), self.path, fmt)
        if self.data.get("lines") and os.path.isdir(self.cache):
            pipeline._save_json(pipeline.edit_data_path(self.cache, self.lang), self.data)
        self.dirty = False
        self.status.setText(tr("已保存"))
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            self.table.item(r, 3).setData(Qt.BackgroundRole, None)
        self.table.blockSignals(False)

    def closeEvent(self, e):
        if self.dirty:
            b = QMessageBox.question(self, "PolySub", tr("有未保存的修改，保存吗？"),
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if b == QMessageBox.Cancel:
                e.ignore(); return
            if b == QMessageBox.Save:
                self.save()
        super().closeEvent(e)
