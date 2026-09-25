"""Settings page: general, speech recognition and translation options."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import langs
from ..api import ChatClient
from ..config import save
from .style import Card, mini, page_title, secondary, section
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
        self.combo.setMinimumWidth(240)
        self.combo.setEditText(value)
        self.btn = QPushButton(tr("获取列表"))
        self.btn.setToolTip(tr("从这个服务读取可用的模型"))
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
    """Grouped settings; every change is saved right away (like macOS System Settings)."""

    def __init__(self, window):
        super().__init__()
        self.win = window
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self.apply)
        outer = QHBoxLayout(self); outer.setContentsMargins(20, 16, 20, 20)
        inner = QWidget(); inner.setMaximumWidth(720)
        self.lay = QVBoxLayout(inner); self.lay.setContentsMargins(0, 0, 0, 0); self.lay.setSpacing(18)
        outer.addWidget(inner, 1); outer.addStretch(0)
        self.build()

    def _watch(self, *widgets):
        for w in widgets:
            for sig in ("currentIndexChanged", "valueChanged", "toggled", "editingFinished"):
                if hasattr(w, sig):
                    getattr(w, sig).connect(self._changed)
                    break
            if isinstance(w, ModelBox):
                w.combo.currentTextChanged.connect(self._changed)

    def _changed(self, *_):
        if not self._building:
            self._save_timer.start()

    def build(self):
        self._building = True
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        cfg = self.win.cfg
        g, a, t = cfg.general, cfg.asr, cfg.translate
        names = [(e.name, e.name) for e in cfg.endpoints]

        head = QHBoxLayout(); head.addWidget(page_title(tr("设置"))); head.addStretch(1)
        self.saved = secondary(tr("更改会自动保存"), small=False, wrap=False); head.addWidget(self.saved)
        w = QWidget(); w.setLayout(head); self.lay.addWidget(w)

        c = Card()
        self.src = _combo([(x, tr("自动识别") if x == "auto" else lang_label(x)) for x in SOURCE_LANGS], g.source_lang)
        c.add_row(tr("视频原语言"), self.src)
        self.fmt = _combo([("srt", "SRT"), ("ass", "ASS"), ("vtt", "WebVTT")], g.output_format)
        c.add_row(tr("字幕格式"), self.fmt)
        self.bilingual = QCheckBox(); self.bilingual.setChecked(g.bilingual)
        c.add_row(tr("双语字幕"), self.bilingual, tr("译文下面附原文"))
        self.exists = _combo([("skip", tr("跳过")), ("overwrite", tr("覆盖")), ("rename", tr("另存为新文件"))], g.on_exists)
        c.add_row(tr("已有同名字幕时"), self.exists)
        self.lay.addWidget(section(tr("常规"), c))

        c = Card()
        self.tr_ep = _combo(names, t.endpoint)
        c.add_row(tr("服务"), self.tr_ep, tr("在「模型服务」里添加本机或云端服务"))
        self.tr_model = ModelBox(lambda: cfg.find_endpoint(self.tr_ep.currentData()), t.model)
        c.add_row(tr("模型"), self.tr_model, stretch=True)
        self.quality = _combo([(k, v[0]) for k, v in QUALITY.items()] + [("custom", tr("自定义"))],
                              quality_of(t.think, t.think_budget))
        c.add_row(tr("翻译质量"), self.quality, tr("快速不思考，速度最快；标准和精细更准确，但更慢"))
        self.lay.addWidget(section(tr("翻译"), c))

        c = Card()
        self.asr_ep = _combo(names, a.endpoint)
        c.add_row(tr("服务"), self.asr_ep)
        self.asr_model = ModelBox(lambda: cfg.find_endpoint(self.asr_ep.currentData()), a.model)
        c.add_row(tr("模型"), self.asr_model, stretch=True)
        self.second = _combo([("auto", tr("按需（推荐）")), ("all", tr("全部重识别（最慢）")), ("off", tr("关闭（最快）"))],
                             a.second_pass if a.two_pass else "off")
        c.add_row(tr("第二遍识别"), self.second, tr("带人名、称呼提示修正同音错字；按需只重识别含人名或疑似错字的片段"))
        self.lay.addWidget(section(tr("语音识别"), c))

        c = Card()
        self.think = _combo([("off", tr("关闭")), ("low", "low"), ("medium", "medium")], t.think)
        self.budget = QSpinBox(minimum=0, maximum=32768, singleStep=256, value=t.think_budget)
        self.budget.setSpecialValueText(tr("不限"))
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.think); row.addWidget(QLabel(tr("上限"))); row.addWidget(self.budget)
        w = QWidget(); w.setLayout(row)
        c.add_row(tr("思考"), w, tr("每批最多思考多少 token；低于约 700 时模型容易答错格式，反而更慢"))
        self.batch = QSpinBox(minimum=0, maximum=60, value=t.batch_size, suffix=tr(" 行"))
        self.batch.setSpecialValueText(tr("自动"))
        c.add_row(tr("每批行数"), self.batch, tr("自动：不思考时每批 40 行，思考时 20 行"))
        self.fb_ep = _combo([("", tr("不使用"))] + names, t.fallback_endpoint)
        c.add_row(tr("被拒时改用"), self.fb_ep, tr("云端服务因内容审核拒绝某一批时，改用这个服务翻译那一批"))
        self.fb_model = ModelBox(lambda: cfg.find_endpoint(self.fb_ep.currentData()), t.fallback_model)
        self.fb_model.combo.lineEdit().setPlaceholderText(tr("留空 = 与翻译模型相同"))
        c.add_row(tr("备用模型"), self.fb_model, stretch=True)
        self.vad = QDoubleSpinBox(decimals=2, minimum=0.05, maximum=0.9, singleStep=0.05, value=a.vad_threshold)
        c.add_row(tr("说话检测灵敏度"), self.vad, tr("越低越不容易漏句，但更容易把喘息、背景声当成说话；实测 0.25 最合适"))
        self.maxspeech = QDoubleSpinBox(decimals=1, minimum=3, maximum=30, value=a.max_speech_s, suffix=tr(" 秒"))
        c.add_row(tr("单句最长"), self.maxspeech)
        self.ffmpeg = QLineEdit(g.ffmpeg_path); self.ffmpeg.setPlaceholderText(tr("一般不用填"))
        c.add_row(tr("ffmpeg 路径"), self.ffmpeg, tr("个别格式读不了时才会用到"), stretch=True)
        self.lay.addWidget(section(tr("高级"), c))

        foot = QHBoxLayout(); foot.addStretch(1)
        b = mini(QPushButton(tr("从配置文件重新载入"))); b.clicked.connect(self.win.reload_config); foot.addWidget(b)
        w = QWidget(); w.setLayout(foot); self.lay.addWidget(w)
        self.lay.addStretch(1)

        self.quality.currentIndexChanged.connect(self._quality_to_fields)
        self.think.currentIndexChanged.connect(self._fields_to_quality)
        self.budget.valueChanged.connect(self._fields_to_quality)
        self._watch(self.src, self.fmt, self.bilingual, self.exists, self.tr_ep, self.tr_model, self.quality,
                    self.asr_ep, self.asr_model, self.second, self.think, self.budget, self.batch, self.fb_ep,
                    self.fb_model, self.vad, self.maxspeech, self.ffmpeg)
        self._building = False

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
        self._save_timer.stop()
        cfg = self.win.cfg
        g, a, t = cfg.general, cfg.asr, cfg.translate
        g.source_lang, g.output_format = self.src.currentData(), self.fmt.currentData()
        g.bilingual, g.on_exists, g.ffmpeg_path = self.bilingual.isChecked(), self.exists.currentData(), self.ffmpeg.text().strip()
        a.endpoint, a.model = self.asr_ep.currentData(), self.asr_model.value()
        a.vad_threshold, a.max_speech_s = self.vad.value(), self.maxspeech.value()
        a.two_pass = self.second.currentData() != "off"
        if a.two_pass:
            a.second_pass = self.second.currentData()
        t.endpoint, t.model = self.tr_ep.currentData(), self.tr_model.value()
        t.think, t.think_budget = self.think.currentData(), self.budget.value()
        t.fallback_endpoint, t.fallback_model = self.fb_ep.currentData(), self.fb_model.value()
        t.batch_size = self.batch.value()
        save(cfg)
        self.win.settings_changed(rebuild=False)
        self.saved.setText(tr("已保存 {t}").format(t=time.strftime("%H:%M:%S")))
