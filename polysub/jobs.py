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
PAUSE = os.path.join(STATE, "paused")              # exists -> worker stops after the current job

# rough share of total time per stage (translate is split across target languages)
WEIGHTS = {"audio": 1, "vad": 2, "detect": 2, "asr1": 6, "brief": 20, "asr2": 6, "translate": 60, "write": 1}
_ORDER = list(WEIGHTS)
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
    started: float = 0.0
    finished: float = 0.0
    percent: float = 0.0             # 0-100, written by the worker
    stage: str = ""                  # current step, human readable
    notes: str = ""                  # language detected, fallbacks, timings...
    cache: str = ""                  # pipeline cache dir (editor data)


def _read() -> List[Job]:
    try:
        with open(QUEUE, encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    known = set(Job.__dataclass_fields__)
    return [Job(**{k: v for k, v in j.items() if k in known}) for j in raw]


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
    update(job_id, status="pending", error="", percent=0.0, stage="", notes="")


def remove(job_ids):
    ids = set(job_ids)
    with _locked():
        _write([j for j in _read() if j.id not in ids or j.status == "running"])


def _cancel_flag(job_id: str) -> str:
    return os.path.join(STATE, f"cancel-{job_id}")


def cancel(job_id: str):
    """Pending jobs are cancelled at once; a running job is asked to stop."""
    with _locked():
        jobs = _read()
        for j in jobs:
            if j.id == job_id:
                if j.status == "pending":
                    j.status = "cancelled"
                elif j.status == "running":
                    open(_cancel_flag(job_id), "w").close()
                    j.stage = "正在取消…（当前这一步结束后停止）"
        _write(jobs)


def set_paused(paused: bool):
    os.makedirs(STATE, exist_ok=True)
    if paused:
        open(PAUSE, "w").close()
    elif os.path.exists(PAUSE):
        os.remove(PAUSE)


def is_paused() -> bool:
    return os.path.exists(PAUSE)


def worker_running() -> bool:
    wl = FileLock(WORKER_LOCK)
    try:
        wl.acquire(timeout=0)
    except Timeout:
        return True
    wl.release()
    return False


def percent_of(p, n_targets: int, target_index: int) -> float:
    """Map a pipeline Progress to 0-100 for the whole job."""
    if p.stage == "done":
        return 100.0
    if p.stage not in WEIGHTS:
        return 0.0
    total = sum(WEIGHTS.values())
    before = sum(WEIGHTS[s] for s in _ORDER[:_ORDER.index(p.stage)])
    frac = (p.done / p.total) if p.total else 0.0
    if p.stage == "translate":
        share = WEIGHTS["translate"] / max(1, n_targets)
        return 100.0 * (before + share * (target_index + frac)) / total
    return 100.0 * (before + WEIGHTS[p.stage] * frac) / total


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
        stop_all = cancel or threading.Event()
        while not stop_all.is_set() and not is_paused():
            j = _next()
            if not j:
                break
            c = cfg or load()
            name = os.path.basename(j.video)
            job_cancel = threading.Event()
            last = [0.0, ""]

            def prog(p, j=j):
                if stop_all.is_set() or os.path.exists(_cancel_flag(j.id)):
                    job_cancel.set()
                if on_progress:
                    on_progress(j.id, p)
                now = time.time()
                idx = j.targets.index(p.lang) if p.lang in j.targets else 0
                label = f"{p.message}" + (f" {p.done}/{p.total}" if p.total else "") + (f"（{p.lang}）" if p.lang else "")
                if job_cancel.is_set():
                    label = "正在取消…（当前这一步结束后停止）"
                if p.stage != last[1] or now - last[0] > 0.7:
                    last[0], last[1] = now, p.stage
                    update(j.id, percent=round(percent_of(p, len(j.targets), idx), 1), stage=label)

            watch_stop = threading.Event()

            def watch(j=j):  # notice a cancel request even when no progress is reported
                while not watch_stop.wait(0.5):
                    if stop_all.is_set() or os.path.exists(_cancel_flag(j.id)):
                        job_cancel.set()
                        return

            threading.Thread(target=watch, daemon=True).start()
            log(f"开始 {name} → {','.join(j.targets)}")
            update(j.id, started=time.time(), percent=0.0, stage="准备")
            if notify_user:
                notify("PolySub", f"开始：{name}")
            t0 = time.time()
            try:
                r = pipeline.run(j.video, c, j.targets, cancel=job_cancel, progress=prog)
                status = "done" if r.outputs else "skipped"
                notes = "；".join(r.notes + ([r.usage] if r.usage else []))
                update(j.id, status=status, outputs=r.outputs, cache=r.cache_dir, finished=time.time(), percent=100.0,
                       stage="完成" if r.outputs else "已有字幕，跳过", notes=notes)
                log(f"完成 {name}：{r.outputs or '已有字幕，跳过'} {r.seconds} {notes}")
                if notify_user:
                    notify("PolySub ✓", f"完成（{(time.time() - t0) / 60:.0f} 分钟）：{name}")
            except Exception as e:
                cancelled = job_cancel.is_set()
                update(j.id, status="cancelled" if cancelled else "failed", error="" if cancelled else str(e)[:500],
                       finished=time.time(), stage="已取消" if cancelled else "失败")
                log(f"{'取消' if cancelled else '失败'} {name}：{e}")
                if notify_user and not cancelled:
                    notify("PolySub ✗", f"失败：{name}（{str(e)[:60]}）")
            finally:
                watch_stop.set()
                if os.path.exists(_cancel_flag(j.id)):
                    os.remove(_cancel_flag(j.id))
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
    if getattr(sys, "frozen", False):  # packaged app: the app binary itself understands CLI arguments
        cmd = [sys.executable, "queue", "run", "--quiet"]
    else:
        cmd = [sys.executable, "-m", "polysub", "queue", "run", "--quiet"]
    subprocess.Popen(cmd, **kw)
