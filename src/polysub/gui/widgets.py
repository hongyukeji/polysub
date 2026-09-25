"""Shared GUI helpers."""
import os
import subprocess
import sys
import threading
import traceback

from PySide6.QtCore import QCoreApplication, QObject, Signal

from .. import langs
from ..config import QUALITY_LEVELS


def tr(text: str) -> str:
    return QCoreApplication.translate("PolySub", text)


# quality presets shown in the UI -> (label, think, think_budget); values from config.QUALITY_LEVELS
_LABELS = {"fast": tr("快速（推荐）"), "standard": tr("标准"), "fine": tr("精细")}
QUALITY = {k: (_LABELS[k], *v) for k, v in QUALITY_LEVELS.items()}
MINE_LABEL = tr("我的模型")


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


_keep = set()


def run_async(fn, on_done=None, on_fail=None):
    """Run fn() on a background thread; deliver the result or error on the GUI thread.

    Daemon threads, not QThreadPool: the pool makes the app wait for every running
    task at exit, and some tasks are network calls with long timeouts (connection
    checks, model lists), so quitting could hang for minutes."""
    sig = _Signals()
    if on_done:
        sig.done.connect(on_done)
    if on_fail:
        sig.failed.connect(on_fail)
    _keep.add(sig)  # keep the signals alive until delivered (released on the GUI thread)
    sig.done.connect(lambda *_: _keep.discard(sig))
    sig.failed.connect(lambda *_: _keep.discard(sig))

    def work():
        try:
            result, error = fn(), None
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            result, error = None, str(e)
        try:  # the receiving window may already be gone (e.g. app quitting)
            if error is None:
                sig.done.emit(result)
            else:
                sig.failed.emit(error)
        except RuntimeError:
            pass

    threading.Thread(target=work, daemon=True).start()
