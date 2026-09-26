"""PolySub configuration (TOML).

Location: $POLYSUB_CONFIG, else the per-user app data dir
(macOS ~/Library/Application Support/PolySub/config.toml,
 Windows %LOCALAPPDATA%\\PolySub\\config.toml). API keys are stored in plain
text by design; the file is written with 0600 permissions.

Every model service is an OpenAI-compatible "endpoint" (base URL + key). The
ASR step uses POST /v1/audio/transcriptions, translation uses
POST /v1/chat/completions. `thinking` tells the client how that vendor turns
model reasoning on/off.
"""
import copy
import json
import os
import tomllib
from dataclasses import asdict, dataclass, field
from typing import List, Optional

import tomlkit
from platformdirs import user_config_dir

# preset -> defaults. thinking styles:
#   chat_template_kwargs  oMLX / vLLM / SGLang (Qwen chat template)
#   enable_thinking       Alibaba Bailian (DashScope compatible mode)
#   deepseek              DeepSeek {"thinking": {"type": ...}}
#   reasoning_effort      OpenAI-style top-level reasoning_effort
#   none                  do not send anything
PRESETS = {
    "builtin": dict(label="内置（本机）", base_url="", thinking="chat_template_kwargs", concurrency=4),
    "omlx": dict(label="本机 oMLX", base_url="http://127.0.0.1:8888", thinking="chat_template_kwargs", concurrency=1),
    "deepseek": dict(label="DeepSeek", base_url="https://api.deepseek.com", thinking="deepseek", concurrency=4),
    "bailian": dict(label="阿里云百炼（通义千问）", base_url="https://dashscope.aliyuncs.com/compatible-mode", thinking="enable_thinking", concurrency=4),
    "openai": dict(label="OpenAI", base_url="https://api.openai.com", thinking="reasoning_effort", concurrency=4),
    "ollama": dict(label="Ollama", base_url="http://127.0.0.1:11434", thinking="none", concurrency=1),
    "lmstudio": dict(label="LM Studio", base_url="http://127.0.0.1:1234", thinking="none", concurrency=1),
    "custom": dict(label="自定义", base_url="", thinking="none", concurrency=2),
}
THINK_LEVELS = ("off", "low", "medium")
# translation quality levels -> (think, think_budget); "mine" switches to the [mine] combination
QUALITY_LEVELS = {"fast": ("off", 0), "standard": ("low", 1024), "fine": ("low", 0)}


def quality_of(cfg: "Config") -> str:
    if cfg.general.use_mine and cfg.mine.configured:
        return "mine"
    t = cfg.translate
    return next((k for k, v in QUALITY_LEVELS.items() if v == (t.think, t.think_budget)), "custom")


def with_quality(cfg: "Config", level: str) -> "Config":
    """Copy of cfg using quality level (fast / standard / fine / mine)."""
    c = copy.deepcopy(cfg)
    if level == "mine":
        c.general.use_mine = True
    elif level in QUALITY_LEVELS:
        c.general.use_mine = False
        c.translate.think, c.translate.think_budget = QUALITY_LEVELS[level]
    return c


@dataclass
class Endpoint:
    name: str
    preset: str = "custom"
    base_url: str = ""
    api_key: str = ""
    thinking: str = "none"
    concurrency: int = 1
    timeout: int = 900

    @property
    def root(self) -> str:
        """Base URL without a trailing /v1 (both spellings are accepted)."""
        u = self.base_url.rstrip("/")
        return u[:-3] if u.endswith("/v1") else u

    @property
    def is_local(self) -> bool:
        return any(h in self.base_url for h in ("127.0.0.1", "localhost", "::1"))


@dataclass
class General:
    source_lang: str = "auto"            # auto = detect once per video
    target_langs: List[str] = field(default_factory=lambda: ["zh-Hans"])
    output_format: str = "srt"           # srt | ass | vtt
    bilingual: bool = False              # target line + source line
    on_exists: str = "skip"              # skip | overwrite | rename
    ffmpeg_path: str = ""                # only used when PyAV cannot read a file
    use_mine: bool = False               # translate with [mine] ("我的模型") instead of [asr] / [translate]
    download_source: str = "auto"        # built-in models: auto | official | mirror
    watch_dir: str = ""                  # new videos appearing here are queued automatically
    config_version: int = 0              # CONFIG_VERSION once migrated (see _migrate)
    models_dir: str = ""                 # built-in models; empty = <app data>/models


@dataclass
class Asr:
    endpoint: str = "本机 oMLX"
    model: str = "Qwen3-ASR-1.7B-8bit"
    vad_threshold: float = 0.25
    max_speech_s: float = 8.0
    two_pass: bool = True                # second pass with name/title hints
    second_pass: str = "auto"            # auto = only segments with name / mishearing candidates | all


@dataclass
class Translate:
    endpoint: str = "本机 oMLX"
    model: str = "qwen3.8-27b-4bit"
    think: str = "off"                   # off | low | medium (new configs default to fast)
    think_budget: int = 0                # max thinking tokens per batch (0 = no cap); 512 breaks the JSON
    brief_think: str = "low"             # thinking level for the one-off brief
    fallback_endpoint: str = ""          # used when the primary rejects content
    fallback_model: str = ""
    batch_size: int = 0                  # lines per batch; 0 = auto: 1 without thinking (in batches models
                                         # moved sentences between lines; parallel requests keep it fast),
                                         # 20 with thinking (thinking is paid per request)
    temperature: float = 0.3             # sampling temperature for translation (servers default to ~0.8)
    context_lines: int = 5               # previous lines (with their translations) sent as context
    # quality aids (Q1); each can be switched off
    lookahead_lines: int = 2             # following lines sent as read-only context (0 = off; more made
                                         # models pull the next lines' content forward)
    glossary: bool = True                # per-language renderings of names / recurring terms
    careful_prompt: bool = True          # stricter rules: subjects, negation, idioms, noise lines
    continuation_marks: bool = False     # mark lines that run on into the next one (off: models merged lines)
    check_output: bool = True            # re-translate lines that fail cheap checks
    review: bool = False                 # ... and use thinking (low) for that second try

    def batch_lines(self) -> int:
        if self.batch_size > 0:
            return self.batch_size
        return 1 if self.think == "off" else 20


@dataclass
class Mine:
    """The user's own high-accuracy combination, switched on from the task page ("我的模型")."""
    asr_endpoint: str = ""
    asr_model: str = ""
    translate_endpoint: str = ""
    translate_model: str = ""
    think: str = "low"
    think_budget: int = 1024

    @property
    def configured(self) -> bool:
        return bool(self.translate_endpoint and self.translate_model)


@dataclass
class Engine:
    """Built-in engine server options (R3)."""
    ctx_size: int = 6144                 # llama-server context per parallel slot (one pool shared by the slots;
                                         # 4 x 6144 holds the brief's excerpts and fits the 16 GB tier)
    gpu_layers: int = 999                # layers offloaded to the GPU (999 = all)
    idle_minutes: int = 10               # stop a server after this long without use


@dataclass
class Config:
    general: General = field(default_factory=General)
    asr: Asr = field(default_factory=Asr)
    translate: Translate = field(default_factory=Translate)
    mine: Mine = field(default_factory=Mine)
    engine: Engine = field(default_factory=Engine)
    endpoints: List[Endpoint] = field(default_factory=list)
    path: str = ""

    def endpoint(self, name: str) -> Endpoint:
        for e in self.endpoints:
            if e.name == name:
                return e
        raise KeyError(f"没有名为「{name}」的接口配置")

    def find_endpoint(self, name: str) -> Optional[Endpoint]:
        return next((e for e in self.endpoints if e.name == name), None)

    def effective(self) -> "Config":
        """The settings a run uses: [mine] replaces the recognition / translation choice when switched on."""
        if not (self.general.use_mine and self.mine.configured):
            return self
        c = copy.deepcopy(self)
        m = c.mine
        if m.asr_endpoint and m.asr_model:
            c.asr.endpoint, c.asr.model = m.asr_endpoint, m.asr_model
        c.translate.endpoint, c.translate.model = m.translate_endpoint, m.translate_model
        c.translate.think, c.translate.think_budget = m.think, m.think_budget
        return c


def config_path() -> str:
    return os.environ.get("POLYSUB_CONFIG") or os.path.join(
        user_config_dir("PolySub", appauthor=False), "config.toml")


def mask_key(key: str) -> str:
    return f"{key[:3]}…{key[-4:]}" if len(key) > 10 else ("已填写" if key else "未填写")


BUILTIN = PRESETS["builtin"]["label"]


def _omlx_key() -> str:
    try:
        with open(os.path.expanduser("~/.omlx/settings.json")) as f:
            return json.load(f)["auth"]["api_key"] or ""
    except Exception:
        return ""


CONFIG_VERSION = 5   # 2: built-in engine for everyone; 3: Qwen3-ASR replaces Whisper turbo;
                     # 4: continuation marks off (measured: they made models shift sentences between lines);
                     # 5: one line per request on the built-in engine, 4 in parallel, 2 look-ahead lines


def default_config(tier: str = "") -> Config:
    """Config for a new install: the built-in engine, models by the memory tier.
    Other endpoints (oMLX, cloud) are there to pick under 我的模型 / advanced settings."""
    ep = []
    for preset in ("builtin", "omlx", "deepseek", "bailian"):
        p = PRESETS[preset]
        ep.append(Endpoint(name=p["label"], preset=preset, base_url=p["base_url"],
                           api_key=_omlx_key() if preset == "omlx" else "",
                           thinking=p["thinking"], concurrency=p["concurrency"]))
    cfg = Config(endpoints=ep)
    cfg.general.config_version = CONFIG_VERSION
    _use_builtin(cfg, tier)
    return cfg


def _use_builtin(cfg: Config, tier: str = "") -> None:
    from .engine import manifest
    cfg.asr.endpoint = cfg.translate.endpoint = BUILTIN
    cfg.asr.model, cfg.translate.model = manifest.TIERS[tier or manifest.recommended_tier()]


def _migrate(cfg: Config) -> bool:
    """Bring an older config up to CONFIG_VERSION; True when something changed.

    v2: the built-in engine becomes the default. A recognition / translation choice
    that pointed elsewhere (typically oMLX) moves to [mine], so 我的模型 on the task
    page switches back to it in one click; the old default quality (low thinking)
    becomes fast, the new default.
    v3: built-in recognition moves from Whisper turbo to Qwen3-ASR."""
    if cfg.general.config_version >= CONFIG_VERSION:
        return False
    if not cfg.find_endpoint(BUILTIN):
        p = PRESETS["builtin"]
        cfg.endpoints.insert(0, Endpoint(name=BUILTIN, preset="builtin", base_url=p["base_url"],
                                         thinking=p["thinking"], concurrency=p["concurrency"]))
    t, m = cfg.translate, cfg.mine
    if cfg.general.config_version < 2 and t.endpoint != BUILTIN:
        if not m.configured:
            m.asr_endpoint, m.asr_model = cfg.asr.endpoint, cfg.asr.model
            m.translate_endpoint, m.translate_model = t.endpoint, t.model
            m.think, m.think_budget = t.think, t.think_budget
        _use_builtin(cfg)
        cfg.general.use_mine = False
        if (t.think, t.think_budget) == ("low", 1024):
            t.think, t.think_budget = QUALITY_LEVELS["fast"]
    if cfg.general.config_version < 3 and cfg.asr.endpoint == BUILTIN and cfg.asr.model == "asr-turbo":
        # v3: Qwen3-ASR (llama-server) recognizes names and titles better than Whisper turbo, and faster
        from .engine import manifest
        cfg.asr.model = manifest.TIERS[manifest.recommended_tier()][0]
    if cfg.general.config_version < 4:
        cfg.translate.continuation_marks = False
    if cfg.general.config_version < 5:
        ep = cfg.find_endpoint(BUILTIN)
        if ep and ep.concurrency == 1:
            ep.concurrency = PRESETS["builtin"]["concurrency"]
        if cfg.translate.lookahead_lines == 5:
            cfg.translate.lookahead_lines = 2
        if cfg.translate.batch_size in (20, 40):   # old defaults written into configs, not a user choice
            cfg.translate.batch_size = 0
    cfg.general.config_version = CONFIG_VERSION
    return True


def _from_dict(d: dict) -> Config:
    base = default_config()
    cfg = Config(
        general=General(**{**asdict(base.general), **d.get("general", {})}),
        asr=Asr(**{**asdict(base.asr), **d.get("asr", {})}),
        translate=Translate(**{**asdict(base.translate), **d.get("translate", {})}),
        mine=Mine(**{**asdict(base.mine), **d.get("mine", {})}),
        engine=Engine(**{**asdict(base.engine), **d.get("engine", {})}),
        endpoints=[Endpoint(**e) for e in d["endpoints"]] if d.get("endpoints") else base.endpoints,
    )
    if isinstance(cfg.general.target_langs, str):
        cfg.general.target_langs = [cfg.general.target_langs]
    return cfg


HEADER = """# PolySub 配置。可以直接编辑，也可以在 PolySub 的设置界面里改。
# 所有模型服务都是 OpenAI 兼容接口（[[endpoints]]），语音识别和翻译各自选用一个。
# thinking（思考开关格式）：chat_template_kwargs（oMLX/vLLM）| enable_thinking（阿里百炼）
#   | deepseek | reasoning_effort（OpenAI）| none
# API Key 以明文保存，本文件权限为仅本人可读写。
"""


def save(cfg: Config, path: str = "") -> str:
    path = path or cfg.path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = tomlkit.document()
    for line in HEADER.strip().splitlines():
        doc.add(tomlkit.comment(line.lstrip("# ")))
    for sec in ("general", "asr", "translate", "mine", "engine"):
        doc[sec] = asdict(getattr(cfg, sec))
    aot = tomlkit.aot()
    for e in cfg.endpoints:
        aot.append(tomlkit.item(asdict(e)))
    doc["endpoints"] = aot
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(tomlkit.dumps(doc))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    cfg.path = path
    return path


def load(path: str = "") -> Config:
    path = path or config_path()
    if not os.path.exists(path):
        cfg = default_config()
        save(cfg, path)
        return cfg
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    cfg = _from_dict(raw)
    cfg.general.config_version = raw.get("general", {}).get("config_version", 0)
    cfg.path = path
    if _migrate(cfg):
        save(cfg, path)
    from .engine import manifest
    manifest.set_models_dir(cfg.general.models_dir)
    return cfg


def with_overrides(cfg: Config, **kw) -> Config:
    """Copy of cfg with command-line overrides (None values are ignored)."""
    c = copy.deepcopy(cfg)
    for key, val in kw.items():
        if val is None:
            continue
        sec, _, attr = key.partition("__")
        setattr(getattr(c, sec), attr, val)
    return c
