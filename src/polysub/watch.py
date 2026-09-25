"""Watch folder: videos that newly appear in a folder are queued automatically.

The first scan of a folder only remembers what is already there (nothing old is
queued). A file is taken once it has not changed for `settle` seconds, so a
video that is still being copied is not picked up half-written.
"""
import hashlib
import json
import os
import time
from typing import List

from . import jobs


def _state(folder: str) -> str:
    key = hashlib.sha1(os.path.abspath(folder).encode()).hexdigest()[:12]
    return os.path.join(jobs.STATE, f"watch-{key}.json")


def scan(folder: str, settle: float = 10) -> List[str]:
    """New, settled media files in folder (and subfolders) since the last scan."""
    if not folder or not os.path.isdir(folder):
        return []
    files = jobs.expand([folder])
    path = _state(folder)
    try:
        with open(path, encoding="utf-8") as f:
            seen = set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        seen = None
    now = time.time()
    if seen is None:                       # first look: baseline, queue nothing
        new, seen = [], set(files)
    else:
        new = []
        for p in files:
            if p in seen:
                continue
            try:
                if now - os.path.getmtime(p) >= settle:
                    new.append(p)
            except OSError:
                continue
        seen.update(new)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False)
    return new
