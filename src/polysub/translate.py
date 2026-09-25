"""Batch subtitle translation over an OpenAI-compatible chat endpoint.

Each batch of numbered lines is sent with the translation brief and the
preceding lines as context; the model answers with a JSON object
{"1": "...", "2": "..."}. Wrong or incomplete answers are retried, then the
missing lines are translated one by one; a line that still fails keeps its
source text. Moderation rejections are handled by the client's fallback.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional

from . import langs
from .api import Cancelled, ChatClient

SYSTEM = """You are a professional film subtitle translator. Translate numbered {src} subtitle lines into {tgt}.

Rules:
- Return exactly one translation per input line, with the same numbers. Never merge, split, skip or reorder lines.
- The lines are consecutive fragments of spoken dialogue from speech recognition. Use the brief and the context lines to work out meaning, speakers, pronouns and recognition errors (homophones, wrong kanji).
- Write natural, colloquial subtitles a native {tgt} viewer would expect; keep each speaker's tone and interjections.
- Translate faithfully. Do not soften, censor, summarize or omit anything, including explicit or vulgar content.
- Never copy text from the brief into a subtitle, and add no notes, explanations or quotes around lines.
{style}
Answer with a single JSON object only, e.g. {{"1": "...", "2": "..."}}."""

USER = """Brief (background notes, do not translate):
{brief}

{context}Translate these lines:
{lines}"""


def _parse(text: str, n: int) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    out = {}
    for k, v in d.items():
        try:
            i = int(str(k).strip())
        except ValueError:
            continue
        if 1 <= i <= n and isinstance(v, str) and v.strip():
            out[i] = v.strip()
    return out


class Translator:
    def __init__(self, client: ChatClient, src_lang: str, tgt_lang: str, brief: str = "",
                 batch_size: int = 20, context_lines: int = 5):
        self.c = client
        self.src, self.tgt = src_lang, tgt_lang
        self.brief = brief.strip() or "(none)"
        self.batch_size, self.context_lines = batch_size, context_lines
        st = langs.style(tgt_lang)
        self.system = SYSTEM.format(src=langs.name(src_lang) if src_lang else "source-language",
                                    tgt=langs.name(tgt_lang), style=f"- {st}\n" if st else "")
        self.failed_lines = 0

    def _ask(self, lines: List[str], context: List[tuple]) -> dict:
        ctx = ""
        if context:
            ctx = "Context (previous lines, already translated — do not translate again):\n" + "\n".join(
                f"- {s}" + (f"  =>  {t}" if t else "") for s, t in context) + "\n\n"
        payload = json.dumps({str(i + 1): s for i, s in enumerate(lines)}, ensure_ascii=False, indent=0)
        msgs = [{"role": "system", "content": self.system},
                {"role": "user", "content": USER.format(brief=self.brief, context=ctx, lines=payload)}]
        return _parse(self.c.complete(msgs, max_tokens=8192, json_mode=True), len(lines))

    def _batch(self, lines: List[str], context: List[tuple]) -> List[str]:
        got = {}
        for _ in range(2):
            got.update({k: v for k, v in self._ask(lines, context).items() if k not in got})
            if len(got) == len(lines):
                break
        for i in range(1, len(lines) + 1):  # translate what is still missing line by line
            if i not in got:
                one = self._ask([lines[i - 1]], context + [(lines[j - 1], got.get(j, "")) for j in range(max(1, i - 3), i)])
                if 1 in one:
                    got[i] = one[1]
                else:
                    self.failed_lines += 1
                    got[i] = lines[i - 1]
        return [got[i] for i in range(1, len(lines) + 1)]

    def translate(self, lines: List[str], progress: Optional[Callable[[int, int], None]] = None) -> List[str]:
        bs = self.batch_size
        batches = [(i, lines[i:i + bs]) for i in range(0, len(lines), bs)]
        out: List[Optional[str]] = [None] * len(lines)
        done = 0
        workers = max(1, self.c.ep.concurrency)
        if workers == 1:
            # sequential: earlier translations become context for the next batch
            for start, chunk in batches:
                ctx = [(lines[j], out[j]) for j in range(max(0, start - self.context_lines), start)]
                out[start:start + len(chunk)] = self._batch(chunk, ctx)
                done += 1
                if progress:
                    progress(done, len(batches))
        else:
            def run(b):
                start, chunk = b
                ctx = [(lines[j], "") for j in range(max(0, start - self.context_lines), start)]
                return start, self._batch(chunk, ctx)
            with ThreadPoolExecutor(workers) as ex:
                for start, res in ex.map(run, batches):
                    out[start:start + len(res)] = res
                    done += 1
                    if progress:
                        progress(done, len(batches))
        if self.c.cancel.is_set():
            raise Cancelled()
        return out  # type: ignore[return-value]
