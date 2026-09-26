"""Translation brief: the translation model reads the whole transcript once and
writes a short note (setting, characters, recurring terms, likely ASR
mishearings) plus 3-6 names/titles used as hints for the second ASR pass."""
import json
import re
from typing import Dict, List, Tuple

from .api import ChatClient
from .asr import Cue

ASK = """下面是一部影片的语音识别（ASR）字幕，可能有同音误识别。请通读后，用简体中文写一份不超过 250 字的「翻译参考」，只包含：
1. 场景与人物关系（一两句）；
2. 人物及其称呼；
3. 反复出现的术语；
4. ASR 同音误识别：逐行检查放在句中不合语境的词（尤其是出现在称呼、人名、专有名词位置上的普通词。同音词很多，要按读音找出语境里真正该用的词；同一个称呼被识别成多种写法时要逐一列出），格式「误→正」，只列有把握的。
最后单独一行输出「ASR_TERMS: 」加上 3～6 个该片反复出现的人名和对人的称呼（例如姓氏、职务称呼；按原语言写法，用顿号分隔；不要普通名词、动词或身体部位词），供语音识别参考。
不要翻译字幕，不要任何其他内容。

字幕：
{text}"""


def clean_terms(raw: str) -> str:
    """Keep only short name-like items; drop anything that reads like a sentence
    (e.g. leaked reasoning) so the second ASR pass never gets garbage hints."""
    out = []
    for t in re.split(r"[、,，/;；]\s*", raw):
        t = t.strip(" 　。.「」\"'")
        if 0 < len(t) <= 12 and not re.search(r"[\s.?!？！:：]", t) and t not in out:
            out.append(t)
    return "、".join(out[:6])


BRIEF_MAX_CHARS = 16000   # fits the built-in engine's context with room for the answer


def excerpt(lines: List[str], limit: int = BRIEF_MAX_CHARS) -> str:
    """The whole transcript when it fits, else evenly spaced runs of consecutive lines from
    beginning to end (the brief needs an overview - setting, people, names - not every line)."""
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    run = 12                                   # consecutive lines per excerpt keep conversations readable
    avg = max(1, len(text) // max(1, len(lines)))
    n_runs = max(1, limit // (avg * run + 8))
    step = max(run, len(lines) // n_runs)
    parts, used = [], 0
    for start in range(0, len(lines), step):
        chunk = "\n".join(lines[start:start + run])
        if used + len(chunk) > limit:
            break
        parts.append(chunk); used += len(chunk) + 5
    return "\n…\n".join(parts)


def make_brief(client: ChatClient, cues: List[Cue]) -> Tuple[str, str]:
    """-> (brief, asr terms)."""
    text = excerpt([c.text for c in cues])
    note = client.complete([{"role": "user", "content": ASK.format(text=text)}], max_tokens=16384)
    found = re.findall(r"ASR_TERMS[:：]\s*(.+)", note)
    terms = clean_terms(found[-1]) if found else ""
    note = re.sub(r"\n?.*ASR_TERMS.*", "", note).strip()
    return note, terms


GLOSSARY = """Below are notes about a film (setting, characters, recurring terms). List the people's names, forms of address and recurring terms that appear in its {src} subtitles, each with the rendering to use in {tgt} subtitles.
Answer with one JSON object {{"term as written in the subtitles": "rendering in {tgt}"}}, at most 30 entries, and nothing else.

Notes:
{brief}

Name hints: {terms}"""


def make_glossary(client: ChatClient, brief: str, terms: str, src: str, tgt: str) -> Dict[str, str]:
    """Source term -> rendering in the target language, for names and recurring terms."""
    if not brief.strip() and not terms.strip():
        return {}
    text = client.complete([{"role": "user", "content": GLOSSARY.format(src=src or "source-language", tgt=tgt,
                                                                        brief=brief, terms=terms or "(none)")}],
                           max_tokens=2048, json_mode=True)
    m = re.search(r"\{.*\}", text, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        d = {}
    out = {}
    for k, v in (d.items() if isinstance(d, dict) else []):
        k, v = str(k).strip(), str(v).strip() if isinstance(v, str) else ""
        if 0 < len(k) <= 20 and 0 < len(v) <= 40:
            out[k] = v
    return dict(list(out.items())[:30])
