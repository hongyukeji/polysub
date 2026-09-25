"""Automatic per-line checks on a translated subtitle (no model needed).

- residual source text: characters of a script the source language uses and
  the target does not (kana in Chinese, Han in English...); for a Latin-script
  source, lowercase source words copied verbatim into a non-Latin target.
- other-language words: a script neither language uses (kana in a Chinese
  translation of English), or Latin-letter words in a CJK target that are not
  in the source line, not acronyms and not allowlisted.
- suspected omission: the translation is much shorter than usual for this
  run. Lengths are compared with the run's own median target/source length
  ratio, so no per-language-pair table is needed.
- untranslated: the translation equals the source (the translator keeps the
  source when a line fails).

Also: chrF against a reference subtitle, and time-overlap alignment of the
output with that reference for the side-by-side table.
"""
import re
import statistics
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

SCRIPTS = {
    "han": re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"),
    "kana": re.compile("[\u3041-\u3096\u30a1-\u30fa\u31f0-\u31ff\uff66-\uff9d]"),
    "hangul": re.compile("[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]"),
    "latin": re.compile("[A-Za-z\u00c0-\u024f]"),
    "cyrillic": re.compile("[\u0400-\u04ff]"),
    "thai": re.compile("[\u0e00-\u0e7f]"),
    "arabic": re.compile("[\u0600-\u06ff]"),
    "devanagari": re.compile("[\u0900-\u097f]"),
}
_LANG_SCRIPTS = {
    "zh": {"han"}, "yue": {"han"}, "ja": {"han", "kana"}, "ko": {"hangul"},
    "ru": {"cyrillic"}, "uk": {"cyrillic"}, "th": {"thai"}, "ar": {"arabic"}, "hi": {"devanagari"},
}
CJK_TARGETS = {"zh", "yue", "ja", "ko"}
# Latin-letter tokens that are normal inside CJK subtitles
ALLOW = {"ok", "okay", "app", "wifi", "email", "vlog", "youtuber", "youtube", "ai", "tv", "pc", "dj", "cd", "dvd",
         "ipad", "iphone", "mv", "pk", "vip", "kg", "km", "cm", "mm", "ml", "pm", "am", "vs"}
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*[A-Za-z]|[A-Za-z]")


def base_lang(code: str) -> str:
    return (code or "").split("-")[0].lower()


def scripts_of(lang: str) -> Set[str]:
    return _LANG_SCRIPTS.get(base_lang(lang), {"latin"})


def scripts_in(text: str) -> Set[str]:
    return {k for k, rx in SCRIPTS.items() if rx.search(text or "")}


def latin_words(text: str) -> List[str]:
    return [w for w in _WORD.findall(text or "") if len(w) >= 2]


def _acronym(w: str) -> bool:
    return w.isupper() and len(w) <= 5


def residual_source(src_lang: str, tgt_lang: str, src: str, tr: str, allow: Iterable[str] = ()) -> bool:
    """Source-language text left in the translation."""
    foreign = scripts_of(src_lang) - scripts_of(tgt_lang)
    present = scripts_in(tr)
    if (foreign - {"latin"}) & present:
        return True
    if "latin" in foreign and "latin" in present:
        allow_l = {a.lower() for a in allow} | ALLOW
        src_words = {w.lower() for w in latin_words(src)}
        for w in latin_words(tr):
            # capitalised words are usually names; acronyms are fine
            if w.lower() in src_words and w[0].islower() and w.lower() not in allow_l:
                return True
    return False


def other_language(src_lang: str, tgt_lang: str, src: str, tr: str, allow: Iterable[str] = ()) -> bool:
    """Words of a language that is neither the target nor the source."""
    known = scripts_of(src_lang) | scripts_of(tgt_lang)
    present = scripts_in(tr)
    if (present - known - {"latin"}):
        return True
    if base_lang(tgt_lang) in CJK_TARGETS and "latin" in present:
        allow_l = {a.lower() for a in allow} | ALLOW
        src_words = {w.lower() for w in latin_words(src)}
        for w in latin_words(tr):
            if w.lower() in allow_l or _acronym(w) or w.lower() in src_words:
                continue
            return True
    return False


def text_len(s: str) -> int:
    """Length without spaces and punctuation (characters)."""
    return len(re.sub(r"[\s\W_]+", "", s or "", flags=re.U))


def omission_flags(src: Sequence[str], tr: Sequence[str], factor: float = 0.4, min_src: int = 0) -> List[bool]:
    """True where len(tr)/len(src) < factor * median ratio of the run.
    min_src: ignore short source lines (default: 8 for CJK-like, 20 otherwise)."""
    ratios = [text_len(t) / text_len(s) for s, t in zip(src, tr) if text_len(s) >= 4 and text_len(t) > 0]
    if not ratios:
        return [False] * len(src)
    med = statistics.median(ratios)
    out = []
    for s, t in zip(src, tr):
        ls, lt = text_len(s), text_len(t)
        need = min_src or (8 if scripts_in(s) & {"han", "kana", "hangul", "thai"} else 20)
        out.append(ls >= need and lt / ls < factor * med)
    return out


def line_flags(src_lang: str, tgt_lang: str, src: Sequence[str], tr: Sequence[str],
               allow: Iterable[str] = ()) -> List[Dict[str, bool]]:
    allow = list(allow)
    om = omission_flags(src, tr)
    return [{"residual": residual_source(src_lang, tgt_lang, s, t, allow),
             "other_lang": other_language(src_lang, tgt_lang, s, t, allow),
             "omission": o,
             "untranslated": bool(s.strip()) and s.strip() == t.strip()}
            for s, t, o in zip(src, tr, om)]


def count_flags(flags: List[Dict[str, bool]]) -> Dict[str, int]:
    c = Counter()
    for f in flags:
        for k, v in f.items():
            c[k] += bool(v)
    return {k: c.get(k, 0) for k in ("residual", "other_lang", "omission", "untranslated")}


# ---- reference comparison ------------------------------------------------

def align(lines: Sequence[dict], ref: Sequence[Tuple[float, float, str]], min_overlap: float = 0.2) -> List[str]:
    """For each output line (start/end), the reference cues it overlaps in time."""
    out = []
    j0 = 0
    for ln in lines:
        s, e = ln["start"], ln["end"]
        while j0 < len(ref) and ref[j0][1] <= s - 30:
            j0 += 1
        hits = []
        for rs, re_, text in ref[j0:]:
            if rs > e:
                break
            ov = min(e, re_) - max(s, rs)
            if ov >= min_overlap or (ov > 0 and ov >= 0.5 * (re_ - rs)):
                hits.append(text.replace("\n", " ").strip())
        out.append(" / ".join(hits))
    return out


def _ngrams(s: str, n: int) -> Counter:
    s = re.sub(r"\s+", " ", s.strip())
    return Counter(s[i:i + n] for i in range(len(s) - n + 1))


def chrf(hyp: Sequence[str], ref: Sequence[str], n: int = 6, beta: float = 2.0) -> Optional[float]:
    """Corpus-level chrF (character n-grams, 1..n, recall weighted by beta), 0-100.
    Lines with an empty reference are skipped. None when nothing to compare."""
    prec, rec = [0.0] * n, [0.0] * n
    hp, rp = [0] * n, [0] * n
    any_line = False
    for h, r in zip(hyp, ref):
        if not r.strip():
            continue
        any_line = True
        for k in range(1, n + 1):
            hg, rg = _ngrams(h, k), _ngrams(r, k)
            match = sum((hg & rg).values())
            prec[k - 1] += match; rec[k - 1] += match
            hp[k - 1] += sum(hg.values()); rp[k - 1] += sum(rg.values())
    if not any_line:
        return None
    ps = [prec[i] / hp[i] for i in range(n) if hp[i]]
    rs = [rec[i] / rp[i] for i in range(n) if rp[i]]
    p = sum(ps) / len(ps) if ps else 0.0
    r = sum(rs) / len(rs) if rs else 0.0
    if p + r == 0:
        return 0.0
    return round(100 * (1 + beta ** 2) * p * r / (beta ** 2 * p + r), 1)
