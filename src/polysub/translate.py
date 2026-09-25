"""Batch subtitle translation over an OpenAI-compatible chat endpoint.

Each batch of numbered lines is sent with the translation brief and the
preceding lines as context; the model answers with a JSON object
{"1": "...", "2": "..."}. Wrong or incomplete answers are retried, then the
missing lines are translated one by one; a line that still fails keeps its
source text. Moderation rejections are handled by the client's fallback.

Quality aids (each can be switched off, see config.Translate): the following
lines as read-only context, a per-language glossary, "continues on the next
line" marks, stricter prompt rules, and cheap output checks whose failing lines
are translated once more (optionally with a thinking model).
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Sequence

from . import langs
from .api import Cancelled, ChatClient

SYSTEM = """You are a professional film subtitle translator. Translate numbered {src} subtitle lines into {tgt}.

Rules:
- Return exactly one translation per input line, with the same numbers. Never merge, split, skip or reorder lines.
- The lines are consecutive fragments of spoken dialogue from speech recognition. Use the brief and the context lines to work out meaning, speakers, pronouns and recognition errors (homophones, wrong kanji).
- Write natural, colloquial subtitles a native {tgt} viewer would expect; keep each speaker's tone and interjections.
- Translate faithfully. Do not soften, censor, summarize or omit anything, including explicit or vulgar content.
- Never copy text from the brief into a subtitle, and add no notes, explanations or quotes around lines.
{style}{careful}
Answer with a single JSON object only, e.g. {{"1": "...", "2": "..."}}."""

CAREFUL = """- Languages like Japanese often omit the subject: work out who speaks to whom from the lines before and after, and state it when {tgt} needs it.
- Check negation, likes and dislikes, and who does what for whom word by word; never flip them.
- Render idioms and set phrases by meaning in natural spoken {tgt}, not word for word.
- Translate every sentence of a line; a line with two sentences needs both.
- If a line is speech-recognition noise that fits no context, output only its tone (e.g. an interjection) or "…"; never invent a fluent sentence.
- Use the glossary renderings exactly whenever a term appears.
- A line ending in "⤵" continues on the next line: translate both so they read as one sentence split at the same place, and do not output the "⤵".
"""

USER = """Brief (background notes, do not translate):
{brief}

{glossary}{context}Translate these lines:
{lines}
{after}"""

CONT = "⤵"
_KANA = re.compile(r"[\u3040-\u30ff]")
_HANGUL = re.compile(r"[\uac00-\ud7af]")
_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
_ITEM = re.compile(r'"(\d+)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _parse(text: str, n: int) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    d = None
    if m:
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            d = None
    if not isinstance(d, dict):  # broken JSON (trailing comma, cut off, stray text): take the items that parse
        d = {}
        for k, v in _ITEM.findall(text):
            try:
                d[k] = json.loads(f'"{v}"')
            except json.JSONDecodeError:
                continue
    out = {}
    for k, v in d.items():
        try:
            i = int(str(k).strip())
        except ValueError:
            continue
        if 1 <= i <= n and isinstance(v, str) and v.strip():
            out[i] = v.replace(CONT, "").strip()
    return out


def problems(src: str, out: str, tgt: str, src_lang: str = "", glossary: Optional[Dict[str, str]] = None) -> List[str]:
    """Cheap checks on one translated line -> reasons it looks wrong (empty = fine)."""
    glossary = glossary or {}
    why = []
    s, o = src.strip(), out.strip()
    if not o:
        return ["empty"]
    if tgt != "ja" and _KANA.search(o) and not _KANA.search(" ".join(glossary.values())):
        why.append("source script left in the translation")
    if tgt != "ko" and _HANGUL.search(o):
        why.append("source script left in the translation")
    if src_lang and not tgt.startswith(src_lang) and len(s) > 3 and o == s:
        why.append("not translated")
    if tgt in langs.CJK:
        allowed = {w.lower() for w in _LATIN_WORD.findall(s + " " + " ".join(glossary.values()))}
        stray = [w for w in _LATIN_WORD.findall(o) if w.lower() not in allowed]
        if stray:
            why.append("contains foreign words: " + ", ".join(stray[:3]))
    src_cjk = src_lang in ("zh", "ja", "ko", "yue")
    if (src_cjk == (tgt in langs.CJK)) and len(s) >= 10 and len(o) < 0.25 * len(s):
        why.append("much shorter than the source (something left out?)")
    for term, want in glossary.items():
        if term and want and term in s and want not in o:
            why.append(f"glossary: {term} should be {want}")
    return list(dict.fromkeys(why))


class Translator:
    def __init__(self, client: ChatClient, src_lang: str, tgt_lang: str, brief: str = "",
                 batch_size: int = 20, context_lines: int = 5, lookahead_lines: int = 0,
                 glossary: Optional[Dict[str, str]] = None, careful: bool = True, check: bool = False,
                 review_client: Optional[ChatClient] = None):
        self.c = client
        self.src, self.tgt = src_lang, tgt_lang
        self.brief = brief.strip() or "(none)"
        self.batch_size, self.context_lines, self.lookahead = batch_size, context_lines, lookahead_lines
        self.glossary = glossary or {}
        self.check = check
        self.review = review_client
        st = langs.style(tgt_lang)
        tname = langs.name(tgt_lang)
        self.system = SYSTEM.format(src=langs.name(src_lang) if src_lang else "source-language", tgt=tname,
                                    style=f"- {st}\n" if st else "",
                                    careful=CAREFUL.format(tgt=tname) if careful else "")
        self.failed_lines = 0
        self.flagged = self.fixed = 0

    def _ask(self, lines: List[str], context: List[tuple], after: Sequence[str] = (), note: str = "",
             client: Optional[ChatClient] = None) -> dict:
        ctx = ""
        if context:
            ctx = "Context (previous lines, already translated — do not translate again):\n" + "\n".join(
                f"- {s}" + (f"  =>  {t}" if t else "") for s, t in context) + "\n\n"
        joined = " ".join(lines) + " " + " ".join(after)
        used = {k: v for k, v in self.glossary.items() if k and k in joined}
        gl = ("Glossary (use these renderings):\n" + "\n".join(f"- {k} → {v}" for k, v in used.items()) + "\n\n") if used else ""
        tail = ("\nFollowing lines (context only, do not translate):\n" + "\n".join(f"- {a}" for a in after)) if after else ""
        if note:
            tail += "\n\nNote: " + note
        payload = json.dumps({str(i + 1): s for i, s in enumerate(lines)}, ensure_ascii=False, indent=0)
        msgs = [{"role": "system", "content": self.system},
                {"role": "user", "content": USER.format(brief=self.brief, glossary=gl, context=ctx, lines=payload,
                                                        after=tail)}]
        return _parse((client or self.c).complete(msgs, max_tokens=8192, json_mode=True), len(lines))

    def _batch(self, lines: List[str], context: List[tuple], after: Sequence[str] = ()) -> List[str]:
        got = {}
        for _ in range(2):
            got.update({k: v for k, v in self._ask(lines, context, after).items() if k not in got})
            if len(got) == len(lines):
                break
        for i in range(1, len(lines) + 1):  # translate what is still missing line by line
            if i not in got:
                one = self._ask([lines[i - 1]], context + [(lines[j - 1], got.get(j, "")) for j in range(max(1, i - 3), i)],
                                lines[i:i + 2])
                if 1 in one:
                    got[i] = one[1]
                else:
                    self.failed_lines += 1
                    got[i] = lines[i - 1].replace(CONT, "").strip()
        out = [got[i] for i in range(1, len(lines) + 1)]
        if self.check:
            out = self._recheck(lines, out, context, after)
        return out

    def _recheck(self, lines, out, context, after) -> List[str]:
        """Translate the lines that fail the checks once more, one by one, telling the model what was wrong."""
        for i, (s, o) in enumerate(zip(lines, out)):
            src = s.replace(CONT, "").strip()
            why = problems(src, o, self.tgt, self.src, self.glossary)
            if not why:
                continue
            self.flagged += 1
            ctx = (context + [(lines[j].replace(CONT, "").strip(), out[j]) for j in range(max(0, i - 3), i)])[-6:]
            nxt = [x.replace(CONT, "").strip() for x in (list(lines[i + 1:]) + list(after))[:2]]
            again = self._ask([s], ctx, nxt, note=f"a previous translation of this line had a problem ({'; '.join(why)}).",
                              client=self.review).get(1, "")
            if again and len(problems(src, again, self.tgt, self.src, self.glossary)) < len(why):
                out[i] = again
                self.fixed += 1
        return out

    def translate(self, lines: List[str], progress: Optional[Callable[[int, int], None]] = None,
                  continues: Optional[Sequence[bool]] = None) -> List[str]:
        if continues:  # mark lines that run on into the next one
            lines = [f"{s} {CONT}" if c else s for s, c in zip(lines, continues)] + list(lines[len(continues):])
        bs = self.batch_size
        batches = [(i, lines[i:i + bs]) for i in range(0, len(lines), bs)]
        out: List[Optional[str]] = [None] * len(lines)
        done = 0
        workers = max(1, self.c.ep.concurrency)
        plain = lambda j: lines[j].replace(CONT, "").strip()  # noqa: E731

        def after(start, n):
            return [plain(j) for j in range(start + n, min(len(lines), start + n + self.lookahead))]

        if workers == 1:
            # sequential: earlier translations become context for the next batch
            for start, chunk in batches:
                ctx = [(plain(j), out[j]) for j in range(max(0, start - self.context_lines), start)]
                out[start:start + len(chunk)] = self._batch(chunk, ctx, after(start, len(chunk)))
                done += 1
                if progress:
                    progress(done, len(batches))
        else:
            def run(b):
                start, chunk = b
                ctx = [(plain(j), "") for j in range(max(0, start - self.context_lines), start)]
                return start, self._batch(chunk, ctx, after(start, len(chunk)))
            with ThreadPoolExecutor(workers) as ex:
                for start, res in ex.map(run, batches):
                    out[start:start + len(res)] = res
                    done += 1
                    if progress:
                        progress(done, len(batches))
        if self.c.cancel.is_set():
            raise Cancelled()
        return out  # type: ignore[return-value]


_END = re.compile(r"[。！？!?.…」』）)♪～〜]$")


def continuation_marks(cues, max_gap: float = 0.5) -> List[bool]:
    """True for a line that does not end a sentence and is followed closely by the next one."""
    marks = []
    for a, b in zip(cues, cues[1:]):
        marks.append(not _END.search(a.text.strip()) and (b.start - a.end) <= max_gap)
    return marks + [False] if cues else []
