"""Settings page: general, speech recognition and translation options."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import langs
from ..api import ChatClient
from ..config import save
from .widgets import QUALITY, lang_label, quality_of, run_async, tr

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
        self.win.flash(tr("设置已保存"))
