"""Settings page: common options, and advanced ones (services and models, "my models",
translation and recognition details, built-in engine, automation) behind a toggle."""
import time

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..api import ChatClient
from ..config import Asr, Engine, Translate, save
from ..config import quality_of as quality_of_cfg
from ..engine import manifest
from . import style
from .style import Card, section
from .widgets import MINE_LABEL, QUALITY, lang_label, quality_of, run_async, tr

SOURCE_LANGS = ["auto", "ja", "en", "zh", "ko", "yue", "fr", "de", "es", "ru", "pt", "it", "th", "vi", "id"]
THINKING_STYLES = {
    "chat_template_kwargs": tr("oMLX / vLLM（chat_template_kwargs）"),
    "enable_thinking": tr("阿里百炼（enable_thinking）"),
    "deepseek": tr("DeepSeek（thinking）"),
    "reasoning_effort": tr("OpenAI（reasoning_effort）"),
    "none": tr("不发送（模型不支持思考开关）"),
}


def _combo(items, current):
    c = QComboBox()
    c.setMinimumWidth(style.CONTROL_WIDTH)
    for data, label in items:
        c.addItem(label, data)
    i = c.findData(current)
    c.setCurrentIndex(i if i >= 0 else 0)
    return c


class ModelBox(QWidget):
    """Editable model combo + "获取列表" (the endpoint's /v1/models, or the built-in model list)
    + "文件…" for the built-in engine (the user's own GGUF / whisper model file)."""

    def __init__(self, get_endpoint, value):
        super().__init__()
        self.get_endpoint = get_endpoint
        self.combo = QComboBox(editable=True)
        self.combo.setMinimumWidth(220)
        self.combo.setEditText(value)
        self.btn = QPushButton(tr("获取列表"))
        self.btn.setToolTip(tr("从这个服务读取可用的模型"))
        self.btn.clicked.connect(self.fetch)
        self.file_btn = QPushButton(tr("文件…"))
        self.file_btn.setToolTip(tr("内置引擎：选择本机的模型文件（翻译用 .gguf，识别用 whisper.cpp 的 .bin）；"
                                    "也可以直接填 hf:用户/仓库/文件名，用到时自动下载"))
        self.file_btn.clicked.connect(self.pick_file)
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.combo, 1); lay.addWidget(self.btn); lay.addWidget(self.file_btn)
        self.endpoint_changed()

    def value(self):
        return self.combo.currentText().strip()

    def _builtin(self):
        ep = self.get_endpoint()
        return bool(ep) and ep.preset == "builtin"

    def endpoint_changed(self):
        self.file_btn.setVisible(self._builtin())

    def pick_file(self):
        f, _ = QFileDialog.getOpenFileName(self, tr("选择模型文件"), manifest.models_dir(),
                                           tr("模型文件") + " (*.gguf *.bin);;" + tr("所有文件") + " (*)")
        if f:
            self.combo.setEditText(f)

    def fetch(self):
        ep = self.get_endpoint()
        if not ep:
            return
        cur = self.value()
        if self._builtin():
            self.combo.clear()
            self.combo.addItems(list(manifest.MODELS))
            self.combo.setEditText(cur)
            self.combo.showPopup()
            return
        self.btn.setEnabled(False)

        def done(models):
            self.btn.setEnabled(True)
            self.combo.clear()
            self.combo.addItems(models)
            self.combo.setEditText(cur)

        def fail(msg):
            self.btn.setEnabled(True)
            QMessageBox.warning(self, "PolySub", tr("获取模型列表失败：") + msg)

        run_async(lambda: ChatClient(ep, cur).list_models(), done, fail)


def _switch(on: bool) -> style.Switch:
    return style.Switch(on)


class SettingsPage(QWidget):
    """Common settings first, the rest behind "显示高级设置"; every change is saved right away."""

    def __init__(self, window):
        super().__init__()
        self.win = window
        self._building = False
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self.apply)
        self.lay = style.form_page(self)
        self.build()

    def _watch(self, *widgets):
        for w in widgets:
            if isinstance(w, ModelBox):
                w.combo.currentTextChanged.connect(self._changed)
                continue
            for sig in ("currentIndexChanged", "valueChanged", "toggled", "editingFinished"):
                if hasattr(w, sig):
                    getattr(w, sig).connect(self._changed)
                    break

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
        g, a, t, m, e = cfg.general, cfg.asr, cfg.translate, cfg.mine, cfg.engine
        names = [(x.name, x.name) for x in cfg.endpoints]

        self.saved = style.secondary(tr("更改会自动保存"), small=False, wrap=False)
        self.lay.addWidget(style.PageHeader(tr("设置"), "", self.saved))

        # ---- common ----
        c = Card()
        self.src = _combo([(x, tr("自动识别") if x == "auto" else lang_label(x)) for x in SOURCE_LANGS], g.source_lang)
        c.add_row(tr("视频原语言"), self.src)
        self.fmt = _combo([("srt", "SRT"), ("ass", "ASS"), ("vtt", "WebVTT")], g.output_format)
        c.add_row(tr("字幕格式"), self.fmt)
        self.bilingual = _switch(g.bilingual)
        c.add_row(tr("双语字幕"), self.bilingual, tr("译文下面附原文"))
        self.exists = _combo([("skip", tr("跳过")), ("overwrite", tr("覆盖")), ("rename", tr("另存为新文件"))], g.on_exists)
        c.add_row(tr("已有同名字幕时"), self.exists)
        self.quality = _combo([(k, v[0]) for k, v in QUALITY.items()] + [("mine", MINE_LABEL), ("custom", tr("自定义"))],
                              quality_of_cfg(cfg))
        c.add_row(tr("翻译质量"), self.quality, tr("快速不思考、速度最快；标准和精细更准确但更慢；「我的模型」用高级设置里配好的组合"))
        self.lay.addWidget(section(tr("常规"), c))

        self.adv_btn = QPushButton(tr("隐藏高级设置") if self._show_adv() else tr("显示高级设置"))
        self.adv_btn.clicked.connect(lambda: self._toggle_adv(self.adv.isHidden()))
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0); row.addStretch(1); row.addWidget(self.adv_btn)
        w = QWidget(); w.setLayout(row); self.lay.addWidget(w)

        # ---- advanced ----
        self.adv = QWidget(); av = QVBoxLayout(self.adv); av.setContentsMargins(0, 0, 0, 0)
        av.setSpacing(style.SECTION_SPACING)

        c = Card()
        self.tr_ep = _combo(names, t.endpoint)
        c.add_row(tr("翻译服务"), self.tr_ep, tr("「内置（本机）」不需要另装软件；其他服务在「自定义服务」里添加"))
        self.tr_model = ModelBox(lambda: cfg.find_endpoint(self.tr_ep.currentData()), t.model)
        c.add_row(tr("翻译模型"), self.tr_model, stretch=True)
        self.asr_ep = _combo(names, a.endpoint)
        c.add_row(tr("识别服务"), self.asr_ep)
        self.asr_model = ModelBox(lambda: cfg.find_endpoint(self.asr_ep.currentData()), a.model)
        c.add_row(tr("识别模型"), self.asr_model, stretch=True)
        self.second = _combo([("auto", tr("按需（推荐）")), ("all", tr("全部重识别（最慢）")), ("off", tr("关闭（最快）"))],
                             a.second_pass if a.two_pass else "off")
        c.add_row(tr("第二遍识别"), self.second, tr("带人名、称呼提示修正同音错字；按需只重识别含人名或疑似错字的片段"))
        av.addWidget(section(tr("默认的识别与翻译"), c))

        c = Card()
        self.m_tr_ep = _combo([("", tr("未设置"))] + names, m.translate_endpoint)
        c.add_row(tr("翻译服务"), self.m_tr_ep)
        self.m_tr_model = ModelBox(lambda: cfg.find_endpoint(self.m_tr_ep.currentData()), m.translate_model)
        c.add_row(tr("翻译模型"), self.m_tr_model, stretch=True)
        self.m_asr_ep = _combo([("", tr("与默认相同"))] + names, m.asr_endpoint)
        c.add_row(tr("识别服务"), self.m_asr_ep)
        self.m_asr_model = ModelBox(lambda: cfg.find_endpoint(self.m_asr_ep.currentData()), m.asr_model)
        c.add_row(tr("识别模型"), self.m_asr_model, stretch=True)
        self.m_think = _combo([("off", tr("关闭")), ("low", "low"), ("medium", "medium")], m.think)
        self.m_budget = QSpinBox(minimum=0, maximum=32768, singleStep=256, value=m.think_budget)
        self.m_budget.setSpecialValueText(tr("不限"))
        c.add_row(tr("思考"), self._pair(self.m_think, tr("上限"), self.m_budget))
        av.addWidget(section(MINE_LABEL, c, tr("高精度组合，例如本机 oMLX 的大模型或云端服务。任务页「翻译质量」选「我的模型」时使用。")))

        c = Card()
        self.think = _combo([("off", tr("关闭")), ("low", "low"), ("medium", "medium")], t.think)
        self.budget = QSpinBox(minimum=0, maximum=32768, singleStep=256, value=t.think_budget)
        self.budget.setSpecialValueText(tr("不限"))
        c.add_row(tr("思考"), self._pair(self.think, tr("上限"), self.budget),
                  tr("每批最多思考多少 token；低于约 700 时模型容易答错格式，反而更慢"))
        self.batch = QSpinBox(minimum=0, maximum=60, value=t.batch_size, suffix=tr(" 行"))
        self.batch.setSpecialValueText(tr("自动"))
        c.add_row(tr("每批行数"), self.batch, tr("自动：不思考时每批 40 行，思考时 20 行"))
        self.ctx = QSpinBox(minimum=0, maximum=30, value=t.context_lines, suffix=tr(" 行"))
        self.ahead = QSpinBox(minimum=0, maximum=30, value=t.lookahead_lines, suffix=tr(" 行"))
        c.add_row(tr("上下文"), self._pair(QLabel(tr("前文")), self.ctx, tr("后文"), self.ahead),
                  tr("每批带上前面几行（含译文）和后面几行原文，帮助判断主语和语气"))
        self.glossary = _switch(t.glossary)
        c.add_row(tr("术语表"), self.glossary, tr("先整理人名、称呼、反复出现的词的译法，每批按表翻译"))
        self.careful = _switch(t.careful_prompt)
        c.add_row(tr("严格的翻译要求"), self.careful, tr("补全省略的主语、核对否定和授受关系、识别噪声不硬译"))
        self.cont = _switch(t.continuation_marks)
        c.add_row(tr("标记没说完的句子"), self.cont, tr("一句话被切成两行时，提示模型当成一句来译"))
        self.check = _switch(t.check_output)
        c.add_row(tr("检查译文并重译"), self.check, tr("残留原文、混入外语单词、明显漏译、术语不符的行再译一次"))
        self.review = _switch(t.review)
        c.add_row(tr("重译时开启思考"), self.review, tr("更准，但更慢"))
        self.fb_ep = _combo([("", tr("不使用"))] + names, t.fallback_endpoint)
        c.add_row(tr("被拒时改用"), self.fb_ep, tr("云端服务因内容审核拒绝某一批时，改用这个服务翻译那一批"))
        self.fb_model = ModelBox(lambda: cfg.find_endpoint(self.fb_ep.currentData()), t.fallback_model)
        self.fb_model.combo.lineEdit().setPlaceholderText(tr("留空 = 与翻译模型相同"))
        c.add_row(tr("备用模型"), self.fb_model, stretch=True)
        av.addWidget(section(tr("翻译细节"), c))

        c = Card()
        self.vad = QDoubleSpinBox(decimals=2, minimum=0.05, maximum=0.9, singleStep=0.05, value=a.vad_threshold)
        c.add_row(tr("说话检测灵敏度"), self.vad, tr("越低越不容易漏句，但更容易把喘息、背景声当成说话；实测 0.25 最合适"))
        self.maxspeech = QDoubleSpinBox(decimals=1, minimum=3, maximum=30, value=a.max_speech_s, suffix=tr(" 秒"))
        c.add_row(tr("单句最长"), self.maxspeech)
        self.ffmpeg = QLineEdit(g.ffmpeg_path); self.ffmpeg.setPlaceholderText(tr("一般不用填"))
        c.add_row(tr("ffmpeg 路径"), self.ffmpeg, tr("个别格式读不了时才会用到"), stretch=True)
        av.addWidget(section(tr("识别细节"), c))

        c = Card()
        self.e_ctx = QSpinBox(minimum=2048, maximum=131072, singleStep=2048, value=e.ctx_size)
        c.add_row(tr("上下文长度"), self.e_ctx, tr("翻译引擎每个并发槽位的 token 数；越大越占内存"))
        self.e_gpu = QSpinBox(minimum=0, maximum=999, value=e.gpu_layers)
        self.e_gpu.setSpecialValueText(tr("只用 CPU"))
        c.add_row(tr("GPU 层数"), self.e_gpu, tr("999 = 全部放到 GPU（推荐）"))
        self.e_idle = QSpinBox(minimum=1, maximum=240, value=e.idle_minutes, suffix=tr(" 分钟"))
        c.add_row(tr("空闲多久后退出"), self.e_idle, tr("释放内存；下次用到时自动启动"))
        av.addWidget(section(tr("内置引擎"), c, tr("翻译的并发数在「自定义服务 › 内置（本机）」里设置。改动在引擎下次启动时生效。")))

        c = Card()
        self.watch = QLineEdit(g.watch_dir); self.watch.setPlaceholderText(tr("未设置"))
        pick = QPushButton(tr("选择…")); pick.clicked.connect(self._pick_watch)
        c.add_row(tr("监视文件夹"), self._pair(self.watch, pick), tr("PolySub 打开时，这里新出现的视频会自动加入队列"),
                  stretch=True)
        av.addWidget(section(tr("自动化"), c))

        foot = QHBoxLayout(); foot.addStretch(1)
        b = QPushButton(tr("恢复默认")); b.clicked.connect(self.restore_defaults); foot.addWidget(b)
        b = QPushButton(tr("从配置文件重新载入")); b.clicked.connect(self.win.reload_config); foot.addWidget(b)
        w = QWidget(); w.setLayout(foot); av.addWidget(w)
        self.adv.setVisible(self._show_adv())
        self.lay.addWidget(self.adv)
        self.lay.addStretch(1)

        self.quality.currentIndexChanged.connect(self._quality_to_fields)
        self.think.currentIndexChanged.connect(self._fields_to_quality)
        self.budget.valueChanged.connect(self._fields_to_quality)
        for combo, box in ((self.tr_ep, self.tr_model), (self.asr_ep, self.asr_model), (self.m_tr_ep, self.m_tr_model),
                           (self.m_asr_ep, self.m_asr_model), (self.fb_ep, self.fb_model)):
            combo.currentIndexChanged.connect(box.endpoint_changed)
        self._watch(self.src, self.fmt, self.bilingual, self.exists, self.quality, self.tr_ep, self.tr_model,
                    self.asr_ep, self.asr_model, self.second, self.m_tr_ep, self.m_tr_model, self.m_asr_ep,
                    self.m_asr_model, self.m_think, self.m_budget, self.think, self.budget, self.batch, self.ctx,
                    self.ahead, self.glossary, self.careful, self.cont, self.check, self.review, self.fb_ep,
                    self.fb_model, self.vad, self.maxspeech, self.ffmpeg, self.e_ctx, self.e_gpu, self.e_idle,
                    self.watch)
        self._building = False

    @staticmethod
    def _pair(*items) -> QWidget:
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
        for x in items:
            h.addWidget(QLabel(x) if isinstance(x, str) else x)
        return w

    @staticmethod
    def _show_adv() -> bool:
        return QSettings("PolySub", "PolySub").value("settings/advanced", False, type=bool)

    def _toggle_adv(self, on):
        QSettings("PolySub", "PolySub").setValue("settings/advanced", on)
        self.adv.setVisible(on)
        self.adv_btn.setText(tr("隐藏高级设置") if on else tr("显示高级设置"))

    def _pick_watch(self):
        d = QFileDialog.getExistingDirectory(self, tr("选择要监视的文件夹"), self.watch.text())
        if d:
            self.watch.setText(d)
            self._changed()

    def _quality_to_fields(self):
        k = self.quality.currentData()
        if k == "mine" and not (self.m_tr_ep.currentData() and self.m_tr_model.value()):
            QMessageBox.information(self, "PolySub", tr("先在下面「高级 › 我的模型」里选好翻译服务和模型。"))
            self._toggle_adv(True)
            self._fields_to_quality()
            return
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

    def restore_defaults(self):
        """Reset the tuning values (not the chosen services and models)."""
        if QMessageBox.question(self, "PolySub", tr("把翻译细节、识别细节和内置引擎参数恢复为默认值？"
                                                   "所选的服务和模型不变。")) != QMessageBox.Yes:
            return
        cfg = self.win.cfg
        keep_t = {k: getattr(cfg.translate, k) for k in ("endpoint", "model", "fallback_endpoint", "fallback_model")}
        keep_a = {k: getattr(cfg.asr, k) for k in ("endpoint", "model")}
        cfg.translate = Translate(**keep_t)
        cfg.asr = Asr(**keep_a)
        cfg.engine = Engine()
        save(cfg)
        self.win.settings_changed()

    def apply(self):
        self._save_timer.stop()
        cfg = self.win.cfg
        g, a, t, m, e = cfg.general, cfg.asr, cfg.translate, cfg.mine, cfg.engine
        g.source_lang, g.output_format = self.src.currentData(), self.fmt.currentData()
        g.bilingual, g.on_exists, g.ffmpeg_path = self.bilingual.isChecked(), self.exists.currentData(), self.ffmpeg.text().strip()
        g.watch_dir = self.watch.text().strip()
        a.endpoint, a.model = self.asr_ep.currentData(), self.asr_model.value()
        a.vad_threshold, a.max_speech_s = self.vad.value(), self.maxspeech.value()
        a.two_pass = self.second.currentData() != "off"
        if a.two_pass:
            a.second_pass = self.second.currentData()
        t.endpoint, t.model = self.tr_ep.currentData(), self.tr_model.value()
        t.think, t.think_budget = self.think.currentData(), self.budget.value()
        t.fallback_endpoint, t.fallback_model = self.fb_ep.currentData(), self.fb_model.value()
        t.batch_size, t.context_lines, t.lookahead_lines = self.batch.value(), self.ctx.value(), self.ahead.value()
        t.glossary, t.careful_prompt, t.continuation_marks = (self.glossary.isChecked(), self.careful.isChecked(),
                                                              self.cont.isChecked())
        t.check_output, t.review = self.check.isChecked(), self.review.isChecked()
        m.translate_endpoint, m.translate_model = self.m_tr_ep.currentData(), self.m_tr_model.value()
        m.asr_endpoint, m.asr_model = self.m_asr_ep.currentData(), self.m_asr_model.value()
        m.think, m.think_budget = self.m_think.currentData(), self.m_budget.value()
        e.ctx_size, e.gpu_layers, e.idle_minutes = self.e_ctx.value(), self.e_gpu.value(), self.e_idle.value()
        g.use_mine = self.quality.currentData() == "mine" and m.configured
        save(cfg)
        self.win.settings_changed(rebuild=False)
        self.saved.setText(tr("已保存 {t}").format(t=time.strftime("%H:%M:%S")))
