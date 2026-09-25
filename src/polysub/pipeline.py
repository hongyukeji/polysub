"""video -> subtitles in one or more target languages.

Steps: load audio -> VAD -> detect language (when auto) -> ASR pass 1 ->
brief -> ASR pass 2 (with name hints; by default only the segments
that contain a hint or a mishearing noted in the brief) -> translate per target language ->
write files. Recognition results and the brief are cached per video, so
another target language or a different translation setting skips them.
"""
import copy
import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

from platformdirs import user_cache_dir

from . import langs, subtitle
from .api import AsrClient, ChatClient, Usage
from .asr import Cue, detect_language, merge_cues, recheck_segments, speech_segments, transcribe
from .brief import make_brief, make_glossary
from .engine import builtin, runtime
from .config import Config
from .media import SR, load_audio
from .translate import Translator, continuation_marks

CACHE_VERSION = 2  # 2: variant-aware echo filter


@dataclass
class Progress:
    stage: str          # audio | vad | detect | asr1 | brief | asr2 | translate | write | done
    done: int = 0
    total: int = 0
    message: str = ""
    lang: str = ""      # target language during translate


@dataclass
class Result:
    video: str
    source_lang: str = ""
    outputs: Dict[str, str] = field(default_factory=dict)   # lang -> path
    skipped: List[str] = field(default_factory=list)        # langs whose file already existed
    seconds: Dict[str, float] = field(default_factory=dict)
    usage: str = ""
    notes: List[str] = field(default_factory=list)
    cache_dir: str = ""                                      # holds sub-<lang>.json for the editor


def _cache_dir(video: str, cfg: Config) -> str:
    st = os.stat(video)
    key = json.dumps([os.path.abspath(video), st.st_size, int(st.st_mtime), CACHE_VERSION,
                      cfg.asr.endpoint, cfg.asr.model, cfg.asr.vad_threshold, cfg.asr.max_speech_s,
                      cfg.asr.two_pass, cfg.asr.second_pass, cfg.general.source_lang])
    d = os.path.join(user_cache_dir("PolySub", appauthor=False), hashlib.sha1(key.encode()).hexdigest()[:16])
    os.makedirs(d, exist_ok=True)
    return d


def _load_json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_json(p, d):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def edit_data_path(cache_dir: str, lang: str) -> str:
    return os.path.join(cache_dir, f"sub-{lang}.json")


def save_edit_data(cache_dir, lang, video, output, source_lang, cues, texts):
    """Line-by-line source + translation, used by the subtitle editor."""
    _save_json(edit_data_path(cache_dir, lang), {
        "video": video, "output": output, "lang": lang, "source_lang": source_lang,
        "lines": [{"start": c.start, "end": c.end, "src": c.text, "tr": t} for c, t in zip(cues, texts)]})


def load_edit_data(cache_dir: str, lang: str):
    return _load_json(edit_data_path(cache_dir, lang)) if cache_dir else None


class _Engines:
    """Endpoints of this run; built-in ones are copies whose server is started right
    before the step that needs it (small-memory machines run one server at a time)."""

    def __init__(self, cfg: Config, cancel: threading.Event):
        a, t = cfg.asr, cfg.translate
        self.cancel, self.opts = cancel, cfg.engine
        self.asr_ep = cfg.endpoint(a.endpoint)
        self.tr_ep = cfg.endpoint(t.endpoint)
        self.fb_ep = cfg.endpoint(t.fallback_endpoint) if t.fallback_endpoint else None
        self.todo = {}   # kind -> [(endpoint copy, model)]
        if builtin.is_builtin(self.asr_ep):
            self.asr_ep = copy.copy(self.asr_ep)
            self.todo["asr"] = [(self.asr_ep, a.model)]
        if builtin.is_builtin(self.tr_ep):
            self.tr_ep = copy.copy(self.tr_ep)
            self.todo["mt"] = [(self.tr_ep, t.model)]
        if builtin.is_builtin(self.fb_ep):
            if "mt" in self.todo:  # one translation server at a time: a built-in fallback needs another backend
                self.fb_ep = None
            else:
                self.fb_ep = copy.copy(self.fb_ep)
                self.todo["mt"] = [(self.fb_ep, t.fallback_model or t.model)]
        self.kinds = list(self.todo)

    def ready(self, kind: str):
        for ep, model in self.todo.get(kind, []):
            builtin.start(ep, kind, model, self.cancel, self.opts)


def make_translator(cfg: Config, client: ChatClient, src: str, tgt: str, brief: str,
                    glossary: Optional[dict] = None, review: Optional[ChatClient] = None) -> Translator:
    t = cfg.translate
    return Translator(client, src, tgt, brief, t.batch_lines(), t.context_lines,
                      lookahead_lines=t.lookahead_lines, glossary=glossary if t.glossary else None,
                      careful=t.careful_prompt, check=t.check_output, review_client=review if t.review else None)


def glossary_path(cache_dir: str, tgt: str, ep_name: str, model: str) -> str:
    return os.path.join(cache_dir, f"glossary-{tgt}-{hashlib.sha1((ep_name + model).encode()).hexdigest()[:8]}.json")


def load_glossary(cache_dir: str, tgt: str) -> dict:
    """Any glossary made for this target (the editor does not know which model made it)."""
    for f in sorted(os.listdir(cache_dir)) if cache_dir and os.path.isdir(cache_dir) else []:
        if f.startswith(f"glossary-{tgt}-"):
            return _load_json(os.path.join(cache_dir, f)) or {}
    return {}


def run(video: str, cfg: Config, targets: Optional[List[str]] = None,
        progress: Optional[Callable[[Progress], None]] = None,
        cancel: Optional[threading.Event] = None, use_cache: bool = True,
        output: str = "") -> Result:
    cancel = cancel or threading.Event()
    emit = progress or (lambda p: None)
    cfg = cfg.effective()
    g, a, t = cfg.general, cfg.asr, cfg.translate
    targets = targets or g.target_langs
    res = Result(video=video)
    clock = time.monotonic

    # which targets still need a file?
    plan = {}
    for lang in targets:
        path = output if (output and len(targets) == 1) else subtitle.output_path(video, lang, g.output_format, g.on_exists)
        if path is None:
            res.skipped.append(lang)
        else:
            plan[lang] = path
    if not plan:
        return res

    engines = _Engines(cfg, cancel)
    with runtime.keepalive(engines.kinds):
        return _run(video, cfg, plan, res, engines, emit, cancel, use_cache, clock)


def _run(video, cfg, plan, res, engines, emit, cancel, use_cache, clock) -> Result:
    g, a, t = cfg.general, cfg.asr, cfg.translate
    usage = Usage()
    asr_ep, tr_ep, fb_ep = engines.asr_ep, engines.tr_ep, engines.fb_ep
    fb = None
    if fb_ep:
        fb = ChatClient(fb_ep, t.fallback_model or t.model, "off", usage, cancel)
    tr_client = ChatClient(tr_ep, t.model, t.think, usage, cancel, fallback=fb, think_budget=t.think_budget)
    brief_client = ChatClient(tr_ep, t.model, t.brief_think, usage, cancel, fallback=fb)
    review_client = ChatClient(tr_ep, t.model, "low", usage, cancel, fallback=fb, think_budget=1024) if t.review else None
    asr_client = AsrClient(asr_ep, a.model, cancel)

    cdir = _cache_dir(video, cfg)
    cache_asr = os.path.join(cdir, "asr.json")
    brief_file = os.path.join(cdir, f"brief-{hashlib.sha1((tr_ep.name + t.model).encode()).hexdigest()[:8]}.json")
    cached = _load_json(cache_asr) if use_cache else None
    cached_brief = _load_json(brief_file) if use_cache else None

    if cached and (not a.two_pass or cached.get("pass") == 2):
        cues = [Cue(**c) for c in cached["cues"]]
        lang = cached["lang"]
        brief = (cached_brief or {}).get("brief", "")
        terms = (cached_brief or {}).get("terms", "")
        res.notes.append("使用缓存的识别结果")
    else:
        t0 = clock()
        emit(Progress("audio", message="读取音频"))
        audio = load_audio(video, g.ffmpeg_path)
        emit(Progress("vad", message="检测说话片段"))
        segs = speech_segments(audio, a.vad_threshold, a.max_speech_s)
        res.seconds["audio+vad"] = round(clock() - t0, 1)

        lang = g.source_lang
        if lang in ("", "auto"):
            emit(Progress("detect", message="识别视频语言"))
            engines.ready("asr")
            lang, share = detect_language(asr_client, audio, segs)
            res.notes.append(f"自动识别语言：{lang or '未知'}（{share:.0%} 的样本一致）")

        t0 = clock()
        engines.ready("asr")
        cues, st1 = transcribe(asr_client, audio, segs, lang,
                               progress=lambda d, n: emit(Progress("asr1", d, n, "第一遍识别")))
        res.seconds["asr1"] = round(clock() - t0, 1)
        _save_json(cache_asr, {"lang": lang, "pass": 1, "cues": [asdict(c) for c in cues]})

        t0 = clock()
        emit(Progress("brief", message="通读全片，生成翻译参考"))
        engines.ready("mt")
        brief, terms = make_brief(brief_client, cues)
        _save_json(brief_file, {"brief": brief, "terms": terms})
        res.seconds["brief"] = round(clock() - t0, 1)

        if a.two_pass and terms:
            t0 = clock()
            engines.ready("asr")
            if a.second_pass == "all":
                cues, st2 = transcribe(asr_client, audio, segs, lang, prompt=terms,
                                       progress=lambda d, n: emit(Progress("asr2", d, n, f"第二遍识别（提示：{terms}）")))
            else:
                pick = recheck_segments(segs, cues, terms, brief)
                res.notes.append(f"第二遍识别：{len(pick)}/{len(segs)} 个片段")
                if pick:
                    again, st2 = transcribe(asr_client, audio, [segs[i] for i in pick], lang, prompt=terms,
                                            progress=lambda d, n: emit(Progress("asr2", d, n, f"第二遍识别（提示：{terms}）")))
                    cues = merge_cues(cues, again)
            res.seconds["asr2"] = round(clock() - t0, 1)
            _save_json(cache_asr, {"lang": lang, "pass": 2, "terms": terms, "cues": [asdict(c) for c in cues]})
        else:
            _save_json(cache_asr, {"lang": lang, "pass": 2, "terms": "", "cues": [asdict(c) for c in cues]})
        del audio

    if not brief:  # cached ASR but brief made with another model
        t0 = clock()
        emit(Progress("brief", message="生成翻译参考"))
        engines.ready("mt")
        brief, terms = make_brief(brief_client, cues)
        _save_json(brief_file, {"brief": brief, "terms": terms})
        res.seconds["brief"] = round(clock() - t0, 1)

    res.source_lang = lang
    res.cache_dir = cdir
    src_lines = [c.text for c in cues]
    marks = continuation_marks(cues) if t.continuation_marks else None
    for tgt, path in plan.items():
        t0 = clock()
        engines.ready("mt")
        glossary = {}
        if t.glossary:
            gfile = glossary_path(cdir, tgt, tr_ep.name, t.model)
            glossary = (_load_json(gfile) if use_cache else None)
            if glossary is None:
                emit(Progress("brief", message=f"整理 {tgt} 术语表", lang=tgt))
                try:
                    glossary = make_glossary(brief_client, brief, terms, lang, langs.name(tgt))
                except Exception as e:  # noqa: BLE001 - optional aid; translate without it
                    if cancel.is_set():
                        raise
                    glossary = {}
                    res.notes.append(f"{tgt}：术语表生成失败（{str(e)[:80]}）")
                _save_json(gfile, glossary)
        tr = make_translator(cfg, tr_client, lang, tgt, brief, glossary, review_client)
        texts = tr.translate(src_lines, progress=lambda d, n, tg=tgt: emit(Progress("translate", d, n, "翻译", tg)),
                             continues=marks)
        if tr.failed_lines:
            res.notes.append(f"{tgt}：{tr.failed_lines} 行翻译失败，保留了原文")
        if tr.flagged:
            res.notes.append(f"{tgt}：检查出 {tr.flagged} 行可疑，重译改好 {tr.fixed} 行")
        emit(Progress("write", message=f"写入 {os.path.basename(path)}"))
        subtitle.write(subtitle.build(cues, texts, tgt, g.bilingual), path, g.output_format)
        save_edit_data(cdir, tgt, video, path, lang, cues, texts)
        res.outputs[tgt] = path
        res.seconds[f"translate:{tgt}"] = round(clock() - t0, 1)

    res.usage = usage.summary()
    emit(Progress("done", message="完成"))
    return res
