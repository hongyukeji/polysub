"""video -> subtitles in one or more target languages.

Steps: load audio -> VAD -> detect language (when auto) -> ASR pass 1 ->
brief -> ASR pass 2 (with name hints) -> translate per target language ->
write files. Recognition results and the brief are cached per video, so
another target language or a different translation setting skips them.
"""
import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

from platformdirs import user_cache_dir

from . import subtitle
from .api import AsrClient, ChatClient, Usage
from .asr import Cue, detect_language, speech_segments, transcribe
from .brief import make_brief
from .config import Config
from .media import SR, load_audio
from .translate import Translator

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


def _cache_dir(video: str, cfg: Config) -> str:
    st = os.stat(video)
    key = json.dumps([os.path.abspath(video), st.st_size, int(st.st_mtime), CACHE_VERSION,
                      cfg.asr.endpoint, cfg.asr.model, cfg.asr.vad_threshold, cfg.asr.max_speech_s,
                      cfg.asr.two_pass, cfg.general.source_lang])
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


def run(video: str, cfg: Config, targets: Optional[List[str]] = None,
        progress: Optional[Callable[[Progress], None]] = None,
        cancel: Optional[threading.Event] = None, use_cache: bool = True,
        output: str = "") -> Result:
    cancel = cancel or threading.Event()
    emit = progress or (lambda p: None)
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

    usage = Usage()
    asr_ep = cfg.endpoint(a.endpoint)
    tr_ep = cfg.endpoint(t.endpoint)
    fb = None
    if t.fallback_endpoint:
        fb = ChatClient(cfg.endpoint(t.fallback_endpoint), t.fallback_model or t.model, "off", usage, cancel)
    tr_client = ChatClient(tr_ep, t.model, t.think, usage, cancel, fallback=fb, think_budget=t.think_budget)
    brief_client = ChatClient(tr_ep, t.model, t.brief_think, usage, cancel, fallback=fb)
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
            lang, share = detect_language(asr_client, audio, segs)
            res.notes.append(f"自动识别语言：{lang or '未知'}（{share:.0%} 的样本一致）")

        t0 = clock()
        cues, st1 = transcribe(asr_client, audio, segs, lang,
                               progress=lambda d, n: emit(Progress("asr1", d, n, "第一遍识别")))
        res.seconds["asr1"] = round(clock() - t0, 1)
        _save_json(cache_asr, {"lang": lang, "pass": 1, "cues": [asdict(c) for c in cues]})

        t0 = clock()
        emit(Progress("brief", message="通读全片，生成翻译参考"))
        brief, terms = make_brief(brief_client, cues)
        _save_json(brief_file, {"brief": brief, "terms": terms})
        res.seconds["brief"] = round(clock() - t0, 1)

        if a.two_pass and terms:
            t0 = clock()
            cues, st2 = transcribe(asr_client, audio, segs, lang, prompt=terms,
                                   progress=lambda d, n: emit(Progress("asr2", d, n, f"第二遍识别（提示：{terms}）")))
            res.seconds["asr2"] = round(clock() - t0, 1)
            _save_json(cache_asr, {"lang": lang, "pass": 2, "terms": terms, "cues": [asdict(c) for c in cues]})
        else:
            _save_json(cache_asr, {"lang": lang, "pass": 2, "terms": "", "cues": [asdict(c) for c in cues]})
        del audio

    if not brief:  # cached ASR but brief made with another model
        t0 = clock()
        emit(Progress("brief", message="生成翻译参考"))
        brief, terms = make_brief(brief_client, cues)
        _save_json(brief_file, {"brief": brief, "terms": terms})
        res.seconds["brief"] = round(clock() - t0, 1)

    res.source_lang = lang
    src_lines = [c.text for c in cues]
    for tgt, path in plan.items():
        t0 = clock()
        tr = Translator(tr_client, lang, tgt, brief, t.batch_size, t.context_lines)
        texts = tr.translate(src_lines, progress=lambda d, n, tg=tgt: emit(Progress("translate", d, n, "翻译", tg)))
        if tr.failed_lines:
            res.notes.append(f"{tgt}：{tr.failed_lines} 行翻译失败，保留了原文")
        emit(Progress("write", message=f"写入 {os.path.basename(path)}"))
        subtitle.write(subtitle.build(cues, texts, tgt, g.bilingual), path, g.output_format)
        res.outputs[tgt] = path
        res.seconds[f"translate:{tgt}"] = round(clock() - t0, 1)

    res.usage = usage.summary()
    emit(Progress("done", message="完成"))
    return res
