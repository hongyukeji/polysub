"""Speech recognition: Silero VAD segments -> per-segment transcription.

Timestamps come from the VAD segments (the OpenAI transcription API returns no
sentence timing for Qwen3-ASR). Pure interjection lines and lines that only
echo the biasing prompt are dropped.
"""
import io
import re
import wave
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import numpy as np

from .api import AsrClient
from .media import SR
from .vad import VadOptions, get_speech_timestamps


@dataclass
class Cue:
    start: float
    end: float
    text: str


# kana / breath-only lines ("あっ", "んん…", "はぁ")
INTERJ = re.compile(r"^[\sあぁいぃうぅえぇおぉんっッーはハふフへヘほホアァイィウゥエェオォン、。…！!？?～〜・]*$")
_PUNCT = re.compile(r"[\s、。，,？?！!…・「」]")


def speech_segments(audio: np.ndarray, threshold: float, max_speech_s: float) -> List[Tuple[int, int]]:
    opts = VadOptions(threshold=threshold, min_speech_duration_ms=300, min_silence_duration_ms=400,
                      speech_pad_ms=200, max_speech_duration_s=max_speech_s)
    return [(c["start"], c["end"]) for c in get_speech_timestamps(audio, opts)]


def _wav(audio: np.ndarray) -> bytes:
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm)
    return b.getvalue()


def detect_language(client: AsrClient, audio: np.ndarray, segs, samples: int = 24) -> Tuple[str, float]:
    """Majority vote over up to `samples` of the longer segments spread across the
    first part of the film. -> (lang code, share of votes)."""
    cand = [s for s in segs if (s[1] - s[0]) >= 1.5 * SR] or list(segs)
    if not cand:
        return "", 0.0
    step = max(1, len(cand) // samples)
    picks = cand[::step][:samples]
    votes = Counter()
    with ThreadPoolExecutor(max(1, client.ep.concurrency)) as ex:
        for text, lang in ex.map(lambda s: client.transcribe(_wav(audio[s[0]:s[1]])), picks):
            if lang and text.strip():
                votes[lang] += 1
    if not votes:
        return "", 0.0
    lang, n = votes.most_common(1)[0]
    return lang, n / sum(votes.values())


def _fold(s: str) -> str:
    """Fold kanji variants (機/机, 長/长 ...) so echoes spelled differently still match."""
    from .subtitle import normalize_script
    return normalize_script(s, "zh-Hans")


def _is_echo(text: str, prompt: str) -> bool:
    """True when the line is (mostly) the biasing prompt read back by the model."""
    terms = [_fold(t) for t in re.split(r"[、,，\s]+", prompt) if t]
    rest = _fold(text)
    hits = 0
    for t in terms:
        if t in rest:
            hits += 1
            rest = rest.replace(t, "")
    rest = _PUNCT.sub("", rest)
    return len(rest) <= 1 or (hits >= 2 and len(rest) <= 0.3 * len(_PUNCT.sub("", text)))


def transcribe(client: AsrClient, audio: np.ndarray, segs, language: str, prompt: str = "",
               progress: Optional[Callable[[int, int], None]] = None) -> Tuple[List[Cue], dict]:
    total, done = len(segs), 0
    out: List[Optional[Cue]] = [None] * total
    stats = {"segments": total, "interjection": 0, "echo": 0, "empty": 0}

    def one(i):
        s, e = segs[i]
        text, _ = client.transcribe(_wav(audio[s:e]), language=language, prompt=prompt)
        return i, text

    with ThreadPoolExecutor(max(1, client.ep.concurrency)) as ex:
        for i, text in ex.map(one, range(total)):
            done += 1
            if progress:
                progress(done, total)
            if not text:
                stats["empty"] += 1
            elif prompt and _is_echo(text, prompt):
                stats["echo"] += 1
            elif INTERJ.match(text):
                stats["interjection"] += 1
            else:
                s, e = segs[i]
                out[i] = Cue(s / SR, e / SR, text)
    return [c for c in out if c], stats
