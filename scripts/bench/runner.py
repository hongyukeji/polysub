"""Run one configuration on one video (or cached recognition result) and
collect: step timings, tokens, batch JSON failures, per-line checks, and a
side-by-side table with a reference subtitle.

Nothing in polysub is changed: the run goes through pipeline.run (video) or
brief + Translator (cached recognition result) while Recorder wraps
Translator methods for the duration of the run to count JSON failures and
time the translation step.
"""
import contextlib
import copy
import dataclasses
import hashlib
import json
import os
import threading
import time
import tomllib
from typing import Dict, List, Optional, Sequence

import pysubs2

from polysub import config as pconfig, pipeline, subtitle, translate
from polysub.api import ChatClient, Usage
from polysub.asr import Cue
from polysub.brief import make_brief

import fetch
import metrics

STAGE_NAMES = {"audio": "读音轨", "vad": "VAD", "detect": "识别语言", "asr1": "第一遍识别", "brief": "全片参考",
               "asr2": "第二遍识别", "translate": "翻译", "write": "写文件"}


# ---- configuration -------------------------------------------------------

def _cast(old, text: str):
    if isinstance(old, bool):
        return text.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(old, int):
        return int(text)
    if isinstance(old, float):
        return float(text)
    if isinstance(old, list):
        return [x.strip() for x in text.split(",") if x.strip()]
    return text


def apply_overlay(cfg: pconfig.Config, data: dict) -> None:
    for sec in ("general", "asr", "translate"):
        for k, v in (data.get(sec) or {}).items():
            obj = getattr(cfg, sec)
            if not hasattr(obj, k):
                raise KeyError(f"unknown setting {sec}.{k}")
            setattr(obj, k, v)


def load_config(path: str = "", sets: Sequence[str] = ()) -> pconfig.Config:
    """User config (or a full config file), then an optional partial overlay
    file, then --set section.key=value overrides."""
    cfg = pconfig.load()
    if path:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        if data.get("endpoints"):
            cfg = pconfig.load(path)
        else:
            apply_overlay(cfg, data)
    for s in sets:
        key, _, val = s.partition("=")
        sec, _, attr = key.strip().partition(".")
        obj = getattr(cfg, sec, None)
        if obj is None or not hasattr(obj, attr):
            raise KeyError(f"unknown setting {key!r} (use section.key=value, e.g. translate.think=off)")
        setattr(obj, attr, _cast(getattr(obj, attr), val))
    return cfg


def describe_config(cfg: pconfig.Config) -> dict:
    """Settings that matter for a run, without keys or URLs."""
    def ep(name):
        e = cfg.find_endpoint(name)
        return {"name": name, "preset": e.preset, "thinking": e.thinking, "concurrency": e.concurrency} if e else {"name": name}
    return {"asr": dataclasses.asdict(cfg.asr), "translate": dataclasses.asdict(cfg.translate),
            "asr_endpoint": ep(cfg.asr.endpoint), "translate_endpoint": ep(cfg.translate.endpoint)}


# ---- instrumentation -----------------------------------------------------

def _tok(usage: Optional[Usage]) -> List[int]:
    if not usage:
        return [0, 0, 0]
    with usage.lock:
        return [sum(v[i] for v in usage.tokens.values()) for i in range(3)]


class Recorder:
    """Counts batch JSON results and times stages while active (context manager)."""

    def __init__(self, echo: bool = False):
        self.echo = echo
        self.events: List[tuple] = []     # (stage, monotonic time), first emit per stage
        self.batches: List[dict] = []
        self.lock = threading.Lock()
        self.usage: Optional[Usage] = None
        self.tok_at_translate: Optional[List[int]] = None
        self.failed_lines = 0
        self._local = threading.local()

    def mark(self, stage: str) -> None:
        with self.lock:
            if not any(s == stage for s, _ in self.events):
                self.events.append((stage, time.monotonic()))
                if self.echo and stage in STAGE_NAMES:
                    print(f"  [{time.strftime('%H:%M:%S')}] {STAGE_NAMES[stage]}", flush=True)

    def progress(self, p) -> None:
        if p.stage != "translate":   # translate is marked when Translator.translate starts
            self.mark(p.stage)

    def record_batch(self, n: int, asks: List[tuple]) -> None:
        """asks: (lines asked, lines parsed) in call order, as Translator._batch makes them:
        1-2 whole-batch attempts, then one call per still-missing line."""
        first = asks[0] if asks else (n, 0)
        attempts = 1 if first[1] == first[0] else min(2, len(asks))
        whole = asks[:attempts]
        single = asks[attempts:]
        with self.lock:
            self.batches.append({
                "lines": n,
                "first_ok": first[1] == n,
                "first_empty": first[1] == 0,      # nothing parseable: broken / non-JSON answer
                "attempts": attempts,
                "attempt_fail": sum(g < k for k, g in whole),
                "attempt_empty": sum(g == 0 for k, g in whole),
                "single_calls": len(single),
                "single_ok": sum(g == 1 for _, g in single),
            })

    def json_stats(self) -> dict:
        b = self.batches
        nb = len(b)
        att = sum(x["attempts"] for x in b)
        return {
            "batches": nb,
            "batch_first_fail": sum(not x["first_ok"] for x in b),
            "batch_first_fail_rate": round(sum(not x["first_ok"] for x in b) / nb, 4) if nb else None,
            "batch_first_unparseable": sum(x["first_empty"] for x in b),
            "attempts": att,
            "attempt_fail_rate": round(sum(x["attempt_fail"] for x in b) / att, 4) if att else None,
            "line_fallback_calls": sum(x["single_calls"] for x in b),
            "line_fallback_ok": sum(x["single_ok"] for x in b),
            "failed_lines": self.failed_lines,
        }

    def timings(self, end: float) -> Dict[str, float]:
        ev = sorted(self.events, key=lambda e: e[1])
        out = {}
        for i, (stage, t) in enumerate(ev):
            nxt = ev[i + 1][1] if i + 1 < len(ev) else end
            if stage in ("done",):
                continue
            out[stage] = round(nxt - t, 1)
        return out

    @contextlib.contextmanager
    def active(self):
        T = translate.Translator
        orig = (T._ask, T._batch, T.translate, pipeline.Usage)
        rec, local = self, self._local

        def _ask(tr, lines, context):
            got = orig[0](tr, lines, context)
            asks = getattr(local, "asks", None)
            if asks is not None:
                asks.append((len(lines), len(got)))
            return got

        def _batch(tr, lines, context):
            local.asks = []
            try:
                return orig[1](tr, lines, context)
            finally:
                rec.record_batch(len(lines), local.asks)
                local.asks = None

        def _translate(tr, lines, progress=None):
            rec.usage = rec.usage or tr.c.usage
            rec.tok_at_translate = _tok(tr.c.usage)
            rec.mark("translate")
            try:
                return orig[2](tr, lines, progress)
            finally:
                rec.failed_lines += tr.failed_lines
                rec.mark("translate_end")

        class _Usage(Usage):
            def __init__(u, *a, **kw):
                super().__init__(*a, **kw)
                rec.usage = u

        T._ask, T._batch, T.translate, pipeline.Usage = _ask, _batch, _translate, _Usage
        try:
            yield self
        finally:
            T._ask, T._batch, T.translate, pipeline.Usage = orig

    def tokens(self) -> dict:
        total = _tok(self.usage)
        before = self.tok_at_translate or total
        return {"brief": {"in": before[0], "out": before[1], "calls": before[2]},
                "translate": {"in": total[0] - before[0], "out": total[1] - before[1], "calls": total[2] - before[2]}}


# ---- inputs --------------------------------------------------------------

def load_asr(path: str) -> dict:
    """pipeline cache asr.json ({lang, cues}), bench data asr.json (same), or editor
    data sub-<lang>.json ({source_lang, lines}). -> {"lang", "cues": [Cue], "terms"}"""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if "cues" in d:
        cues = [Cue(float(c["start"]), float(c["end"]), c["text"]) for c in d["cues"]]
        return {"lang": d.get("lang", ""), "cues": cues, "terms": d.get("terms", "")}
    if "lines" in d:
        cues = [Cue(float(c["start"]), float(c["end"]), c["src"]) for c in d["lines"]]
        return {"lang": d.get("source_lang", ""), "cues": cues, "terms": ""}
    raise ValueError(f"{path}: neither a recognition cache nor editor data")


def read_reference(path: str) -> List[tuple]:
    subs = pysubs2.load(path, encoding="utf-8")
    return [(e.start / 1000, e.end / 1000, e.plaintext.strip()) for e in subs if e.plaintext.strip()]


# ---- run -----------------------------------------------------------------

def _batch_lines(t) -> int:
    """Lines per batch; config batch_size 0 means auto (S0), older configs have no auto."""
    if hasattr(t, "batch_lines"):
        return t.batch_lines()
    return t.batch_size or (40 if t.think == "off" else 20)


def _brief_cached(client: ChatClient, cues: List[Cue], cfg: pconfig.Config, use_cache: bool) -> tuple:
    t = cfg.translate
    key = hashlib.sha1(json.dumps([[c.text for c in cues], t.endpoint, t.model, t.brief_think],
                                  ensure_ascii=False).encode()).hexdigest()[:16]
    p = os.path.join(fetch.cache_dir("briefs"), key + ".json")
    if use_cache and os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d["brief"], d["terms"], True
    brief, terms = make_brief(client, cues)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"brief": brief, "terms": terms}, f, ensure_ascii=False, indent=1)
    return brief, terms, False


def run_asr_json(asr: dict, cfg: pconfig.Config, target: str, out_dir: str, source_lang: str = "",
                 limit: int = 0, use_cache: bool = True) -> dict:
    """Translation-only run from a recognition result (brief is cached per model)."""
    t = cfg.translate
    cues = asr["cues"][:limit] if limit else asr["cues"]
    lang = source_lang or asr["lang"]
    rec = Recorder(echo=True)
    usage = Usage()
    rec.usage = usage
    tr_ep = cfg.endpoint(t.endpoint)
    fb = None
    if t.fallback_endpoint:
        fb = ChatClient(cfg.endpoint(t.fallback_endpoint), t.fallback_model or t.model, "off", usage)
    tr_client = ChatClient(tr_ep, t.model, t.think, usage, fallback=fb, think_budget=t.think_budget)
    brief_client = ChatClient(tr_ep, t.model, t.brief_think, usage, fallback=fb)
    t_start = time.monotonic()
    with rec.active():
        rec.mark("brief")
        brief, terms, brief_hit = _brief_cached(brief_client, cues, cfg, use_cache)
        tr = translate.Translator(tr_client, lang, target, brief, _batch_lines(t), t.context_lines)
        texts = tr.translate([c.text for c in cues])
    end = time.monotonic()
    out_srt = os.path.join(out_dir, f"output.{target}.srt")
    subtitle.write(subtitle.build(cues, texts, target), out_srt, "srt")
    timings = rec.timings(end)
    timings.pop("translate_end", None)
    if brief_hit:
        timings["brief"] = 0.0
    return {
        "mode": "asr-json", "source_lang": lang, "target": target, "brief": brief, "terms": terms,
        "brief_cached": brief_hit, "asr_cached": True,
        "lines": [{"start": c.start, "end": c.end, "src": c.text, "tr": x} for c, x in zip(cues, texts)],
        "seconds": timings, "wall_s": round(end - t_start, 1), "tokens": rec.tokens(),
        "json": rec.json_stats(), "output": out_srt,
        "asr": {"lang": lang, "terms": asr.get("terms", ""), "cues": [dataclasses.asdict(c) for c in cues]},
    }


def run_video(video: str, cfg: pconfig.Config, target: str, out_dir: str, use_cache: bool = True) -> dict:
    """Full pipeline (recognition is cached per video by PolySub itself)."""
    cfg = copy.deepcopy(cfg)
    rec = Recorder(echo=True)
    out_srt = os.path.join(out_dir, f"output.{target}.{cfg.general.output_format}")
    t_start = time.monotonic()
    with rec.active():
        res = pipeline.run(video, cfg, targets=[target], progress=rec.progress, use_cache=use_cache, output=out_srt)
    end = time.monotonic()
    data = pipeline.load_edit_data(res.cache_dir, target) or {}
    t = cfg.translate
    tr_ep = cfg.endpoint(t.endpoint)
    brief_file = os.path.join(res.cache_dir, f"brief-{hashlib.sha1((tr_ep.name + t.model).encode()).hexdigest()[:8]}.json")
    brief = {}
    if os.path.exists(brief_file):
        with open(brief_file, encoding="utf-8") as f:
            brief = json.load(f)
    asr = {}
    asr_file = os.path.join(res.cache_dir, "asr.json")
    if os.path.exists(asr_file):
        with open(asr_file, encoding="utf-8") as f:
            asr = json.load(f)
    timings = rec.timings(end)
    timings.pop("translate_end", None)
    timings.pop("write", None)
    return {
        "mode": "video", "source_lang": res.source_lang, "target": target,
        "brief": brief.get("brief", ""), "terms": brief.get("terms", ""),
        "asr_cached": "使用缓存的识别结果" in res.notes, "brief_cached": "brief" not in res.seconds,
        "lines": data.get("lines", []), "seconds": timings, "pipeline_seconds": res.seconds,
        "wall_s": round(end - t_start, 1), "tokens": rec.tokens(), "json": rec.json_stats(),
        "output": out_srt, "notes": res.notes, "asr": asr,
    }


# ---- report --------------------------------------------------------------

def analyse(result: dict, reference: str = "") -> dict:
    lines = result["lines"]
    src = [l["src"] for l in lines]
    trs = [l["tr"] for l in lines]
    allow = [x for x in (result.get("terms") or "").replace("、", ",").split(",") if x.strip()]
    flags = metrics.line_flags(result["source_lang"], result["target"], src, trs, allow)
    refs = []
    chrf = None
    if reference and os.path.exists(reference):
        refs = metrics.align(lines, read_reference(reference))
        chrf = metrics.chrf(trs, refs)
    return {"flags": flags, "counts": metrics.count_flags(flags), "refs": refs, "chrf": chrf, "lines": len(lines)}


def _flag_text(f: dict) -> str:
    names = {"residual": "残留原文", "other_lang": "混入外语", "omission": "疑似漏译", "untranslated": "未翻译"}
    return "、".join(names[k] for k, v in f.items() if v)


def _ts(s: float) -> str:
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


def write_outputs(out_dir: str, item: str, result: dict, ana: dict, meta: dict) -> dict:
    import csv
    lines = result["lines"]
    refs = ana["refs"] or [""] * len(lines)
    with open(os.path.join(out_dir, "compare.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["line", "start", "end", "src", "tr", "reference", "flags"])
        for i, (ln, ref, fl) in enumerate(zip(lines, refs, ana["flags"]), 1):
            w.writerow([i, f"{ln['start']:.2f}", f"{ln['end']:.2f}", ln["src"], ln["tr"], ref, _flag_text(fl)])
    esc = lambda s: (s or "").replace("|", "\\|").replace("\n", " ")
    md = [f"# {item}", "", "| # | 时间 | 原文 | 译文 | 参考 | 检查 |", "| --- | --- | --- | --- | --- | --- |"]
    for i, (ln, ref, fl) in enumerate(zip(lines, refs, ana["flags"]), 1):
        md.append(f"| {i} | {_ts(ln['start'])} | {esc(ln['src'])} | {esc(ln['tr'])} | {esc(ref)} | {_flag_text(fl)} |")
    with open(os.path.join(out_dir, "compare.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    with open(os.path.join(out_dir, "lines.json"), "w", encoding="utf-8") as f:
        json.dump({"item": item, "source_lang": result["source_lang"], "target": result["target"],
                   "brief": result.get("brief", ""), "terms": result.get("terms", ""), "lines": lines},
                  f, ensure_ascii=False, indent=1)
    if result.get("asr"):
        with open(os.path.join(out_dir, "asr.json"), "w", encoding="utf-8") as f:
            json.dump(result["asr"], f, ensure_ascii=False, indent=1)
    summary = {
        "item": item, "mode": result["mode"], "source_lang": result["source_lang"], "target": result["target"],
        "lines": ana["lines"], "seconds": result["seconds"], "wall_s": result["wall_s"],
        "asr_cached": result.get("asr_cached"), "brief_cached": result.get("brief_cached"),
        "tokens": result["tokens"], "json": result["json"], "checks": ana["counts"], "chrf": ana["chrf"],
        **meta,
    }
    with open(os.path.join(out_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    return summary


def summary_table(rows: Sequence[dict]) -> str:
    stages = [s for s in STAGE_NAMES if any(s in r["seconds"] for r in rows)]
    head = ["素材", "行数", *[f"{STAGE_NAMES[s]} 秒" for s in stages], "总计 秒", "翻译请求", "翻译输入 token",
            "翻译输出 token", "整批 JSON 首次失败", "逐行补译", "仍失败", "残留原文", "混入外语", "疑似漏译", "chrF"]
    out = ["| " + " | ".join(head) + " |", "|" + " --- |" * len(head)]
    for r in rows:
        j, tk, ck = r["json"], r["tokens"]["translate"], r["checks"]
        fr = f"{j['batch_first_fail']}/{j['batches']}" + (f"（{j['batch_first_fail_rate']:.0%}）" if j["batches"] else "")
        cells = [r["item"], r["lines"], *[r["seconds"].get(s, "-") for s in stages], r["wall_s"], tk["calls"],
                 f"{tk['in']:,}", f"{tk['out']:,}", fr, j["line_fallback_calls"], j["failed_lines"],
                 ck["residual"], ck["other_lang"], ck["omission"], r["chrf"] if r["chrf"] is not None else "-"]
        out.append("| " + " | ".join(str(c) for c in cells) + " |")
    return "\n".join(out)
