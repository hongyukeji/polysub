"""Environment check page: decoder, endpoints, models, files; one-click ASR model download for oMLX."""
import os
import shutil
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)
from platformdirs import user_cache_dir

from .. import jobs, models
from ..api import ChatClient
from ..media import find_ffmpeg
from .common import open_file, reveal, run_async, tr


def _dir_size(p):
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


class _Progress(QObject):
    changed = Signal(object, object)  # done, total (thread -> GUI)


class DoctorPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self.grid = QGridLayout()
        checks = QGroupBox(tr("环境检查")); checks.setLayout(self.grid)

        self.dl_box = QGroupBox(tr("语音识别模型"))
        dl = QVBoxLayout(self.dl_box)
        self.dl_info = QLabel(); self.dl_info.setWordWrap(True)
        self.dl_bar = QProgressBar(); self.dl_bar.setVisible(False)
        row = QHBoxLayout()
        self.dl_btn = QPushButton(tr("下载到本机 oMLX 并加载")); self.dl_btn.clicked.connect(self.download)
        self.dl_cancel = QPushButton(tr("取消下载")); self.dl_cancel.setVisible(False)
        self.dl_cancel.clicked.connect(lambda: self._cancel.set())
        row.addWidget(self.dl_btn); row.addWidget(self.dl_cancel); row.addStretch(1)
        dl.addWidget(self.dl_info); dl.addWidget(self.dl_bar); dl.addLayout(row)

        files = QGroupBox(tr("文件位置")); fl = QGridLayout(files); fl.setColumnStretch(1, 1)
        self.cache_dir = user_cache_dir("PolySub", appauthor=False)
        rows = [(tr("配置文件"), lambda: self.win.cfg.path, lambda: open_file(self.win.cfg.path)),
                (tr("日志"), lambda: jobs.LOG, lambda: reveal(jobs.LOG)),
                (tr("识别缓存"), lambda: self.cache_dir, lambda: reveal(self.cache_dir))]
        self.file_labels = []
        for i, (name, path, act) in enumerate(rows):
            fl.addWidget(QLabel(name), i, 0)
            lab = QLabel(); lab.setTextInteractionFlags(Qt.TextSelectableByMouse); fl.addWidget(lab, i, 1)
            b = QPushButton(tr("打开")); b.clicked.connect(act); fl.addWidget(b, i, 2, Qt.AlignRight)
            self.file_labels.append((lab, path))
        self.clear_btn = QPushButton(tr("清空识别缓存")); self.clear_btn.clicked.connect(self.clear_cache)
        fl.addWidget(self.clear_btn, len(rows), 1, 1, 2, Qt.AlignRight)

        top = QHBoxLayout(); top.addStretch(1)
        b = QPushButton(tr("重新检查")); b.clicked.connect(self.run_checks); top.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addLayout(top); lay.addWidget(checks); lay.addWidget(self.dl_box); lay.addWidget(files); lay.addStretch(1)
        self._cancel = threading.Event()
        self._prog = _Progress()
        self._prog.changed.connect(self._show_progress)
        self.run_checks()

    def _show_progress(self, done_, total):
        if total:
            self.dl_bar.setValue(int(1000 * done_ / total))
            self.dl_bar.setFormat(f"{done_ / 1e9:.2f} / {total / 1e9:.2f} GB")

    def _set_rows(self, results):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, (ok, name, detail) in enumerate(results):
            mark = QLabel({True: "✅", False: "❌", None: "➖"}[ok])
            self.grid.addWidget(mark, i, 0)
            self.grid.addWidget(QLabel(f"<b>{name}</b>"), i, 1)
            d = QLabel(detail); d.setWordWrap(True); d.setStyleSheet("color: palette(placeholder-text);")
            d.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.grid.addWidget(d, i + 0, 2)
        self.grid.setColumnStretch(1, 0)
        self.grid.setColumnStretch(2, 1)
        self.grid.setColumnMinimumWidth(1, 160)

    def run_checks(self):
        cfg = self.win.cfg
        for lab, path in self.file_labels:
            lab.setText(path())
        size = _dir_size(self.cache_dir) if os.path.isdir(self.cache_dir) else 0
        self.clear_btn.setText(tr("清空识别缓存（{mb:.1f} MB）").format(mb=size / 1e6))
        self._set_rows([(None, tr("检查中…"), "")])

        def work():
            res = []
            try:
                import av
                res.append((True, tr("音频解码"), f"PyAV {av.__version__}"))
            except Exception as e:  # noqa: BLE001
                res.append((False, tr("音频解码"), str(e)))
            ff = find_ffmpeg(cfg.general.ffmpeg_path)
            res.append((True if ff else None, tr("系统 ffmpeg（可选）"), ff or tr("未安装，一般不需要；个别格式读不了时才会用到")))
            asr_missing = None
            for role, epname, model in ((tr("语音识别"), cfg.asr.endpoint, cfg.asr.model),
                                        (tr("翻译"), cfg.translate.endpoint, cfg.translate.model)):
                ep = cfg.find_endpoint(epname)
                if not ep:
                    res.append((False, role, tr("接口「{n}」不存在").format(n=epname)))
                    continue
                try:
                    ms = ChatClient(ep, model).list_models()
                    ok = model in ms
                    res.append((ok, role, f"{ep.name}（{ep.base_url}）· {model}" +
                                ("" if ok else tr(" —— 接口里没有这个模型"))))
                    if role == tr("语音识别") and not ok:
                        asr_missing = ep
                except Exception as e:  # noqa: BLE001
                    res.append((False, role, tr("{n} 无法连接：{e}").format(n=ep.name, e=str(e)[:160])))
            fb = cfg.translate.fallback_endpoint
            if fb:
                ep = cfg.find_endpoint(fb)
                res.append((bool(ep), tr("被拒时改用"), fb if ep else tr("接口「{n}」不存在").format(n=fb)))
            return res, asr_missing

        def done(out):
            res, asr_missing = out
            self._set_rows(res)
            cfg = self.win.cfg
            ep = cfg.find_endpoint(cfg.asr.endpoint)
            local_omlx = bool(ep and ep.preset == "omlx")
            target = os.path.join(models.omlx_models_dir(), models.DEFAULT_ASR_REPO)
            have = os.path.isdir(target) and any(f.endswith(".safetensors") for f in os.listdir(target))
            if not local_omlx:
                self.dl_info.setText(tr("语音识别用的是「{n}」，由该服务提供模型，这里不需要下载。").format(n=cfg.asr.endpoint))
                self.dl_btn.setEnabled(False)
            elif have and not asr_missing:
                self.dl_info.setText(tr("已安装：{p}").format(p=target))
                self.dl_btn.setEnabled(False)
            else:
                self.dl_info.setText(tr("推荐的语音识别模型 {r}（约 2.5GB）{s}。下载到 {d}，完成后自动让 oMLX 重新发现模型"
                                        "（oMLX 会短暂卸载并重新加载已固定的模型）。").format(
                    r=models.DEFAULT_ASR_REPO, s=tr("已下载，但 oMLX 还没加载") if have else tr("尚未安装"),
                    d=models.omlx_models_dir()))
                self.dl_btn.setEnabled(True)

        run_async(work, done, lambda m: self._set_rows([(False, tr("检查失败"), m)]))

    def download(self):
        cfg = self.win.cfg
        ep = cfg.find_endpoint(cfg.asr.endpoint)
        dest = os.path.join(models.omlx_models_dir(), models.DEFAULT_ASR_REPO)
        self._cancel = threading.Event()
        self.dl_btn.setEnabled(False); self.dl_cancel.setVisible(True); self.dl_bar.setVisible(True)
        self.dl_bar.setRange(0, 1000)

        def prog(done_, total, name):
            self._prog.changed.emit(done_, total)

        def work():
            models.download(models.DEFAULT_ASR_REPO, dest, progress=prog, cancel=self._cancel)
            msg = models.omlx_reload(ep.root, ep.api_key)
            if cfg.asr.model != os.path.basename(dest):
                cfg.asr.model = os.path.basename(dest)
            return msg

        def done(msg):
            self.dl_cancel.setVisible(False)
            from ..config import save
            save(cfg)
            self.win.settings_changed()
            QMessageBox.information(self, "PolySub", tr("下载完成，oMLX 已重新加载模型：") + str(msg))
            self.run_checks()

        def fail(msg):
            self.dl_cancel.setVisible(False); self.dl_btn.setEnabled(True)
            QMessageBox.warning(self, "PolySub", tr("下载失败（下次会接着下）：") + msg)

        run_async(work, done, fail)

    def clear_cache(self):
        if QMessageBox.question(self, "PolySub", tr("清空后，已经处理过的视频再生成其他语言时需要重新识别。继续吗？")) \
                != QMessageBox.Yes:
            return
        shutil.rmtree(self.cache_dir, ignore_errors=True)
        self.run_checks()
