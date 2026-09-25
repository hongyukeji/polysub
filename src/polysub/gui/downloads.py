"""Built-in model downloads shared by the welcome dialog and the Models page:
one download at a time in a background thread, the rest queued."""
import os
import threading

from PySide6.QtCore import QObject, Signal

from .. import models
from ..engine import manifest


class Downloader(QObject):
    progress = Signal(str, object, object)   # model id, bytes done, bytes total
    finished = Signal(str, str)              # model id, error ("" = ok, "cancelled")
    changed = Signal()                       # queue or state changed

    def __init__(self, get_source):
        super().__init__()
        self.get_source = get_source          # -> "auto" | "official" | "mirror"
        self.queue = []
        self.current = ""
        self.done = {}                        # id -> (done, total) of the running download
        self._cancel = threading.Event()
        self._thread = None

    def busy(self) -> bool:
        return bool(self.current or self.queue)

    def state(self, model_id: str) -> str:
        if model_id == self.current:
            return "downloading"
        if model_id in self.queue:
            return "queued"
        m = manifest.remote(model_id)
        return "installed" if m and os.path.isfile(manifest.download_path(m)) else "missing"

    def percent(self, model_id: str) -> int:
        d, t = self.done.get(model_id, (0, 0))
        return int(100 * d / t) if t else 0

    def start(self, ids):
        for i in ids:
            if self.state(i) == "missing":
                self.queue.append(i)
        if self.queue and not (self._thread and self._thread.is_alive()):
            self._cancel = threading.Event()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        self.changed.emit()

    def cancel(self, model_id: str = ""):
        """Cancel one queued / running download, or everything."""
        if model_id and model_id in self.queue:
            self.queue.remove(model_id)
        elif not model_id or model_id == self.current:
            if not model_id:
                self.queue.clear()
            self._cancel.set()
        self.changed.emit()

    def _run(self):
        manifest.set_downloading(True)
        try:
            while self.queue:
                self.current = self.queue.pop(0)
                self.changed.emit()
                m = manifest.remote(self.current)
                err = ""
                try:
                    models.download_file(m.repo, m.file, manifest.download_path(m), m.sha256, m.revision,
                                         self.get_source(), progress=self._progress, cancel=self._cancel)
                except models.Cancelled:
                    err = "cancelled"
                except Exception as e:  # noqa: BLE001
                    err = str(e)
                mid, self.current = self.current, ""
                self.done.pop(mid, None)
                self.finished.emit(mid, err)
                if self._cancel.is_set():
                    self._cancel = threading.Event()
                self.changed.emit()
        finally:
            manifest.set_downloading(False)

    def _progress(self, done, total, name):
        self.done[self.current] = (done, total)
        self.progress.emit(self.current, done, total)
