"""Environment check page: decoder, endpoints, models, files; one-click ASR model download for oMLX."""
import os
import shutil
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
                               QWidget)
from platformdirs import user_cache_dir

from .. import jobs, models
from ..config import save
from ..engine import builtin, manifest
from ..api import ChatClient
from ..media import find_ffmpeg
from .style import Card, StatusDot, page_title, secondary, section
from .widgets import open_file, reveal, run_async, tr


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


class BuiltinModels(Card):
    """One row per built-in model: role, size, in use, status, download / delete / cancel."""

    def __init__(self, window):
        super().__init__()
        self.win = window
        dl = window.downloads
        self.source = QComboBox()
        for k, v in models.SOURCES.items():
            self.source.addItem(tr(v), k)
        self.source.currentIndexChanged.connect(self._source_changed)
        self.add_row(tr("下载源"), self.source, tr("可断点续传；国内网络选「国内镜像」通常更快"))
        self.rows = {}
        for m in manifest.MODELS.values():
            state = QLabel(); state.setProperty("secondary", True)
            btn = QPushButton(); btn.clicked.connect(lambda _=False, i=m.id: self._act(i))
            box = QWidget(); h = QHBoxLayout(box); h.setContentsMargins(0, 0, 0, 0); h.addWidget(state); h.addWidget(btn)
            row = self.add_row(m.label, box, " ")
            self.rows[m.id] = (row.findChildren(QLabel)[1], state, btn)
        dl.changed.connect(self.refresh)
        dl.progress.connect(lambda mid, d, t: self._show(mid))
        dl.finished.connect(self._finished)
        self.refresh()

    def _source_changed(self):
        cfg = self.win.cfg
        if cfg.general.download_source != self.source.currentData():
            cfg.general.download_source = self.source.currentData()
            save(cfg)

    def refresh(self):
        cfg = self.win.cfg
        self.source.blockSignals(True)
        self.source.setCurrentIndex(max(0, self.source.findData(cfg.general.download_source)))
        self.source.blockSignals(False)
        used = set()
        for sec in (cfg.asr, cfg.translate, cfg.effective().asr, cfg.effective().translate):
            if builtin.is_builtin(cfg.find_endpoint(sec.endpoint)):
                used.add(sec.model)
        for mid in self.rows:
            m = manifest.MODELS[mid]
            role = tr("语音识别") if m.kind == "asr" else tr("翻译")
            self.rows[mid][0].setText(f"{role} · {m.size / 1e9:.1f} GB · {m.license}" + (tr(" · 正在使用") if mid in used else ""))
            self._show(mid)

    def _show(self, mid):
        _, state, btn = self.rows[mid]
        st = self.win.downloads.state(mid)
        text = {"installed": tr("已下载"), "missing": tr("未下载"), "queued": tr("排队中"),
                "downloading": tr("下载中 {p}%").format(p=self.win.downloads.percent(mid))}[st]
        state.setText(text)
        btn.setText({"installed": tr("删除"), "missing": tr("下载")}.get(st, tr("取消")))

    def _act(self, mid):
        dl = self.win.downloads
        st = dl.state(mid)
        if st == "missing":
            dl.start([mid])
        elif st == "installed":
            if QMessageBox.question(self, "PolySub", tr("删除「{n}」？以后用到时需要重新下载。").format(
                    n=manifest.MODELS[mid].label)) == QMessageBox.Yes:
                os.remove(manifest.path_of(mid))
                self.refresh()
                self.win.environment.run_checks()
        else:
            dl.cancel(mid)

    def _finished(self, mid, err):
        self.refresh()
        self.win.environment.run_checks()
        if err and err != "cancelled":
            QMessageBox.warning(self, "PolySub", tr("下载失败（下次会接着下）：") + err)


class EnvironmentPage(QWidget):
    """Models page: built-in models, status checks, oMLX download (advanced), file locations."""

    def __init__(self, window):
        super().__init__()
        self.win = window
        self.checks = Card()
        self.builtin_models = BuiltinModels(window)

        self.dl_card = Card()
        self.dl_info = secondary(small=False)
        self.dl_bar = QProgressBar(); self.dl_bar.setVisible(False)
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0)
        self.dl_btn = QPushButton(tr("下载到本机 oMLX 并加载")); self.dl_btn.clicked.connect(self.download)
        self.dl_cancel = QPushButton(tr("取消下载")); self.dl_cancel.setVisible(False)
        self.dl_cancel.clicked.connect(lambda: self._cancel.set())
        row.addWidget(self.dl_bar, 1); row.addStretch(0); row.addWidget(self.dl_cancel); row.addWidget(self.dl_btn)
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(8)
        v.addWidget(self.dl_info); v.addLayout(row)
        self.dl_card.add_widget(box)

        files = Card()
        self.cache_dir = user_cache_dir("PolySub", appauthor=False)
        rows = [(tr("内置模型"), manifest.models_dir, lambda: reveal(manifest.models_dir()), tr("在 Finder 中显示")),
                (tr("配置文件"), lambda: self.win.cfg.path, lambda: open_file(self.win.cfg.path), tr("打开")),
                (tr("日志"), lambda: jobs.LOG, lambda: reveal(jobs.LOG), tr("在 Finder 中显示")),
                (tr("识别缓存"), lambda: self.cache_dir, lambda: reveal(self.cache_dir), tr("在 Finder 中显示"))]
        self.file_labels = []
        for name, path, act, text in rows:
            b = QPushButton(text); b.clicked.connect(act)
            r = files.add_row(name, b, " ")
            hint = r.findChildren(QLabel)[1]
            hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.file_labels.append((hint, path))
        self.clear_btn = QPushButton(tr("清空")); self.clear_btn.clicked.connect(self.clear_cache)
        self.cache_row = files.add_row(tr("清空识别缓存"), self.clear_btn, " ")

        self.recheck = QPushButton(tr("重新检查")); self.recheck.clicked.connect(self.run_checks)
        head = QHBoxLayout(); head.addWidget(page_title(tr("模型"))); head.addStretch(1); head.addWidget(self.recheck)
        inner = QWidget(); inner.setMaximumWidth(720)
        lay = QVBoxLayout(inner); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(18)
        lay.addLayout(head)
        lay.addWidget(section(tr("内置模型"), self.builtin_models,
                              tr("PolySub 自带的本机识别和翻译引擎用这些模型；下载一次，之后离线也能用。")))
        lay.addWidget(section(tr("状态"), self.checks))
        self.omlx_section = section(tr("oMLX 语音识别模型（高级）"), self.dl_card)
        lay.addWidget(self.omlx_section)
        lay.addWidget(section(tr("文件位置"), files))
        lay.addStretch(1)
        outer = QHBoxLayout(self); outer.setContentsMargins(20, 16, 20, 20); outer.addWidget(inner, 1); outer.addStretch(0)
        self._cancel = threading.Event()
        self._prog = _Progress()
        self._prog.changed.connect(self._show_progress)
        self.run_checks()

    def _show_progress(self, done_, total):
        if total:
            self.dl_bar.setValue(int(1000 * done_ / total))
            self.dl_bar.setFormat(f"{done_ / 1e9:.2f} / {total / 1e9:.2f} GB")

    def _set_rows(self, results):
        self.checks.clear()
        for ok, name, detail in results:
            dot = StatusDot(ok)
            r = self.checks.add_row(name, None, detail)
            r.layout().insertWidget(0, dot, 0, Qt.AlignTop)
            dot.setContentsMargins(0, 3, 0, 0)
            for lab in r.findChildren(QLabel)[1:]:
                lab.setTextInteractionFlags(Qt.TextSelectableByMouse)

    def run_checks(self):
        cfg = self.win.cfg
        for lab, path in self.file_labels:
            lab.setText(path())
        size = _dir_size(self.cache_dir) if os.path.isdir(self.cache_dir) else 0
        self.cache_row.findChildren(QLabel)[1].setText(tr("已用 {mb:.1f} MB；清空后，处理过的视频再生成其他语言时要重新识别").format(mb=size / 1e6))
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
            res.extend(builtin.check(cfg))
            for role, epname, model in ((tr("语音识别"), cfg.asr.endpoint, cfg.asr.model),
                                        (tr("翻译"), cfg.translate.endpoint, cfg.translate.model)):
                ep = cfg.find_endpoint(epname)
                if not ep:
                    res.append((False, role, tr("接口「{n}」不存在").format(n=epname)))
                    continue
                if builtin.is_builtin(ep):
                    continue
                try:
                    ms = ChatClient(ep, model).list_models()
                    ok = model in ms
                    res.append((ok, role, f"{ep.name}（{ep.base_url}）· {model}" +
                                ("" if ok else tr(" —— 接口里没有这个模型"))))
                    if role == tr("语音识别") and not ok:
                        asr_missing = ep
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    if "Max retries" in msg or "Connection" in msg:
                        msg = tr("连不上 {u}，服务可能没有启动").format(u=ep.base_url)
                    res.append((False, role, tr("{n}：{e}").format(n=ep.name, e=msg[:160])))
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
            self.omlx_section.setVisible(local_omlx)
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
