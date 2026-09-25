"""Settings page (general / speech recognition / translation) and endpoints page."""
import copy

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QMenu, QMessageBox, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import langs
from ..api import AsrClient, ChatClient
from ..asr import _wav
from ..config import PRESETS, Endpoint, save
from .common import QUALITY, lang_label, quality_of, run_async, tr

SOURCE_LANGS = ["auto", "ja", "en", "zh", "ko", "yue", "fr", "de", "es", "ru", "pt", "it", "th", "vi", "id"]
THINKING_STYLES = {
    "chat_template_kwargs": tr("oMLX / vLLM（chat_template_kwargs）"),
    "enable_thinking": tr("阿里百炼（enable_thinking）"),
    "deepseek": tr("DeepSeek（thinking）"),
    "reasoning_effort": tr("OpenAI（reasoning_effort）"),
    "none": tr("不发送（模型不支持思考开关）"),
}


def _form(box=None):
    """Form whose fields stretch to the available width, labels right-aligned, left-anchored."""
    f = QFormLayout(box) if box is not None else QFormLayout()
    f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    f.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return f


def _narrow(w, width=140):
    w.setMaximumWidth(width)
    return w


def _combo(items, current):
    c = QComboBox()
    for data, label in items:
        c.addItem(label, data)
    i = c.findData(current)
    c.setCurrentIndex(i if i >= 0 else 0)
    return c


class ModelBox(QWidget):
    """Editable model combo + button that fetches /v1/models from the chosen endpoint."""

    def __init__(self, get_endpoint, value):
        super().__init__()
        self.get_endpoint = get_endpoint
        self.combo = QComboBox(editable=True)
        self.combo.setMinimumWidth(280)
        self.combo.setEditText(value)
        self.btn = QPushButton(tr("获取列表"))
        self.btn.clicked.connect(self.fetch)
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.combo, 1); lay.addWidget(self.btn)

    def value(self):
        return self.combo.currentText().strip()

    def fetch(self):
        ep = self.get_endpoint()
        if not ep:
            return
        self.btn.setEnabled(False)
        cur = self.value()

        def done(models):
            self.btn.setEnabled(True)
            self.combo.clear()
            self.combo.addItems(models)
            self.combo.setEditText(cur)

        def fail(msg):
            self.btn.setEnabled(True)
            QMessageBox.warning(self, "PolySub", tr("获取模型列表失败：") + msg)

        run_async(lambda: ChatClient(ep, cur).list_models(), done, fail)


class SettingsPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        outer = QHBoxLayout(self)
        inner = QWidget(); inner.setMaximumWidth(760)
        self.lay = QVBoxLayout(inner)
        outer.addWidget(inner, 1); outer.addStretch(0)
        self.build()

    def build(self):
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        cfg = self.win.cfg
        g, a, t = cfg.general, cfg.asr, cfg.translate
        names = [(e.name, e.name) for e in cfg.endpoints]

        box = QGroupBox(tr("常规")); f = _form(box)
        self.src = _combo([(c, tr("自动识别") if c == "auto" else lang_label(c)) for c in SOURCE_LANGS], g.source_lang)
        f.addRow(tr("视频原语言"), self.src)
        self.fmt = _narrow(_combo([("srt", "SRT"), ("ass", "ASS"), ("vtt", "WebVTT")], g.output_format), 200)
        f.addRow(tr("字幕格式"), self.fmt)
        self.bilingual = QCheckBox(tr("双语字幕（译文下面附原文）")); self.bilingual.setChecked(g.bilingual)
        f.addRow("", self.bilingual)
        self.exists = _combo([("skip", tr("跳过")), ("overwrite", tr("覆盖")), ("rename", tr("另存为新文件"))], g.on_exists)
        f.addRow(tr("已有同名字幕时"), self.exists)
        self.ffmpeg = QLineEdit(g.ffmpeg_path); self.ffmpeg.setPlaceholderText(tr("一般不用填；个别格式读不了时才会用到"))
        f.addRow(tr("ffmpeg 路径"), self.ffmpeg)
        self.lay.addWidget(box)

        box = QGroupBox(tr("语音识别")); f = _form(box)
        self.asr_ep = _combo(names, a.endpoint)
        f.addRow(tr("接口"), self.asr_ep)
        self.asr_model = ModelBox(lambda: cfg.find_endpoint(self.asr_ep.currentData()), a.model)
        f.addRow(tr("模型"), self.asr_model)
        self.vad = QDoubleSpinBox(decimals=2, minimum=0.05, maximum=0.9, singleStep=0.05, value=a.vad_threshold)
        self.vad.setToolTip(tr("越低越不容易漏句，但更容易把喘息、背景声当成说话；实测 0.25 最合适"))
        f.addRow(tr("说话检测灵敏度"), _narrow(self.vad))
        self.maxspeech = QDoubleSpinBox(decimals=1, minimum=3, maximum=30, value=a.max_speech_s, suffix=tr(" 秒"))
        f.addRow(tr("单句最长"), _narrow(self.maxspeech))
        self.two_pass = QCheckBox(tr("两遍识别（第二遍带人名、称呼提示，修正同音错字）")); self.two_pass.setChecked(a.two_pass)
        f.addRow("", self.two_pass)
        self.lay.addWidget(box)

        box = QGroupBox(tr("翻译")); f = _form(box)
        self.tr_ep = _combo(names, t.endpoint)
        f.addRow(tr("接口"), self.tr_ep)
        self.tr_model = ModelBox(lambda: cfg.find_endpoint(self.tr_ep.currentData()), t.model)
        f.addRow(tr("模型"), self.tr_model)
        self.quality = _combo([(k, v[0]) for k, v in QUALITY.items()] + [("custom", tr("自定义"))],
                              quality_of(t.think, t.think_budget))
        f.addRow(tr("翻译质量"), self.quality)
        self.think = _combo([("off", tr("关闭")), ("low", "low"), ("medium", "medium")], t.think)
        self.budget = QSpinBox(minimum=0, maximum=32768, singleStep=256, value=t.think_budget)
        self.budget.setSpecialValueText(tr("不限"))
        self.budget.setToolTip(tr("每批最多思考多少 token；低于约 700 时模型容易答错格式，反而更慢"))
        row = QHBoxLayout(); row.addWidget(QLabel(tr("思考"))); row.addWidget(self.think)
        row.addWidget(QLabel(tr("上限"))); row.addWidget(self.budget); row.addStretch(1)
        w = QWidget(); w.setLayout(row); f.addRow(tr("自定义"), w)
        self.quality.currentIndexChanged.connect(self._quality_to_fields)
        self.think.currentIndexChanged.connect(self._fields_to_quality)
        self.budget.valueChanged.connect(self._fields_to_quality)
        self.fb_ep = _combo([("", tr("不使用"))] + names, t.fallback_endpoint)
        self.fb_ep.setToolTip(tr("云端接口因内容审核拒绝某一批时，改用这个接口翻译那一批"))
        f.addRow(tr("被拒时改用"), self.fb_ep)
        self.fb_model = ModelBox(lambda: cfg.find_endpoint(self.fb_ep.currentData()), t.fallback_model)
        self.fb_model.combo.lineEdit().setPlaceholderText(tr("留空 = 与上面的模型相同"))
        f.addRow(tr("备用模型"), self.fb_model)
        self.batch = QSpinBox(minimum=5, maximum=60, value=t.batch_size, suffix=tr(" 行"))
        f.addRow(tr("每批行数"), _narrow(self.batch))
        self.lay.addWidget(box)

        row = QHBoxLayout(); row.addStretch(1)
        b = QPushButton(tr("恢复")); b.clicked.connect(self.win.reload_config); row.addWidget(b)
        b = QPushButton(tr("保存")); b.setDefault(True); b.clicked.connect(self.apply); row.addWidget(b)
        w = QWidget(); w.setLayout(row); self.lay.addWidget(w)
        self.lay.addStretch(1)

    def _quality_to_fields(self):
        k = self.quality.currentData()
        if k in QUALITY:
            _, think, budget = QUALITY[k]
            for wdg in (self.think, self.budget):
                wdg.blockSignals(True)
            self.think.setCurrentIndex(self.think.findData(think)); self.budget.setValue(budget)
            for wdg in (self.think, self.budget):
                wdg.blockSignals(False)

    def _fields_to_quality(self):
        self.quality.blockSignals(True)
        self.quality.setCurrentIndex(self.quality.findData(quality_of(self.think.currentData(), self.budget.value())))
        self.quality.blockSignals(False)

    def apply(self):
        cfg = self.win.cfg
        g, a, t = cfg.general, cfg.asr, cfg.translate
        g.source_lang, g.output_format = self.src.currentData(), self.fmt.currentData()
        g.bilingual, g.on_exists, g.ffmpeg_path = self.bilingual.isChecked(), self.exists.currentData(), self.ffmpeg.text().strip()
        a.endpoint, a.model = self.asr_ep.currentData(), self.asr_model.value()
        a.vad_threshold, a.max_speech_s, a.two_pass = self.vad.value(), self.maxspeech.value(), self.two_pass.isChecked()
        t.endpoint, t.model = self.tr_ep.currentData(), self.tr_model.value()
        t.think, t.think_budget = self.think.currentData(), self.budget.value()
        t.fallback_endpoint, t.fallback_model = self.fb_ep.currentData(), self.fb_model.value()
        t.batch_size = self.batch.value()
        save(cfg)
        self.win.settings_changed()
        self.win.statusBar().showMessage(tr("已保存：") + cfg.path, 5000)


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
        intro.setWordWrap(True); intro.setStyleSheet("color: gray;")
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
        self.win.statusBar().showMessage(tr("接口配置已保存"), 5000)

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
