"""Download a model repo from Hugging Face (resumable) and make oMLX pick it up."""
import os
import threading
from typing import Callable, Optional

import requests

DEFAULT_ASR_REPO = "mlx-community/Qwen3-ASR-1.7B-8bit"
HF = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")


def repo_files(repo: str):
    r = requests.get(f"{HF}/api/models/{repo}", params={"blobs": "true"}, timeout=30)
    r.raise_for_status()
    return [(s["rfilename"], s.get("size") or 0) for s in r.json().get("siblings", [])]


def download(repo: str, dest: str, progress: Optional[Callable[[int, int, str], None]] = None,
             cancel: Optional[threading.Event] = None) -> str:
    """Download every file of `repo` into `dest` (skips complete files, resumes partial ones)."""
    cancel = cancel or threading.Event()
    files = repo_files(repo)
    total = sum(s for _, s in files)
    done = 0
    os.makedirs(dest, exist_ok=True)
    for name, size in files:
        path = os.path.join(dest, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and (not size or os.path.getsize(path) == size):
            done += size
            continue
        part = path + ".part"
        have = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        with requests.get(f"{HF}/{repo}/resolve/main/{name}", headers=headers, stream=True, timeout=60) as r:
            if r.status_code == 416:  # already complete
                pass
            else:
                r.raise_for_status()
                if have and r.status_code != 206:  # server ignored Range
                    have = 0
                with open(part, "ab" if have else "wb") as f:
                    done += have
                    for chunk in r.iter_content(1 << 20):
                        if cancel.is_set():
                            raise RuntimeError("已取消")
                        f.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total, name)
        os.replace(part, path)
    if progress:
        progress(total, total, "")
    return dest


def omlx_models_dir() -> str:
    return os.path.expanduser("~/.omlx/models")


def omlx_reload(root: str, api_key: str) -> str:
    """Ask a running oMLX to re-discover models (unloads models; pinned ones come back)."""
    s = requests.Session()
    r = s.post(f"{root}/admin/api/login", json={"api_key": api_key, "remember": False}, timeout=15)
    r.raise_for_status()
    r = s.post(f"{root}/admin/api/reload", timeout=300)
    r.raise_for_status()
    return r.json().get("message", "ok")
