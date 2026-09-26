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
# lines Whisper-style models invent over silence or music (compared without punctuation / case)
HALLUCINATIONS = {
    "ご視聴ありがとうございました", "ご視聴ありがとうございます", "ご視聴いただきありがとうございました",
    "チャンネル登録お願いします", "チャンネル登録よろしくお願いします",
    "谢谢观看", "謝謝觀看", "感谢观看", "感謝觀看", "谢谢大家观看", "请不吝点赞订阅转发打赏支持明镜与点点栏目",
    "thankyouforwatching", "thanksforwatching", "pleasesubscribe", "thankyouverymuchforwatching",
    "시청해주셔서감사합니다", "구독과좋아요부탁드립니다",
}
_HALLU_PREFIX = ("字幕由amara", "subtitlesbytheamara", "中文字幕志愿者", "优优独播剧场", "字幕製作", "字幕制作")


def is_hallucination(text: str) -> bool:
    t = re.sub(r"[\W_]+", "", text).lower()
    return t in HALLUCINATIONS or t.startswith(_HALLU_PREFIX)


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
    stats = {"segments": total, "interjection": 0, "echo": 0, "empty": 0, "hallucination": 0}

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
            elif is_hallucination(text):
                stats["hallucination"] += 1
            else:
                s, e = segs[i]
                out[i] = Cue(s / SR, e / SR, text)
    return [c for c in out if c], stats


_MISHEARD = re.compile(r"([^\s、，,;；：:。「」『』（）()\"'→]{1,12})\s*→")


def mishearings(brief: str) -> List[str]:
    """Wrong spellings from the brief's "wrong→right" notes."""
    return list(dict.fromkeys(_MISHEARD.findall(brief)))


def recheck_segments(segs, cues: List[Cue], terms: str, brief: str = "") -> List[int]:
    """Indices of the segments worth a second, hinted pass: their first-pass text
    contains one of the name / title hints or a mishearing listed in the brief.
    Segments without first-pass text are left alone."""
    keys = [_fold(t) for t in re.split(r"[、,，\s]+", terms) if t] + [_fold(w) for w in mishearings(brief)]
    keys = [k for k in keys if k]
    at = {s: i for i, (s, _) in enumerate(segs)}
    out = []
    for c in cues:
        i = at.get(int(round(c.start * SR)))
        if i is not None and any(k in _fold(c.text) for k in keys):
            out.append(i)
    return out


def _hint_run(text: str, prompt: str) -> bool:
    """The hint list read back inside a line: two or more hint terms in a row, separated only by
    list punctuation (e.g. "田中さん、部長、会議、資料だよね" for "田中さん、今日の会議の資料だよね")."""
    terms = [_fold(t) for t in re.split(r"[、,，\s]+", prompt) if len(t) >= 2]
    if len(terms) < 2:
        return False
    alt = "|".join(re.escape(t) for t in sorted(set(terms), key=len, reverse=True))
    return bool(re.search(rf"(?:{alt})\s*[、,，]\s*(?:{alt})", _fold(text)))


def merge_cues(first: List[Cue], second: List[Cue], prompt: str = "") -> List[Cue]:
    """First-pass cues with the re-recognized ones swapped in (matched by start); a segment the
    second pass dropped, or where it read the hint list back into the line, keeps its first-pass text."""
    by_start = {c.start: c for c in second}
    out = []
    for c in first:
        s = by_start.get(c.start)
        out.append(s if s is not None and not (prompt and _hint_run(s.text, prompt) and not _hint_run(c.text, prompt))
                   else c)
    return out
