"""Platform differences in one place: memory size, processes, executables,
and how to start PolySub itself as a helper process."""
import os
import signal
import subprocess
import sys


def total_memory() -> int:
    """Physical memory in bytes (0 if unknown)."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            return int(out.stdout.strip())
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("tpf", ctypes.c_ulonglong), ("apf", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ave", ctypes.c_ulonglong)]
            m = MS(); m.len = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))  # type: ignore[attr-defined]
            return int(m.total)
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:  # noqa: BLE001
        return 0


def exe(name: str) -> str:
    return name + ".exe" if sys.platform == "win32" else name


def pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate(pid: int):
    if not pid_alive(pid):
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def detached() -> dict:
    """Popen arguments for a process that outlives the one starting it."""
    kw = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return kw


def self_command(*args: str) -> list:
    """Command line that runs the PolySub CLI with args (the packaged app binary understands CLI arguments)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "polysub", *args]


def resource_dir() -> str:
    """Folder with files bundled into the packaged app ('' when running from source)."""
    if not getattr(sys, "frozen", False):
        return ""
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    res = os.path.join(os.path.dirname(os.path.dirname(sys.executable)), "Resources")  # .app/Contents/Resources
    return res if os.path.isdir(os.path.join(res, "engines")) else base
