"""Start, reuse and stop the built-in model servers.

One server per kind (asr = whisper-server, mt = llama-server), shared by the
GUI, the background queue and the CLI: its port, PID and model are kept in
<app data>/engines/<kind>.json under a file lock. The server runs under a small
PolySub supervisor process (`polysub engine serve ...`) that stops it after it
has been idle for a while, or when it crashes; callers mark activity with touch().
"""
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Dict, Iterable, List, Optional

import requests
from filelock import FileLock
from platformdirs import user_cache_dir, user_data_dir

from .. import system

BINARIES = {"asr": "whisper-server", "mt": "llama-server"}   # default server program per kind
SERVERS = {"whisper": "whisper-server", "llama": "llama-server"}  # per backend (Qwen3-ASR: asr on llama)
IDLE_SECONDS = 600
START_TIMEOUT = 300   # loading a few GB from a slow disk can take a while


class EngineError(RuntimeError):
    pass


def state_dir() -> str:
    d = os.environ.get("POLYSUB_ENGINE_STATE") or os.path.join(user_data_dir("PolySub", appauthor=False), "engines")
    os.makedirs(d, exist_ok=True)
    return d


def _state_file(kind: str) -> str:
    return os.path.join(state_dir(), f"{kind}.json")


def _lock(kind: str) -> FileLock:
    return FileLock(os.path.join(state_dir(), f"{kind}.lock"), timeout=START_TIMEOUT + 60)


_children: Dict[int, subprocess.Popen] = {}   # supervisors started by this process (reaped here)


def _alive(pid: int) -> bool:
    p = _children.get(pid)
    if p is not None:
        return p.poll() is None   # also reaps it, so it does not linger as a zombie
    return system.pid_alive(pid)


def _kill(pid: int):
    system.terminate(pid)
    p = _children.pop(pid, None)
    if p is not None:
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()


def search_dirs() -> List[str]:
    dirs = [os.environ.get("POLYSUB_ENGINES", "")]
    res = system.resource_dir()
    if res:
        dirs.append(os.path.join(res, "engines"))
    dirs.append(os.path.join(user_cache_dir("PolySub", appauthor=False), "engines"))  # dev: fetch.sh --dev
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    dirs.append(os.path.join(repo, "build", "engines", "bin"))  # dev: built by fetch.sh / build.sh in the checkout
    return [d for d in dirs if d]


def binary(kind: str, engine: str = "") -> str:
    name = system.exe(SERVERS[engine] if engine else BINARIES[kind])
    for d in search_dirs():
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    found = shutil.which(name)
    if found:
        return found
    raise EngineError(f"找不到内置引擎程序 {name}（开发环境可运行 packaging/engines/fetch.sh）")


def read_state(kind: str) -> dict:
    try:
        with open(_state_file(kind), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_state(kind: str, st: dict):
    tmp = _state_file(kind) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, _state_file(kind))


def _clear_state(kind: str, pid: int = 0):
    st = read_state(kind)
    if st and (not pid or st.get("pid") == pid):
        with contextlib.suppress(FileNotFoundError):
            os.remove(_state_file(kind))


def touch(kind: str):
    """Mark the server as in use (the supervisor stops it after IDLE_SECONDS without this)."""
    with contextlib.suppress(FileNotFoundError):
        os.utime(_state_file(kind))


def last_used(kind: str) -> float:
    try:
        return os.path.getmtime(_state_file(kind))
    except FileNotFoundError:
        return 0.0


def url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def healthy(port: int) -> bool:
    try:
        return requests.get(url(port) + "/health", timeout=3).status_code == 200
    except requests.RequestException:
        return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_args(kind: str, model: str, port: int, parallel: int = 1, ctx: int = 8192, gpu_layers: int = 999,
                engine: str = "", mmproj: str = "") -> List[str]:
    if (engine or ("whisper" if kind == "asr" else "llama")) == "whisper":
        threads = max(2, min(8, (os.cpu_count() or 4) // 2))
        return ["-m", model, "--host", "127.0.0.1", "--port", str(port), "-l", "auto", "-t", str(threads),
                "--inference-path", "/v1/audio/transcriptions"]
    parallel = max(1, parallel)
    args = ["-m", model, "--host", "127.0.0.1", "--port", str(port), "--jinja", "-ngl", str(gpu_layers),
            "-np", str(parallel), "-c", str(ctx * parallel)]
    if mmproj:   # audio / vision encoder (e.g. Qwen3-ASR)
        args += ["--mmproj", mmproj]
    return args


def status() -> Dict[str, dict]:
    out = {}
    for kind in BINARIES:
        st = read_state(kind)
        if st and _alive(st.get("pid", 0)):
            out[kind] = dict(st, idle=round(time.time() - last_used(kind)))
    return out


def stop(kind: str):
    with _lock(kind):
        st = read_state(kind)
        if st:
            _kill(st.get("pid", 0))
            _clear_state(kind)


def ensure(kind: str, model: str, parallel: int = 1, ctx: int = 8192, gpu_layers: int = 999,
           idle: Optional[float] = None, exclusive: bool = False, cancel: Optional[threading.Event] = None,
           engine: str = "", mmproj: str = "") -> str:
    """Base URL of a running server for this model, starting one if needed.
    exclusive=True stops the other kind first (small-memory machines)."""
    for f in (model, mmproj):
        if f and not os.path.isfile(f):
            raise EngineError(f"模型文件不存在：{f}（先在「模型」页或用 polysub models download 下载）")
    if exclusive:
        for other in BINARIES:
            if other != kind and read_state(other):
                stop(other)
    with _lock(kind):
        st = read_state(kind)
        if st and _alive(st.get("pid", 0)):
            same = st.get("model") == model and st.get("opts") == [parallel, ctx, gpu_layers, mmproj]
            if same and _wait_healthy(st, cancel, timeout=START_TIMEOUT if not healthy(st["port"]) else 0):
                touch(kind)
                return url(st["port"])
            _kill(st["pid"])
        _clear_state(kind)
        exe = binary(kind, engine)
        port = _free_port()
        idle = idle if idle is not None else float(os.environ.get("POLYSUB_ENGINE_IDLE", IDLE_SECONDS))
        cmd = system.self_command("engine", "serve", "--exe", exe, "--port", str(port), "--idle", str(idle), kind,
                                  "--", *server_args(kind, model, port, parallel, ctx, gpu_layers, engine, mmproj))
        p = subprocess.Popen(cmd, **system.detached())
        _children[p.pid] = p
        st = {"pid": p.pid, "port": port, "model": model, "opts": [parallel, ctx, gpu_layers, mmproj],
              "exe": exe, "started": time.time()}
        _write_state(kind, st)
        if not _wait_healthy(st, cancel, START_TIMEOUT):
            _kill(p.pid)
            _clear_state(kind)
            raise EngineError(f"{os.path.basename(exe)} 没有启动成功（日志：{log_path(kind)}）")
        touch(kind)
        return url(port)


def _wait_healthy(st: dict, cancel: Optional[threading.Event], timeout: float) -> bool:
    end = time.time() + timeout
    while True:
        if healthy(st["port"]):
            return True
        if time.time() >= end or not _alive(st["pid"]) or (cancel is not None and cancel.is_set()):
            return False
        time.sleep(0.5)


def log_path(kind: str) -> str:
    return os.path.join(state_dir(), f"{kind}.log")


@contextlib.contextmanager
def keepalive(kinds: Iterable[str], every: float = 30):
    """Touch the servers of these kinds while the block runs (a long translation is not 'idle')."""
    kinds = list(kinds)
    stop_ev = threading.Event()

    def loop():
        while not stop_ev.wait(every):
            for k in kinds:
                touch(k)
    t = threading.Thread(target=loop, daemon=True)
    if kinds:
        t.start()
    try:
        yield
    finally:
        stop_ev.set()
        for k in kinds:
            touch(k)


def serve(kind: str, exe: str, port: int, args: List[str], idle: Optional[float] = None, poll: float = 0) -> int:
    """Supervisor loop (runs as `polysub engine serve`): run the server, stop it when idle or orphaned."""
    idle = idle if idle is not None else float(os.environ.get("POLYSUB_ENGINE_IDLE", IDLE_SECONDS))
    poll = poll or min(5.0, max(0.2, idle / 4))
    me = os.getpid()
    with open(log_path(kind), "ab") as log:
        child = subprocess.Popen([exe, *args], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        try:
            while True:
                try:
                    code = child.wait(timeout=poll)
                    _clear_state(kind, me)
                    return code
                except subprocess.TimeoutExpired:
                    pass
                st = read_state(kind)
                if st.get("pid") != me:          # replaced or stopped by someone else
                    break
                if time.time() - last_used(kind) > idle:
                    _clear_state(kind, me)
                    break
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
    return 0


def _install_sigterm():
    """Turn SIGTERM into SystemExit so serve() stops its child on the way out."""
    if sys.platform != "win32":
        import signal
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
