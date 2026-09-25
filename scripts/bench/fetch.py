"""Download the public evaluation set (manifest.toml) into the user cache.

Files go to <user cache>/polysub-bench/media/, never into the repository.
Every file is checked against the sha256 in the manifest; a manifest entry
without sha256 is downloaded once and its hash printed so it can be pinned.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tomllib
from typing import List, Optional

import requests
from platformdirs import user_cache_dir

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "manifest.toml")
DATA = os.path.join(HERE, "data")
MAX_BYTES = 1 << 30   # refuse anything over 1 GB
UA = {"User-Agent": "polysub-bench/0.1 (https://github.com/hongyukeji/polysub)"}


def cache_dir(*parts) -> str:
    d = os.path.join(os.environ.get("POLYSUB_BENCH_CACHE") or user_cache_dir("polysub-bench", appauthor=False), *parts)
    os.makedirs(d, exist_ok=True)
    return d


def load_manifest(path: str = MANIFEST) -> List[dict]:
    with open(path, "rb") as f:
        return tomllib.load(f)["items"]


def item(item_id: str, path: str = MANIFEST) -> dict:
    for it in load_manifest(path):
        if it["id"] == item_id:
            return it
    raise KeyError(f"no item {item_id!r} in {os.path.basename(path)}")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def download(url: str, dest: str, expect_sha: str = "", expect_size: int = 0) -> str:
    """Resumable download with size cap and sha256 check. -> sha256 of the file."""
    if os.path.exists(dest):
        got = sha256(dest)
        if not expect_sha or got == expect_sha:
            return got
        print(f"  {os.path.basename(dest)}: checksum mismatch, downloading again", file=sys.stderr)
        os.remove(dest)
    if expect_size > MAX_BYTES:
        raise RuntimeError(f"{url}: {expect_size} bytes is over the 1 GB limit")
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    headers = dict(UA, **({"Range": f"bytes={have}-"} if have else {}))
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        if r.status_code == 416:      # already complete
            pass
        else:
            r.raise_for_status()
            if have and r.status_code != 206:
                have = 0              # server ignored Range
            total = have + int(r.headers.get("Content-Length") or 0)
            if total > MAX_BYTES:
                raise RuntimeError(f"{url}: {total} bytes is over the 1 GB limit")
            with open(part, "ab" if have else "wb") as f:
                done = have
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        print(f"\r  {os.path.basename(dest)}: {done / 1e6:.0f} / {total / 1e6:.0f} MB", end="", flush=True)
            print()
    got = sha256(part)
    if expect_sha and got != expect_sha:
        os.remove(part)
        raise RuntimeError(f"{os.path.basename(dest)}: sha256 {got} does not match the manifest ({expect_sha})")
    os.replace(part, dest)
    return got


def clip_path(it: dict, src: str) -> str:
    """The evaluated part of the video: the file itself, or a stream-copied cut."""
    start, end = (it.get("clip") or [0, 0])[:2]
    if not start and not end:
        return src
    out = os.path.join(cache_dir("media"), f"{it['id']}.clip-{start:g}-{end:g}{os.path.splitext(src)[1]}")
    if not os.path.exists(out):
        ff = shutil.which("ffmpeg")
        if not ff:
            raise RuntimeError("cutting a clip needs ffmpeg on PATH")
        cmd = [ff, "-v", "error", "-y", "-ss", str(start)] + (["-to", str(end)] if end else []) + \
              ["-i", src, "-c", "copy", out]
        subprocess.run(cmd, check=True)
    return out


def fetch(it: dict) -> Optional[str]:
    """Download one item (and its reference subtitle). -> path of the evaluated video."""
    dest = os.path.join(cache_dir("media"), it["file"])
    got = download(it["url"], dest, it.get("sha256", ""), it.get("size", 0))
    if not it.get("sha256"):
        print(f"  {it['id']}: sha256 = \"{got}\"  (not pinned in the manifest yet)")
    if it.get("reference"):
        ref = reference_path(it)
        rgot = download(it["reference"], ref, it.get("reference_sha256", ""))
        if not it.get("reference_sha256"):
            print(f"  {it['id']}: reference_sha256 = \"{rgot}\"  (not pinned in the manifest yet)")
    return clip_path(it, dest)


def reference_path(it: dict) -> str:
    return os.path.join(cache_dir("media"), f"{it['id']}.reference{os.path.splitext(it['reference'])[1] or '.srt'}")


def video_path(it: dict) -> Optional[str]:
    """Cached video (clip) if it has been fetched, else None."""
    dest = os.path.join(cache_dir("media"), it["file"])
    return clip_path(it, dest) if os.path.exists(dest) else None
