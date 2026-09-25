"""Subtitle post-processing and writing (SRT / ASS / VTT via pysubs2)."""
import os
import re
from typing import List, Optional

import pysubs2

from . import langs
from .asr import Cue

_BREAK = re.compile(r"(?<=[，。！？、,.!?;；:：…])\s*")
_cc = {}


def normalize_script(text: str, lang: str) -> str:
    """Unify Chinese script: Japanese kanji / mixed forms -> target script."""
    conv = {"zh-Hans": "t2s", "zh-Hant": "s2tw"}.get(lang)
    if not conv:
        return text
    if conv not in _cc:
        from opencc import OpenCC
        _cc[conv] = OpenCC(conv)
    return _cc[conv].convert(text)


def _limit(lang: str) -> int:
    return 22 if lang in langs.CJK else 42


def _wrap(text: str, limit: int) -> str:
    """Put a long line on two lines, breaking at punctuation (or a space) near the middle."""
    if len(text) <= limit or "\n" in text:
        return text
    mid = len(text) / 2
    punct = [m.end() for m in _BREAK.finditer(text) if 0 < m.end() < len(text)]
    near = [p for p in punct if abs(p - mid) <= len(text) * 0.3]
    if near:  # a sentence/clause boundary reasonably close to the middle
        cut = min(near, key=lambda p: abs(p - mid))
    else:
        spaces = [m.start() for m in re.finditer(r" ", text)]
        if spaces:
            cut = min(spaces, key=lambda p: abs(p - mid))
        elif punct:
            cut = min(punct, key=lambda p: abs(p - mid))
        else:
            return text if len(text) <= limit * 1.3 else text[: int(mid)] + "\n" + text[int(mid):]
    return text[:cut].rstrip() + "\n" + text[cut:].lstrip()


def build(cues: List[Cue], texts: List[str], lang: str, bilingual: bool = False) -> List[Cue]:
    out = []
    lim = _limit(lang)
    for c, t in zip(cues, texts):
        t = normalize_script(t.strip(), lang)
        long = len(t) > lim * 2 and not bilingual
        if long:  # split into two timed cues, time proportional to length
            first, _, second = _wrap(t, lim).partition("\n")
            if second:
                cut = c.start + (c.end - c.start) * len(first) / max(1, len(first) + len(second))
                out.append(Cue(c.start, cut, _wrap(first, lim)))
                out.append(Cue(cut, c.end, _wrap(second, lim)))
                continue
        text = _wrap(t, lim)
        if bilingual:
            text = f"{text}\n{c.text}"
        out.append(Cue(c.start, c.end, text))
    return out


def output_path(video: str, lang: str, fmt: str, on_exists: str) -> Optional[str]:
    """-> path to write, or None when the file exists and on_exists == 'skip'."""
    base = f"{os.path.splitext(video)[0]}.{lang}"
    path = f"{base}.{fmt}"
    if os.path.exists(path):
        if on_exists == "skip":
            return None
        if on_exists == "rename":
            n = 2
            while os.path.exists(f"{base}.{n}.{fmt}"):
                n += 1
            path = f"{base}.{n}.{fmt}"
    return path


def write(cues: List[Cue], path: str, fmt: str = "srt") -> str:
    subs = pysubs2.SSAFile()
    for c in cues:
        subs.append(pysubs2.SSAEvent(start=int(c.start * 1000), end=int(c.end * 1000),
                                     text=c.text.replace("\n", r"\N")))
    if fmt == "ass":
        st = subs.styles["Default"]
        st.fontname, st.fontsize, st.outline, st.shadow = "PingFang SC", 20, 1.5, 0
    subs.save(path, format_=fmt, encoding="utf-8")
    return path
