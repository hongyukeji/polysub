"""Built-in models: what can be downloaded, how big it is, and the tiers.

The candidates are provisional until R0 (docs/plans/builtin-engine.md) settles
them with measurements. `sha256` may be left empty: the download then checks
the file against the sha256 Hugging Face publishes for it (LFS object id).
"""
import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from platformdirs import user_data_dir

from .. import system


@dataclass(frozen=True)
class Model:
    id: str
    kind: str            # asr | mt
    label: str
    repo: str            # Hugging Face repo
    file: str            # file in the repo
    size: int            # bytes, approximate (shown before downloading)
    license: str
    sha256: str = ""
    revision: str = "main"


MODELS: Dict[str, Model] = {m.id: m for m in (
    Model("asr-turbo", "asr", "Whisper large-v3-turbo（Q5）", "ggerganov/whisper.cpp",
          "ggml-large-v3-turbo-q5_0.bin", 574_000_000, "MIT"),
    Model("mt-1.7b", "mt", "Qwen3 1.7B（Q8）", "Qwen/Qwen3-1.7B-GGUF", "Qwen3-1.7B-Q8_0.gguf",
          1_830_000_000, "Apache-2.0"),
    Model("mt-4b", "mt", "Qwen3 4B（Q4_K_M）", "Qwen/Qwen3-4B-GGUF", "Qwen3-4B-Q4_K_M.gguf",
          2_500_000_000, "Apache-2.0"),
)}

# tier -> (speech model, translation model)
TIERS: Dict[str, Tuple[str, str]] = {
    "light": ("asr-turbo", "mt-1.7b"),
    "standard": ("asr-turbo", "mt-4b"),
}
TIER_LABELS = {"light": "轻量（8 GB 内存）", "standard": "标准（16 GB 及以上，推荐）"}


def recommended_tier(memory: Optional[int] = None) -> str:
    memory = system.total_memory() if memory is None else memory
    return "light" if memory and memory < 12 * 1024 ** 3 else "standard"


def models_dir() -> str:
    return os.environ.get("POLYSUB_MODELS") or os.path.join(user_data_dir("PolySub", appauthor=False), "models")


def path_of(model_id: str) -> str:
    return os.path.join(models_dir(), MODELS[model_id].file)


def is_installed(model_id: str) -> bool:
    p = path_of(model_id)
    return os.path.isfile(p) and os.path.getsize(p) > 0


def remote(model: str) -> Optional[Model]:
    """Downloadable model for a config value: a manifest id, or "hf:owner/repo/file" for any file on the hub."""
    if model in MODELS:
        return MODELS[model]
    if model.startswith("hf:") and model.count("/") >= 2:
        owner, repo, file = model[3:].split("/", 2)
        return Model(model, "mt" if file.endswith(".gguf") else "asr", file, f"{owner}/{repo}", file, 0, "")
    return None


def resolve(model: str) -> str:
    """Model name from the config -> file path: a manifest id, "hf:owner/repo/file", or a path to the user's own file."""
    if model in MODELS:
        return path_of(model)
    if model.startswith("hf:"):
        m = remote(model)
        if not m:
            raise KeyError(f"写法应为 hf:用户/仓库/文件名，而不是「{model}」")
        return os.path.join(models_dir(), m.repo.replace("/", "__"), m.file)
    p = os.path.expanduser(model)
    if os.path.isabs(p):
        return p
    raise KeyError(f"内置引擎没有模型「{model}」：请用 {', '.join(MODELS)}，或填本机模型文件的完整路径")


def download_path(m: Model) -> str:
    return path_of(m.id) if m.id in MODELS else resolve(m.id)


def _marker() -> str:
    return os.path.join(models_dir(), ".downloading")


def set_downloading(on: bool):
    """Tell the queue worker that a download is running (it waits instead of failing)."""
    os.makedirs(models_dir(), exist_ok=True)
    if on:
        with open(_marker(), "w") as f:
            f.write(str(os.getpid()))
    elif os.path.exists(_marker()):
        os.remove(_marker())


def downloading() -> bool:
    try:
        with open(_marker()) as f:
            return system.pid_alive(int(f.read().strip() or 0))
    except (OSError, ValueError):
        return False


def tier_size(tier: str) -> int:
    return sum(MODELS[m].size for m in TIERS[tier] if not is_installed(m))
