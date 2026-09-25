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
    "omlx": dict(label="本机 oMLX", base_url="http://127.0.0.1:8888", thinking="chat_template_kwargs", concurrency=1),
    "deepseek": dict(label="DeepSeek", base_url="https://api.deepseek.com", thinking="deepseek", concurrency=4),
    "bailian": dict(label="阿里云百炼（通义千问）", base_url="https://dashscope.aliyuncs.com/compatible-mode", thinking="enable_thinking", concurrency=4),
    "openai": dict(label="OpenAI", base_url="https://api.openai.com", thinking="reasoning_effort", concurrency=4),
    "ollama": dict(label="Ollama", base_url="http://127.0.0.1:11434", thinking="none", concurrency=1),
    "lmstudio": dict(label="LM Studio", base_url="http://127.0.0.1:1234", thinking="none", concurrency=1),
    "custom": dict(label="自定义", base_url="", thinking="none", concurrency=2),
}
THINK_LEVELS = ("off", "low", "medium")


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


@dataclass
class Asr:
    endpoint: str = "本机 oMLX"
    model: str = "Qwen3-ASR-1.7B-8bit"
    vad_threshold: float = 0.25
    max_speech_s: float = 8.0
    two_pass: bool = True                # second pass with name/title hints


@dataclass
class Translate:
    endpoint: str = "本机 oMLX"
    model: str = "qwen3.8-27b-4bit"
    think: str = "low"                   # off | low | medium
    think_budget: int = 1024             # max thinking tokens per batch (0 = no cap); 512 breaks the JSON
    brief_think: str = "low"             # thinking level for the one-off brief
    fallback_endpoint: str = ""          # used when the primary rejects content
    fallback_model: str = ""
    batch_size: int = 20
    context_lines: int = 5


@dataclass
class Config:
    general: General = field(default_factory=General)
    asr: Asr = field(default_factory=Asr)
    translate: Translate = field(default_factory=Translate)
    endpoints: List[Endpoint] = field(default_factory=list)
    path: str = ""

    def endpoint(self, name: str) -> Endpoint:
        for e in self.endpoints:
            if e.name == name:
                return e
        raise KeyError(f"没有名为「{name}」的接口配置")

    def find_endpoint(self, name: str) -> Optional[Endpoint]:
        return next((e for e in self.endpoints if e.name == name), None)


def config_path() -> str:
    return os.environ.get("POLYSUB_CONFIG") or os.path.join(
        user_config_dir("PolySub", appauthor=False), "config.toml")


def mask_key(key: str) -> str:
    return f"{key[:3]}…{key[-4:]}" if len(key) > 10 else ("已填写" if key else "未填写")


def _omlx_key() -> str:
    try:
        with open(os.path.expanduser("~/.omlx/settings.json")) as f:
            return json.load(f)["auth"]["api_key"] or ""
    except Exception:
        return ""


def default_config() -> Config:
    ep = []
    for preset in ("omlx", "deepseek", "bailian"):
        p = PRESETS[preset]
        ep.append(Endpoint(name=p["label"], preset=preset, base_url=p["base_url"],
                           api_key=_omlx_key() if preset == "omlx" else "",
                           thinking=p["thinking"], concurrency=p["concurrency"]))
    return Config(endpoints=ep)


def _from_dict(d: dict) -> Config:
    base = default_config()
    cfg = Config(
        general=General(**{**asdict(base.general), **d.get("general", {})}),
        asr=Asr(**{**asdict(base.asr), **d.get("asr", {})}),
        translate=Translate(**{**asdict(base.translate), **d.get("translate", {})}),
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
    for sec in ("general", "asr", "translate"):
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
        cfg = _from_dict(tomllib.load(f))
    cfg.path = path
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
