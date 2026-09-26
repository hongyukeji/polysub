"""First launch without models: pick a tier (recommended by memory), a download
source, and download; the download can continue in the background."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QHBoxLayout, QProgressBar, QPushButton,
                               QRadioButton, QVBoxLayout)

from .. import models, system
from ..config import save
from ..engine import manifest
from . import style
from .style import Card, secondary
from .widgets import tr


def needs_welcome(cfg) -> bool:
    """Built-in engine in use and its models are neither on disk nor downloading."""
    cfg = cfg.effective()   # "我的模型" switched on: no built-in models needed
    ep = cfg.find_endpoint(cfg.translate.endpoint)
    return bool(ep and ep.preset == "builtin") and not manifest.downloading() and any(
        not manifest.is_installed(m) for m in (cfg.asr.model, cfg.translate.model) if m in manifest.MODELS)


def omlx_choice(cfg):
    """(endpoint name, speech model, translation model) of an oMLX the user already has, or None.
    From "我的模型" when it points at oMLX, else from the running oMLX's model list."""
    m = cfg.mine
    ep = cfg.find_endpoint(m.translate_endpoint)
    if m.configured and ep and ep.preset == "omlx":
        return ep.name, m.asr_model, m.translate_model
    ep = next((e for e in cfg.endpoints if e.preset == "omlx"), None)
    if not ep:
        return None
    try:
        from ..api import ChatClient
        ids = ChatClient(ep, "").list_models()
    except Exception:  # noqa: BLE001 - oMLX not running / not installed
        return None
    asr = next((i for i in ids if "asr" in i.lower()), "")
    mt = next((i for i in ids if "asr" not in i.lower()), "")
    return (ep.name, asr, mt) if asr and mt else None


class WelcomeDialog(QDialog):
    def paintEvent(self, e):
        style.paint_content_background(self)

    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.setWindowTitle(tr("欢迎使用 PolySub"))
        self.setMinimumWidth(520)
        cfg = window.cfg
        mem = system.total_memory()
        rec = manifest.recommended_tier(mem)

        title = style.page_title(tr("欢迎使用 PolySub"))
        intro = secondary(tr("PolySub 在本机识别语音、翻译字幕，不需要另装软件或 API Key。"
                             "第一次使用先下载一次模型，之后离线也能用。"), small=False)
        card = Card()
        self.group = QButtonGroup(self)
        for tier, (asr_id, mt_id) in manifest.TIERS.items():
            size = sum(manifest.total_size(manifest.MODELS[m]) for m in (asr_id, mt_id))
            rb = QRadioButton()
            rb.setChecked(tier == rec)
            self.group.addButton(rb)
            rb.tier = tier
            row = card.add_row(manifest.TIER_LABELS[tier], rb, tr("约 {gb:.1f} GB：{a} + {m}").format(
                gb=size / 1e9, a=manifest.MODELS[asr_id].label, m=manifest.MODELS[mt_id].label))
            row.mousePressEvent = lambda e, b=rb: b.setChecked(True)   # the whole row selects the tier
        self.omlx = omlx_choice(cfg)
        if self.omlx:   # reuse what oMLX already downloaded (its MLX models cannot run in the built-in engine)
            rb = QRadioButton(); rb.tier = "omlx"; self.group.addButton(rb)
            row = card.add_row(tr("使用本机已有的 oMLX（不用下载）"), rb, tr("{a} + {m}；识别和翻译最准，需要 oMLX 保持运行").format(
                a=self.omlx[1], m=self.omlx[2]))
            row.mousePressEvent = lambda e, b=rb: b.setChecked(True)
            rb.toggled.connect(lambda on: self.go.setText(tr("使用 oMLX") if on else tr("开始下载")))
        self.source = QComboBox()
        for k, v in models.SOURCES.items():
            self.source.addItem(tr(v), k)
        self.source.setCurrentIndex(max(0, self.source.findData(cfg.general.download_source)))
        card.add_row(tr("下载源"), self.source, tr("国内网络选「国内镜像」通常更快"))
        mem_text = tr("本机内存 {gb:.0f} GB，推荐「{t}」。").format(gb=mem / 1024 ** 3, t=manifest.TIER_LABELS[rec]) \
            if mem else ""

        self.bar = QProgressBar(); self.bar.setRange(0, 100); self.bar.setVisible(False)
        self.status = secondary(mem_text + tr("以后可以在「模型」页更换或删除；高级用户可以在「设置 › 高级」里改用自己的模型。"),
                                small=False)
        self.later = QPushButton(tr("稍后"))
        self.later.clicked.connect(self.reject)
        self.go = QPushButton(tr("开始下载")); self.go.setDefault(True)
        self.go.clicked.connect(self.start)
        btns = QHBoxLayout(); btns.addStretch(1); btns.addWidget(self.later); btns.addWidget(self.go)

        lay = QVBoxLayout(self); lay.setContentsMargins(28, 24, 28, 20); lay.setSpacing(16)
        for w in (title, intro, card, self.bar, self.status):
            lay.addWidget(w)
        lay.addLayout(btns)
        dl = window.downloads
        dl.progress.connect(self._progress)
        dl.changed.connect(self._changed)

    def tier(self) -> str:
        return self.group.checkedButton().tier

    def start(self):
        cfg = self.win.cfg
        if self.tier() == "omlx":   # "我的模型" = the oMLX combination, switched on; nothing to download
            name, asr, mt = self.omlx
            m = cfg.mine
            m.asr_endpoint, m.asr_model, m.translate_endpoint, m.translate_model = name, asr, name, mt
            cfg.general.use_mine = True
            save(cfg)
            self.win.settings_changed()
            self.accept()
            return
        asr_id, mt_id = manifest.TIERS[self.tier()]
        for sec, mid in ((cfg.asr, asr_id), (cfg.translate, mt_id)):
            ep = cfg.find_endpoint(sec.endpoint)
            if ep and ep.preset == "builtin":
                sec.model = mid
        cfg.general.download_source = self.source.currentData()
        save(cfg)
        self.win.settings_changed()
        for b in self.group.buttons():
            b.setEnabled(False)
        self.source.setEnabled(False)
        self.bar.setVisible(True)
        self.go.setEnabled(False)
        self.later.setText(tr("在后台继续"))
        self.later.clicked.disconnect(); self.later.clicked.connect(self.accept)
        self.win.downloads.start([asr_id, mt_id])

    def _progress(self, mid, done, total):
        if total:
            m = manifest.remote(mid)
            self.bar.setValue(int(100 * done / total))
            self.status.setText(tr("正在下载 {n}：{d:.2f} / {t:.2f} GB").format(n=m.label if m else mid,
                                                                             d=done / 1e9, t=total / 1e9))

    def _changed(self):
        if self.go.isEnabled() or self.win.downloads.busy():
            return
        missing = [m for m in manifest.TIERS[self.tier()] if not manifest.is_installed(m)]
        if missing:
            self.status.setText(tr("下载没有完成，可以在「模型」页重试。"))
            self.later.setText(tr("关闭"))
        else:
            self.status.setText(tr("完成，可以把视频拖进窗口了。"))
            self.bar.setValue(100)
            self.later.setText(tr("开始使用"))
        self.later.setDefault(True)
        self.setWindowModality(Qt.NonModal)
