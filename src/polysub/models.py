"""Model downloads from Hugging Face: single files for the built-in engine
(resumable, sha256-checked, official site or mirror) and whole repos for oMLX."""
import hashlib
import os
import threading
from typing import Callable, List, Optional, Tuple

import requests

DEFAULT_ASR_REPO = "mlx-community/Qwen3-ASR-1.7B-8bit"
HF = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
OFFICIAL, MIRROR = "https://huggingface.co", "https://hf-mirror.com"
SOURCES = {"auto": "自动", "official": "Hugging Face 官方", "mirror": "国内镜像（hf-mirror.com）"}


class Cancelled(RuntimeError):
    pass


def hosts(source: str = "auto") -> List[str]:
    """Download hosts to try, in order. HF_ENDPOINT (if set) wins."""
    if os.environ.get("HF_ENDPOINT"):
        return [HF]
    return {"official": [OFFICIAL], "mirror": [MIRROR]}.get(source, [OFFICIAL, MIRROR])


def file_info(host: str, repo: str, name: str, revision: str = "main") -> Tuple[int, str]:
    """-> (size, sha256) as published by the hub for one file."""
    r = requests.get(f"{host}/api/models/{repo}/tree/{revision}", timeout=30)
    r.raise_for_status()
    for f in r.json():
        if f.get("path") == name:
            lfs = f.get("lfs") or {}
            return int(lfs.get("size") or f.get("size") or 0), lfs.get("oid", "")
    raise FileNotFoundError(f"{repo} 里没有 {name}")


def sha256_of(path: str, cancel: Optional[threading.Event] = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            if cancel is not None and cancel.is_set():
                raise Cancelled("已取消")
            h.update(chunk)
    return h.hexdigest()


def download_file(repo: str, name: str, dest: str, sha256: str = "", revision: str = "main", source: str = "auto",
                  progress: Optional[Callable[[int, int, str], None]] = None,
                  cancel: Optional[threading.Event] = None) -> str:
    """Download one file (resumes a partial .part file), check its sha256, move it into place.
    With source "auto" the official site is tried first, then the mirror."""
    cancel = cancel or threading.Event()
    if os.path.isfile(dest):
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    errors = []
    for host in hosts(source):
        try:
            size, published = file_info(host, repo, name, revision)
            want = (sha256 or published).lower()
            have = os.path.getsize(part) if os.path.exists(part) else 0
            if size and have > size:
                os.remove(part); have = 0
            if not size or have < size:
                headers = {"Range": f"bytes={have}-"} if have else {}
                with requests.get(f"{host}/{repo}/resolve/{revision}/{name}", headers=headers, stream=True,
                                  timeout=60) as r:
                    r.raise_for_status()
                    if have and r.status_code != 206:  # server ignored Range
                        have = 0
                    done = have
                    with open(part, "ab" if have else "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            if cancel.is_set():
                                raise Cancelled("已取消")
                            f.write(chunk)
                            done += len(chunk)
                            if progress:
                                progress(done, size, name)
            if progress:
                progress(size, size, name + "（校验中）")
            got = sha256_of(part, cancel)
            if want and got != want:
                os.remove(part)
                raise ValueError(f"{name} 校验失败（sha256 {got[:12]}…，应为 {want[:12]}…），已删除，请重新下载")
            os.replace(part, dest)
            return dest
        except (Cancelled, ValueError):
            raise
        except (requests.RequestException, FileNotFoundError) as e:
            errors.append(f"{host}: {e}")
    raise RuntimeError("下载失败：" + "；".join(errors))


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
