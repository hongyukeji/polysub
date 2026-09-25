"""LLM-assisted scoring: a (stronger) model labels sampled lines with the same
labels and categories as the human sheet. For reference only; it does not
replace human annotation. The output is a normal sheet, so annotate.summarize
and annotate.agreement work on it unchanged.
"""
import json
import re
from typing import Callable, Dict, List, Optional, Sequence

from polysub import langs
from polysub.api import ChatClient

import annotate

SYSTEM = """You are a strict reviewer of film subtitle translations from {src} into {tgt}.
For each line you are asked to grade, decide one label:
- "correct": meaning, speaker relations and tone are faithful; reads naturally.
- "minor": meaning is right but wording is awkward, a nuance or tone is slightly off, or a
  harmless filler is dropped; a viewer would not be misled.
- "error": a viewer would misunderstand or notice something wrong.
For "error" give the main category (and optionally more, separated by ";"):
{categories}
Rules:
- Judge meaning in context: use the surrounding lines (both earlier and later) to work out who
  speaks to whom and what omitted subjects refer to.
- The source comes from speech recognition and may itself be wrong. If the source line is
  garbled or meaningless and the translation invents a fluent sentence anyway, that is an
  error of category "asr_garbage". If the source is clearly a mis-recognition of a
  plausible sentence and the translation follows the plausible meaning, do not penalise.
- Names and terms should be consistent across the film.
- Keep the reason short (at most 30 characters), in Simplified Chinese; for errors, include the
  correct meaning.
Answer with one JSON object only: {{"<line number>": {{"label": "...", "category": "...", "reason": "..."}}, ...}}"""

USER = """{brief}Transcript excerpt (line number | source | translation):
{window}

Grade only these lines: {targets}"""


def _parse(text: str) -> Dict[int, dict]:
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
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
            n = int(str(k).strip())
        except ValueError:
            continue
        if isinstance(v, dict):
            try:
                label = annotate.norm_label(str(v.get("label", "")))
                cats = annotate.norm_categories(str(v.get("category", "") or ""))
            except ValueError:
                continue
            if label:
                out[n] = {"label": label, "categories": cats, "reason": str(v.get("reason", "")).strip()}
    return out


class Judge:
    def __init__(self, client: ChatClient, src_lang: str, tgt_lang: str, batch: int = 8,
                 before: int = 4, after: int = 2, max_tokens: int = 16384):
        self.c = client
        self.batch, self.before, self.after, self.max_tokens = batch, before, after, max_tokens
        cats = "\n".join(f'  - "{k}": {v}' for k, v in annotate.CATEGORIES.items())
        self.system = SYSTEM.format(src=langs.name(src_lang) if src_lang in langs.LANGS else src_lang,
                                    tgt=langs.name(tgt_lang), categories=cats)

    def _window(self, lines: Sequence[dict], picks: Sequence[int], refs: Optional[Sequence[str]]) -> str:
        lo = max(1, min(picks) - self.before)
        hi = min(len(lines), max(picks) + self.after)
        rows = []
        for n in range(lo, hi + 1):
            ln = lines[n - 1]
            row = f"{n} | {ln['src'].strip()} | {ln['tr'].strip()}"
            if refs and refs[n - 1]:
                row += f"   (reference subtitle, loose: {refs[n - 1]})"
            rows.append(row)
        return "\n".join(rows)

    def _ask(self, lines, picks, brief, refs) -> Dict[int, dict]:
        b = f"Background notes on the film (may contain mistakes):\n{brief.strip()}\n\n" if brief.strip() else ""
        msgs = [{"role": "system", "content": self.system},
                {"role": "user", "content": USER.format(brief=b, window=self._window(lines, picks, refs),
                                                        targets=", ".join(map(str, picks)))}]
        got = _parse(self.c.complete(msgs, max_tokens=self.max_tokens, json_mode=True))
        return {n: v for n, v in got.items() if n in picks}

    def grade(self, lines: Sequence[dict], picks: Sequence[int], brief: str = "",
              refs: Optional[Sequence[str]] = None,
              progress: Optional[Callable[[int, int], None]] = None) -> Dict[int, dict]:
        picks = sorted(set(p for p in picks if p))
        chunks = [picks[i:i + self.batch] for i in range(0, len(picks), self.batch)]
        out: Dict[int, dict] = {}
        for i, chunk in enumerate(chunks, 1):
            got = self._ask(lines, chunk, brief, refs)
            missing = [n for n in chunk if n not in got]
            if missing:   # one retry for what the model skipped
                got.update(self._ask(lines, missing, brief, refs))
            out.update(got)
            if progress:
                progress(i, len(chunks))
        return out


def to_rows(item: str, lines: Sequence[dict], picks: Sequence[int], grades: Dict[int, dict],
            annotator: str) -> List[dict]:
    rows = annotate.blank_rows(item, lines, [p for p in picks if p], annotator)
    for r in rows:
        g = grades.get(r["line"])
        if g:
            r["label"] = annotate.LABELS[g["label"]]
            r["category"] = ";".join(g["categories"])
            r["note"] = g["reason"]
        else:
            r["note"] = "（评分模型未给出结果）"
    return rows
