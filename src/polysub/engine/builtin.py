"""Glue between the "builtin" endpoint preset and the engine runtime."""
import os
import threading
from typing import List, Optional, Tuple

from .. import system
from ..config import Config, Endpoint
from ..config import Engine as EngineOpts
from . import manifest, runtime

KIND_OF = {"asr": "asr", "translate": "mt"}


def is_builtin(ep: Optional[Endpoint]) -> bool:
    return bool(ep) and ep.preset == "builtin"


def small_memory() -> bool:
    mem = system.total_memory()
    return bool(mem) and mem < 12 * 1024 ** 3


def start(ep: Endpoint, kind: str, model: str, cancel: Optional[threading.Event] = None,
          opts: Optional[EngineOpts] = None) -> str:
    """Make sure the server for this model runs and point the endpoint at it (ep is modified)."""
    o = opts or EngineOpts()
    engine, mmproj = manifest.engine_of(model)
    ep.base_url = runtime.ensure(kind, manifest.resolve(model), parallel=ep.concurrency if kind == "mt" else 1,
                                 ctx=o.ctx_size, gpu_layers=o.gpu_layers, idle=o.idle_minutes * 60,
                                 exclusive=small_memory(), cancel=cancel, engine=engine, mmproj=mmproj)
    return ep.base_url


def missing(cfg: Config) -> List[str]:
    """Built-in models this (effective) config needs that are not on disk yet."""
    out = []
    for sec in (cfg.asr, cfg.translate):
        if is_builtin(cfg.find_endpoint(sec.endpoint)):
            try:
                if not (manifest.is_installed(sec.model) if sec.model in manifest.MODELS
                        else os.path.isfile(manifest.resolve(sec.model))):
                    out.append(sec.model)
            except KeyError:
                out.append(sec.model)
    return list(dict.fromkeys(out))


def wait_for_models(cfg: Config, cancel: threading.Event, on_wait=None, poll: float = 5):
    """Block while a download of the needed models is running; raise when they are missing and nothing downloads."""
    while True:
        need = missing(cfg)
        if not need:
            return
        if not manifest.downloading():
            raise runtime.EngineError("内置引擎的模型还没下载：" + "、".join(need) +
                                      "。请到「模型」页下载，或运行 polysub models download")
        if on_wait:
            on_wait(need)
        if cancel.wait(poll):
            raise runtime.EngineError("已取消")


def check(cfg: Config) -> List[Tuple[Optional[bool], str, str]]:
    """Status rows (ok, name, detail) for the built-in engine parts this config uses."""
    rows = []
    for role, sec in (("语音识别", cfg.asr), ("翻译", cfg.translate)):
        ep = cfg.find_endpoint(sec.endpoint)
        if not is_builtin(ep):
            continue
        kind = KIND_OF["asr" if sec is cfg.asr else "translate"]
        try:
            exe = runtime.binary(kind, manifest.engine_of(sec.model)[0])
            rows.append((True, f"{role}引擎", exe))
        except (runtime.EngineError, KeyError) as e:
            rows.append((False, f"{role}引擎", str(e)))
        try:
            path = manifest.resolve(sec.model)
            have = manifest.is_installed(sec.model) if sec.model in manifest.MODELS else os.path.isfile(path)
            label = manifest.MODELS[sec.model].label if sec.model in manifest.MODELS else path
            rows.append((have, f"{role}模型", label + ("" if have else "：尚未下载")))
        except KeyError as e:
            rows.append((False, f"{role}模型", str(e.args[0])))
    return rows
