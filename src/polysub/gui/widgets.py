"""Shared GUI helpers."""
import os
import subprocess
import sys
import traceback

from PySide6.QtCore import QCoreApplication, QObject, QRunnable, QThreadPool, Signal

from .. import langs


def tr(text: str) -> str:
    return QCoreApplication.translate("PolySub", text)


# quality presets shown in the UI -> (think, think_budget)
QUALITY = {
    "fast": (tr("快速"), "off", 0),
    "standard": (tr("标准（推荐）"), "low", 1024),
    "fine": (tr("精细"), "low", 0),
}


def quality_of(think: str, budget: int) -> str:
    for k, (_, t, b) in QUALITY.items():
        if t == think and b == budget:
            return k
    return "custom"


def lang_label(code: str) -> str:
    return f"{langs.label(code)}（{code}）" if langs.label(code) != code else code


def reveal(path: str):
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", path])
    elif sys.platform == "win32":
        subprocess.run(["explorer", "/select,", os.path.normpath(path)])
    else:
        subprocess.run(["xdg-open", os.path.dirname(path)])


def open_file(path: str):
    if sys.platform == "darwin":
        subprocess.run(["open", path])
    elif sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", path])


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    """Run fn() on the thread pool; deliver result/error on the GUI thread."""

    def __init__(self, fn, on_done=None, on_fail=None):
        super().__init__()
        self.fn = fn
        self.sig = _Signals()
        if on_done:
            self.sig.done.connect(on_done)
        if on_fail:
            self.sig.failed.connect(on_fail)

    def run(self):
        try:
            result, error = self.fn(), None
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            result, error = None, str(e)
        try:  # the receiving window may already be gone (e.g. app quitting)
            if error is None:
                self.sig.done.emit(result)
            else:
                self.sig.failed.emit(error)
        except RuntimeError:
            pass


_keep = set()


def run_async(fn, on_done=None, on_fail=None):
    t = Task(fn, on_done, on_fail)
    _keep.add(t.sig)  # keep signals alive until delivered
    t.sig.done.connect(lambda *_: _keep.discard(t.sig))
    t.sig.failed.connect(lambda *_: _keep.discard(t.sig))
    t.setAutoDelete(True)
    QThreadPool.globalInstance().start(t)
