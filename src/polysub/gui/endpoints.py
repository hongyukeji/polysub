"""Endpoints page: every OpenAI-compatible service (local oMLX, Ollama, LM Studio,
DeepSeek, Alibaba Bailian, ...), with presets and a connection test."""
import copy

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMenu,
                               QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..api import AsrClient, ChatClient
from ..asr import _wav
from ..config import PRESETS, Endpoint, save
from .settings import THINKING_STYLES, _form, _narrow
from .widgets import run_async, tr


class EndpointsPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self.list = QListWidget()
        self.list.setMinimumWidth(210); self.list.setMaximumWidth(260)
        self.list.currentRowChanged.connect(self.show_ep)

        self.name = QLineEdit()
        self.preset = QComboBox()
        for k, p in PRESETS.items():
            self.preset.addItem(p["label"], k)
        self.preset.activated.connect(self.preset_chosen)
        self.url = QLineEdit(); self.url.setPlaceholderText("https://api.example.com/v1")
        self.key = QLineEdit(); self.key.setEchoMode(QLineEdit.Password); self.key.setPlaceholderText("sk-...")
        show = QPushButton(tr("显示")); show.setCheckable(True)
        show.toggled.connect(lambda on: self.key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        keyrow = QHBoxLayout(); keyrow.setContentsMargins(0, 0, 0, 0); keyrow.addWidget(self.key, 1); keyrow.addWidget(show)
        keyw = QWidget(); keyw.setLayout(keyrow)
        self.thinking = QComboBox()
        for k, v in THINKING_STYLES.items():
            self.thinking.addItem(v, k)
        self.conc = QSpinBox(minimum=1, maximum=32)
        self.conc.setToolTip(tr("同时发几个请求。本机模型设 1；云端可以设 4～8"))
        self.timeout = QSpinBox(minimum=10, maximum=7200, suffix=tr(" 秒"))
        self.test_model = QLineEdit(); self.test_model.setPlaceholderText(tr("测试用的翻译模型，留空则只获取模型列表"))
        self.result = QLabel(); self.result.setWordWrap(True); self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)

        form = _form()
        form.addRow(tr("名称"), self.name)
        form.addRow(tr("类型"), self.preset)
        form.addRow("Base URL", self.url)
        form.addRow("API Key", keyw)
        form.addRow(tr("思考开关格式"), self.thinking)
        form.addRow(tr("并发"), _narrow(self.conc))
        form.addRow(tr("超时"), _narrow(self.timeout))
        form.addRow(tr("测试模型"), self.test_model)

        btns = QHBoxLayout()
        add = QPushButton(tr("新增…")); m = QMenu(self)
        for k, p in PRESETS.items():
            m.addAction(p["label"], lambda k=k: self.add_ep(k))
        add.setMenu(m); btns.addWidget(add)
        b = QPushButton(tr("删除")); b.clicked.connect(self.delete_ep); btns.addWidget(b)
        btns.addStretch(1)
        self.test_btn = QPushButton(tr("测试连接")); self.test_btn.clicked.connect(self.test_ep); btns.addWidget(self.test_btn)
        b = QPushButton(tr("保存")); b.setDefault(True); b.clicked.connect(self.apply); btns.addWidget(b)

        right = QVBoxLayout()
        intro = QLabel(tr("所有模型服务都用 OpenAI 兼容接口：本机 oMLX、Ollama、LM Studio，或 DeepSeek、阿里云百炼等云端。"
                          "语音识别和翻译在「设置」页各选一个。API Key 以明文保存在配置文件里。"))
        intro.setWordWrap(True); intro.setStyleSheet("color: palette(placeholder-text);")
        right.addWidget(intro)
        right.addLayout(form)
        right.addLayout(btns)
        right.addWidget(self.result)
        right.addStretch(1)
        lay = QHBoxLayout(self)
        lay.addWidget(self.list, 1)
        rw = QWidget(); rw.setLayout(right); rw.setMaximumWidth(760); lay.addWidget(rw, 3)
        self.load()

    def load(self):
        self.eps = copy.deepcopy(self.win.cfg.endpoints)
        self.orig_names = [e.name for e in self.eps]
        self.list.clear()
        self.list.addItems([e.name for e in self.eps])
        self.cur = -1
        if self.eps:
            self.list.setCurrentRow(0)

    def _store(self):
        if 0 <= self.cur < len(self.eps):
            e = self.eps[self.cur]
            e.name = self.name.text().strip() or e.name
            e.preset, e.base_url, e.api_key = self.preset.currentData(), self.url.text().strip(), self.key.text().strip()
            e.thinking, e.concurrency, e.timeout = self.thinking.currentData(), self.conc.value(), self.timeout.value()
            self.list.item(self.cur).setText(e.name)

    def show_ep(self, row):
        self._store()
        self.cur = row
        if row < 0:
            return
        e = self.eps[row]
        self.name.setText(e.name)
        self.preset.setCurrentIndex(max(0, self.preset.findData(e.preset)))
        self.url.setText(e.base_url); self.key.setText(e.api_key)
        self.thinking.setCurrentIndex(max(0, self.thinking.findData(e.thinking)))
        self.conc.setValue(e.concurrency); self.timeout.setValue(e.timeout)
        t = self.win.cfg.translate
        self.test_model.setText(t.model if e.name == t.endpoint else "")
        self.result.clear()

    def preset_chosen(self):
        p = PRESETS[self.preset.currentData()]
        if p["base_url"]:
            self.url.setText(p["base_url"])
        self.thinking.setCurrentIndex(self.thinking.findData(p["thinking"]))
        self.conc.setValue(p["concurrency"])

    def add_ep(self, preset):
        self._store()
        p = PRESETS[preset]
        base, n = p["label"], 2
        name = base
        while any(e.name == name for e in self.eps):
            name = f"{base} {n}"; n += 1
        self.eps.append(Endpoint(name=name, preset=preset, base_url=p["base_url"], thinking=p["thinking"],
                                 concurrency=p["concurrency"]))
        self.orig_names.append(None)
        self.list.addItem(name)
        self.list.setCurrentRow(len(self.eps) - 1)

    def delete_ep(self):
        row = self.list.currentRow()
        if row < 0:
            return
        name = self.eps[row].name
        cfg = self.win.cfg
        if name in (cfg.asr.endpoint, cfg.translate.endpoint):
            QMessageBox.information(self, "PolySub", tr("「{n}」正在被语音识别或翻译使用，先在「设置」里换掉再删除。").format(n=name))
            return
        self.cur = -1
        del self.eps[row]; del self.orig_names[row]
        self.list.takeItem(row)

    def apply(self):
        self._store()
        names = [e.name for e in self.eps]
        if len(set(names)) != len(names):
            QMessageBox.warning(self, "PolySub", tr("接口名称不能重复。"))
            return
        cfg = self.win.cfg
        renames = {o: e.name for o, e in zip(self.orig_names, self.eps) if o and o != e.name}
        for sec, attr in ((cfg.asr, "endpoint"), (cfg.translate, "endpoint"), (cfg.translate, "fallback_endpoint")):
            if getattr(sec, attr) in renames:
                setattr(sec, attr, renames[getattr(sec, attr)])
        cfg.endpoints = copy.deepcopy(self.eps)
        save(cfg)
        self.orig_names = names
        self.win.settings_changed()
        self.win.flash(tr("接口配置已保存"))

    def test_ep(self):
        self._store()
        if self.cur < 0:
            return
        ep = copy.deepcopy(self.eps[self.cur])
        model = self.test_model.text().strip()
        asr_model = self.win.cfg.asr.model if ep.name == self.win.cfg.asr.endpoint else ""
        self.test_btn.setEnabled(False)
        self.result.setText(tr("测试中…"))

        def work():
            lines = []
            c = ChatClient(ep, model or "x", "off")
            try:
                ms = c.list_models()
                lines.append("✓ " + tr("连接正常，{n} 个模型：").format(n=len(ms)) + "、".join(ms[:15]) + (" …" if len(ms) > 15 else ""))
            except Exception as e:  # noqa: BLE001
                lines.append("✗ " + tr("获取模型列表失败：") + str(e)[:300])
            if model:
                try:
                    out = c.complete([{"role": "user", "content": "把「今日もよろしく」翻译成简体中文，只输出译文"}], max_tokens=64)
                    lines.append("✓ " + tr("翻译测试（{m}）：").format(m=model) + out)
                except Exception as e:  # noqa: BLE001
                    lines.append("✗ " + tr("翻译测试失败：") + str(e)[:300])
            if asr_model:
                try:
                    tone = 0.1 * np.sin(np.linspace(0, 440 * 2 * np.pi, 16000)).astype(np.float32)
                    AsrClient(ep, asr_model).transcribe(_wav(tone))
                    lines.append("✓ " + tr("语音识别接口可用（{m}）").format(m=asr_model))
                except Exception as e:  # noqa: BLE001
                    lines.append("✗ " + tr("语音识别测试失败：") + str(e)[:300])
            return "\n".join(lines)

        def done(text):
            self.test_btn.setEnabled(True)
            self.result.setText(text)

        def fail(msg):
            self.test_btn.setEnabled(True)
            self.result.setText("✗ " + msg)

        run_async(work, done, fail)
