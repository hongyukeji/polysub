"""Persistent job queue (JSON file) shared by the CLI, the drop app and the GUI.

One worker processes jobs one at a time (a file lock guarantees a single
worker). Jobs left "running" by a crashed worker are reset to "pending".
"""
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable, List, Optional

from filelock import FileLock, Timeout
from platformdirs import user_data_dir, user_log_dir

from . import pipeline
from .config import Config, load
from .media import is_media

STATE = user_data_dir("PolySub", appauthor=False)
QUEUE = os.path.join(STATE, "queue.json")
LOCK = os.path.join(STATE, "queue.lock")          # guards the JSON file
WORKER_LOCK = os.path.join(STATE, "worker.lock")  # held while a worker runs
LOG = os.path.join(user_log_dir("PolySub", appauthor=False), "PolySub.log")


@dataclass
class Job:
    video: str
    targets: List[str]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: str = "pending"          # pending | running | done | failed | cancelled | skipped
    error: str = ""
    outputs: dict = field(default_factory=dict)
    added: float = field(default_factory=time.time)
    finished: float = 0.0


def _read() -> List[Job]:
    try:
        with open(QUEUE, encoding="utf-8") as f:
            return [Job(**j) for j in json.load(f)]
    except FileNotFoundError:
        return []


def _write(jobs: List[Job]):
    os.makedirs(STATE, exist_ok=True)
    tmp = QUEUE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump([asdict(j) for j in jobs], f, ensure_ascii=False, indent=1)
    os.replace(tmp, QUEUE)


def _locked():
    os.makedirs(STATE, exist_ok=True)
    return FileLock(LOCK, timeout=10)


def list_jobs() -> List[Job]:
    with _locked():
        return _read()


def expand(paths: List[str]) -> List[str]:
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                out += [os.path.join(root, f) for f in sorted(files) if not f.startswith(".") and is_media(f)]
        elif os.path.isfile(p) and is_media(p):
            out.append(os.path.abspath(p))
    return out


def add(paths: List[str], targets: List[str]) -> List[Job]:
    """Add videos (files or folders). Returns the jobs actually added."""
    added = []
    with _locked():
        jobs = _read()
        active = {(j.video, tuple(j.targets)) for j in jobs if j.status in ("pending", "running")}
        for v in expand(paths):
            key = (os.path.abspath(v), tuple(targets))
            if key in active:
                continue
            j = Job(video=key[0], targets=list(targets))
            jobs.append(j); added.append(j); active.add(key)
        _write(jobs)
    return added


def update(job_id: str, **kw):
    with _locked():
        jobs = _read()
        for j in jobs:
            if j.id == job_id:
                for k, v in kw.items():
                    setattr(j, k, v)
        _write(jobs)


def clear(statuses=("done", "failed", "cancelled", "skipped")):
    with _locked():
        _write([j for j in _read() if j.status not in statuses])


def retry(job_id: str):
    update(job_id, status="pending", error="")


def _next() -> Optional[Job]:
    with _locked():
        jobs = _read()
        for j in jobs:
            if j.status == "pending":
                j.status = "running"
                _write(jobs)
                return j
    return None


def notify(title: str, text: str):
    if sys.platform == "darwin":
        t = text.replace('"', "'")
        subprocess.run(["osascript", "-e", f'display notification "{t}" with title "{title}"'],
                       capture_output=True)


def log(msg: str):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")


def work(cfg: Optional[Config] = None, on_progress: Optional[Callable] = None,
         cancel: Optional[threading.Event] = None, notify_user: bool = True) -> bool:
    """Process the queue until empty. Returns False if another worker is running."""
    os.makedirs(STATE, exist_ok=True)
    wl = FileLock(WORKER_LOCK)
    try:
        wl.acquire(timeout=0)
    except Timeout:
        return False
    try:
        with _locked():  # recover jobs from a crashed worker
            jobs = _read()
            for j in jobs:
                if j.status == "running":
                    j.status = "pending"
            _write(jobs)
        cancel = cancel or threading.Event()
        while not cancel.is_set():
            j = _next()
            if not j:
                break
            c = cfg or load()
            name = os.path.basename(j.video)
            log(f"开始 {name} → {','.join(j.targets)}")
            if notify_user:
                notify("PolySub", f"开始：{name}")
            t0 = time.time()
            try:
                r = pipeline.run(j.video, c, j.targets, cancel=cancel,
                                 progress=(lambda p, jid=j.id: on_progress(jid, p)) if on_progress else None)
                status = "done" if r.outputs else "skipped"
                update(j.id, status=status, outputs=r.outputs, finished=time.time())
                log(f"完成 {name}：{r.outputs or '已有字幕，跳过'} {r.seconds} {r.usage} {' '.join(r.notes)}")
                if notify_user:
                    notify("PolySub ✓", f"完成（{(time.time() - t0) / 60:.0f} 分钟）：{name}")
            except Exception as e:
                cancelled = cancel.is_set()
                update(j.id, status="cancelled" if cancelled else "failed", error=str(e)[:500], finished=time.time())
                log(f"{'取消' if cancelled else '失败'} {name}：{e}")
                if notify_user and not cancelled:
                    notify("PolySub ✗", f"失败：{name}（{str(e)[:60]}）")
        return True
    finally:
        wl.release()


def start_background():
    """Spawn a detached worker process (no-op if one is already running)."""
    kw = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen([sys.executable, "-m", "polysub", "queue", "run", "--quiet"], **kw)
