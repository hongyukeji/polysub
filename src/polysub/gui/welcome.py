"""First launch without models: pick a tier (recommended by memory), a download
source, and download; the download can continue in the background."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QRadioButton, QVBoxLayout)

from .. import models, system
from ..config import save
from ..engine import manifest
from .style import Card, secondary
from .widgets import tr


def needs_welcome(cfg) -> bool:
    """Built-in engine in use and its models are neither on disk nor downloading."""
    ep = cfg.find_endpoint(cfg.translate.endpoint)
    return bool(ep and ep.preset == "builtin") and not manifest.downloading() and any(
        not manifest.is_installed(m) for m in (cfg.asr.model, cfg.translate.model) if m in manifest.MODELS)


class WelcomeDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.setWindowTitle(tr("欢迎使用 PolySub"))
        self.setMinimumWidth(520)
        cfg = window.cfg
        mem = system.total_memory()
        rec = manifest.recommended_tier(mem)

        title = QLabel(tr("欢迎使用 PolySub"), objectName="pageTitle")
        intro = secondary(tr("PolySub 在本机识别语音、翻译字幕，不需要另装软件或 API Key。"
                             "第一次使用先下载一次模型，之后离线也能用。"), small=False)
        card = Card()
        self.group = QButtonGroup(self)
        for tier, (asr_id, mt_id) in manifest.TIERS.items():
            size = sum(manifest.MODELS[m].size for m in (asr_id, mt_id))
            rb = QRadioButton(manifest.TIER_LABELS[tier])
            rb.setChecked(tier == rec)
            self.group.addButton(rb)
            rb.tier = tier
            card.add_row("", rb, tr("约 {gb:.1f} GB：{a} + {m}").format(
                gb=size / 1e9, a=manifest.MODELS[asr_id].label, m=manifest.MODELS[mt_id].label), stretch=True)
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

        lay = QVBoxLayout(self); lay.setContentsMargins(24, 20, 24, 18); lay.setSpacing(14)
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
