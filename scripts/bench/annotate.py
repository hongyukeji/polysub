"""Annotation sheets: fixed-line sampling, CSV read/write, error-rate summary
and agreement between two annotators (human vs LLM, or draft vs review).

Sheet format (UTF-8 CSV, opens in Numbers / Excel; lines starting with "#"
are comments):

  item,line,start,end,src,tr,label,category,note,annotator

- line: 1-based line number in the run's lines.json (fixed by the sample).
- label: 正确 / 小瑕疵 / 错误 (correct / minor / error are accepted too).
- category: for 错误 (optionally 小瑕疵) one or more codes or names from
  CATEGORIES, separated by ";" or "," — the first one is the main category.
- annotator: human / agent-draft / llm:<model>.
"""
import csv
import io
import random
from collections import Counter, OrderedDict
from typing import Dict, List, Optional, Sequence, Tuple

LABELS = OrderedDict([("correct", "正确"), ("minor", "小瑕疵"), ("error", "错误")])
_LABEL_ALIASES = {"正确": "correct", "对": "correct", "ok": "correct", "correct": "correct",
                  "小瑕疵": "minor", "瑕疵": "minor", "minor": "minor",
                  "错误": "error", "错": "error", "error": "error", "wrong": "error"}

# The error table in docs/plans/builtin-engine.md ("现状实测：翻译质量"), plus "other".
CATEGORIES = OrderedDict([
    ("reversed", "意思相反（否定、好恶、授受弄反）"),
    ("subject", "主语、人称弄错（谁对谁做）"),
    ("omission", "漏译（一行两句只译了一句、丢词）"),
    ("foreign", "混入其他语言的词 / 残留原文"),
    ("term", "术语或人名没按参考翻译、前后不一致"),
    ("asr_garbage", "把识别错误的无意义原文硬译"),
    ("literal", "习惯说法直译"),
    ("other", "其他意思错误"),
])
_CAT_ALIASES = {k: k for k in CATEGORIES}
_CAT_ALIASES.update({v.split("（")[0].split(" /")[0]: k for k, v in CATEGORIES.items()})
_CAT_ALIASES.update({"意思相反": "reversed", "主语": "subject", "人称": "subject", "漏译": "omission",
                     "混入其他语言": "foreign", "残留原文": "foreign", "术语": "term", "人名": "term",
                     "无意义原文": "asr_garbage", "硬译": "asr_garbage", "直译": "literal", "其他": "other"})

FIELDS = ["item", "line", "start", "end", "src", "tr", "label", "category", "note", "annotator"]
DRAFT_MARK = "代理起草，待人工复核"


def norm_label(s: str) -> str:
    """-> correct | minor | error | "" (unlabelled). Raises on unknown text."""
    s = (s or "").strip().lower()
    if not s:
        return ""
    if s in _LABEL_ALIASES:
        return _LABEL_ALIASES[s]
    raise ValueError(f"unknown label {s!r} (use 正确 / 小瑕疵 / 错误)")


def norm_categories(s: str) -> List[str]:
    out = []
    for part in (s or "").replace("；", ";").replace(",", ";").split(";"):
        p = part.strip()
        if not p:
            continue
        key = _CAT_ALIASES.get(p) or _CAT_ALIASES.get(p.lower())
        if not key:  # a longer text: the longest alias it contains
            hits = sorted((k for k in _CAT_ALIASES if len(k) >= 2 and k in p), key=len, reverse=True)
            key = _CAT_ALIASES[hits[0]] if hits else None
        if not key:
            raise ValueError(f"unknown category {p!r}; use one of: {', '.join(CATEGORIES)}")
        if key not in out:
            out.append(key)
    return out


def sample_lines(n_lines: int, k: int = 60, seed: int = 0) -> List[int]:
    """k distinct 1-based line numbers, sorted; the same (n_lines, k, seed) always
    gives the same lines (random.Random is stable across Python versions for this)."""
    k = min(k, n_lines)
    return sorted(random.Random(seed).sample(range(1, n_lines + 1), k))


def blank_rows(item: str, lines: Sequence[dict], picks: Sequence[int], annotator: str = "human") -> List[dict]:
    rows = []
    for n in picks:
        ln = lines[n - 1]
        rows.append({"item": item, "line": n, "start": f"{ln['start']:.2f}", "end": f"{ln['end']:.2f}",
                     "src": ln["src"], "tr": ln["tr"], "label": "", "category": "", "note": "",
                     "annotator": annotator})
    return rows


def legend(extra: Sequence[str] = ()) -> List[str]:
    out = list(extra)
    out.append("label: " + " / ".join(LABELS.values()) + "（错误须填 category，可多个，用 ; 分隔，第一个为主类）")
    out.append("category: " + "; ".join(f"{k}={v}" for k, v in CATEGORIES.items()))
    return out


def write_sheet(path: str, rows: Sequence[dict], comments: Sequence[str] = ()) -> None:
    buf = io.StringIO()
    for c in comments:
        buf.write(f"# {c}\n")
    w = csv.DictWriter(buf, FIELDS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:   # BOM so Excel detects UTF-8
        f.write(buf.getvalue())


def read_sheet(path: str) -> List[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        text = "".join(l for l in f if not l.lstrip().startswith("#"))
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        if not (r.get("line") or "").strip():
            continue
        r["line"] = int(r["line"])
        r["label"] = norm_label(r.get("label", ""))
        r["categories"] = norm_categories(r.get("category", ""))
        rows.append(r)
    return rows


def summarize(rows: Sequence[dict]) -> dict:
    """Error rates over labelled rows: total and by main category (errors only)."""
    lab = [r for r in rows if r["label"]]
    n = len(lab)
    c = Counter(r["label"] for r in lab)
    cats = Counter((r["categories"] or ["other"])[0] for r in lab if r["label"] == "error")
    minor_cats = Counter(r["categories"][0] for r in lab if r["label"] == "minor" and r["categories"])
    rate = (lambda x: round(x / n, 4) if n else None)
    return {
        "rows": len(rows), "labelled": n,
        "correct": c["correct"], "minor": c["minor"], "error": c["error"],
        "error_rate": rate(c["error"]), "minor_rate": rate(c["minor"]),
        "by_category": {k: {"errors": cats.get(k, 0), "rate": rate(cats.get(k, 0))} for k in CATEGORIES},
        "minor_by_category": {k: v for k, v in minor_cats.items()},
    }


def summarize_by_item(rows: Sequence[dict]) -> Dict[str, dict]:
    items = OrderedDict()
    for r in rows:
        items.setdefault(r.get("item") or "", []).append(r)
    out = OrderedDict((k, summarize(v)) for k, v in items.items())
    if len(items) > 1:
        out["(all)"] = summarize(rows)
    return out


def _key(r: dict) -> Tuple[str, int]:
    return (r.get("item") or "", r["line"])


def agreement(a_rows: Sequence[dict], b_rows: Sequence[dict]) -> dict:
    """Agreement on rows both annotators labelled (joined on item + line)."""
    b = {_key(r): r for r in b_rows if r["label"]}
    pairs = [(r, b[_key(r)]) for r in a_rows if r["label"] and _key(r) in b]
    n = len(pairs)
    if not n:
        return {"pairs": 0}
    exact = sum(x["label"] == y["label"] for x, y in pairs)
    binary = sum((x["label"] == "error") == (y["label"] == "error") for x, y in pairs)
    both_err = [(x, y) for x, y in pairs if x["label"] == y["label"] == "error"]
    cat_same = sum((x["categories"] or ["other"])[0] == (y["categories"] or ["other"])[0] for x, y in both_err)
    # Cohen's kappa on the three labels
    labels = list(LABELS)
    pa = Counter(x["label"] for x, _ in pairs)
    pb = Counter(y["label"] for _, y in pairs)
    pe = sum(pa[l] * pb[l] for l in labels) / (n * n)
    po = exact / n
    kappa = round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0
    confusion = {x: {y: 0 for y in labels} for x in labels}
    for x, y in pairs:
        confusion[x["label"]][y["label"]] += 1
    return {"pairs": n, "label_agreement": round(po, 4), "error_vs_not_agreement": round(binary / n, 4),
            "kappa": kappa, "both_error": len(both_err),
            "category_agreement": round(cat_same / len(both_err), 4) if both_err else None,
            "confusion": confusion}


def format_summary(summ: Dict[str, dict]) -> str:
    head = "| 素材 | 标注行 | 正确 | 小瑕疵 | 错误 | 错误率 | " + " | ".join(CATEGORIES) + " |"
    lines = [head, "|" + " --- |" * (6 + len(CATEGORIES))]
    for item, s in summ.items():
        er = f"{s['error_rate']:.1%}" if s["error_rate"] is not None else "-"
        cats = " | ".join(str(s["by_category"][k]["errors"]) for k in CATEGORIES)
        lines.append(f"| {item} | {s['labelled']} | {s['correct']} | {s['minor']} | {s['error']} | {er} | {cats} |")
    return "\n".join(lines)


def format_agreement(ag: dict, a_name: str = "A", b_name: str = "B") -> str:
    if not ag.get("pairs"):
        return "没有双方都标注过的行。"
    cat = f"{ag['category_agreement']:.0%}" if ag["category_agreement"] is not None else "-"
    out = [f"{a_name} vs {b_name}：{ag['pairs']} 行；三档一致 {ag['label_agreement']:.1%}，"
           f"错误/非错误一致 {ag['error_vs_not_agreement']:.1%}，kappa {ag['kappa']}；"
           f"双方都判错误 {ag['both_error']} 行，其中主类一致 {cat}",
           "", f"| {a_name} \\ {b_name} | " + " | ".join(LABELS.values()) + " |", "| --- |" + " --- |" * len(LABELS)]
    for x in LABELS:
        out.append(f"| {LABELS[x]} | " + " | ".join(str(ag["confusion"][x][y]) for y in LABELS) + " |")
    return "\n".join(out)


def match_rows(sample: Sequence[dict], lines: Sequence[dict]) -> List[Optional[int]]:
    """Map sample rows onto another run's lines: same line number when the source
    text matches (same recognition result), else the line overlapping most in time."""
    out = []
    for r in sample:
        n = int(r["line"])
        if 1 <= n <= len(lines) and lines[n - 1]["src"].strip() == (r.get("src") or "").strip():
            out.append(n)
            continue
        s, e = float(r["start"]), float(r["end"])
        best, best_ov = None, 0.0
        for i, ln in enumerate(lines, 1):
            ov = min(e, ln["end"]) - max(s, ln["start"])
            if ov > best_ov:
                best, best_ov = i, ov
        out.append(best)
    return out
