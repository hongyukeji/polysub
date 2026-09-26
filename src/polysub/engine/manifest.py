"""Built-in models: what can be downloaded, how big it is, and the tiers.

The candidates are provisional until R0 (docs/plans/builtin-engine.md) settles
them with measurements. Sizes and sha256 come from scripts/engine/pin_models.py
(also runnable as the manual "Pin models" workflow). An entry without sha256
(e.g. hf:… models) is checked against the sha256 Hugging Face publishes.
"""
import functools
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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
    backend: str = ""    # whisper (whisper-server) | llama (llama-server); default: asr -> whisper, mt -> llama
    mmproj: str = ""     # llama backend, audio / vision models: the projector file in the same repo
    mmproj_size: int = 0
    mmproj_sha256: str = ""

    @property
    def engine(self) -> str:
        return self.backend or ("whisper" if self.kind == "asr" else "llama")


MODELS: Dict[str, Model] = {m.id: m for m in (
    Model("asr-qwen3", "asr", "Qwen3-ASR 1.7B（Q8）", "ggml-org/Qwen3-ASR-1.7B-GGUF", "Qwen3-ASR-1.7B-Q8_0.gguf",
          2_165_034_944, "Apache-2.0", "58e22d0532d4eacaf034cfac17a6fed159f37c41390c710186783be439d1fc57",
          backend="llama", mmproj="mmproj-Qwen3-ASR-1.7B-Q8_0.gguf", mmproj_size=355_709_344,
          mmproj_sha256="46c1d533af3f354ceb37ce855dbceff7da7fa7cf1e6a523df3b13440bd164c0d"),
    Model("asr-turbo", "asr", "Whisper large-v3-turbo（Q5）", "ggerganov/whisper.cpp",
          "ggml-large-v3-turbo-q5_0.bin", 574_041_195, "MIT",
          "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"),
    Model("mt-1.7b", "mt", "Qwen3 1.7B（Q8）", "Qwen/Qwen3-1.7B-GGUF", "Qwen3-1.7B-Q8_0.gguf",
          1_834_426_016, "Apache-2.0", "061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a"),
    Model("mt-4b", "mt", "Qwen3 4B（Q4_K_M）", "Qwen/Qwen3-4B-GGUF", "Qwen3-4B-Q4_K_M.gguf",
          2_497_280_256, "Apache-2.0", "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5"),
    Model("mt-30b-a3b", "mt", "Qwen3 30B-A3B（IQ4_XS）", "unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF",
          "Qwen3-30B-A3B-Instruct-2507-IQ4_XS.gguf", 16_378_073_504, "Apache-2.0",
          "bd03aa8b332adb414eb842ec725b4a5dd116b17b57d4e5543ba742587a3c5e21"),
)}

# tier -> (speech model, translation model)
TIERS: Dict[str, Tuple[str, str]] = {
    "light": ("asr-qwen3", "mt-1.7b"),
    "standard": ("asr-qwen3", "mt-4b"),
    "high": ("asr-qwen3", "mt-30b-a3b"),
}
TIER_LABELS = {"light": "轻量（8 GB 内存）", "standard": "标准（16 GB 内存）", "high": "高质量（32 GB 及以上）"}


def recommended_tier(memory: Optional[int] = None) -> str:
    memory = system.total_memory() if memory is None else memory
    if not memory:
        return "standard"
    gb = memory / 1024 ** 3
    return "light" if gb < 12 else "standard" if gb < 28 else "high"


_custom_dir = ""   # Settings / Models page: the user's own model folder (config general.models_dir)


def set_models_dir(path: str):
    global _custom_dir
    _custom_dir = os.path.expanduser(path or "")


def default_models_dir() -> str:
    return os.path.join(user_data_dir("PolySub", appauthor=False), "models")


def models_dir() -> str:
    return os.environ.get("POLYSUB_MODELS") or _custom_dir or default_models_dir()


def other_dirs() -> List[str]:
    """Where other tools keep GGUF files; an identical file there is used instead of downloading it again.
    (oMLX keeps MLX safetensors, which llama.cpp cannot load.)"""
    home = os.path.expanduser("~")
    return [os.path.join(home, ".lmstudio", "models"), os.path.join(home, ".cache", "lm-studio", "models"),
            os.path.join(home, ".cache", "huggingface", "hub"), os.path.join(home, ".cache", "llama.cpp")]


@functools.lru_cache(maxsize=64)
def find_existing(repo: str, name: str, size: int) -> str:
    """An existing copy of this file elsewhere (same name, or llama.cpp's repo_name form, and same size)."""
    if not size:
        return ""
    wanted = {name, f"{repo.replace('/', '_')}_{name}"}
    for top in other_dirs():
        if not os.path.isdir(top):
            continue
        for root, _dirs, fnames in os.walk(top):
            for f in fnames:
                if f in wanted:
                    p = os.path.join(root, f)
                    try:
                        if os.path.getsize(p) == size:   # follows the Hugging Face cache's symlinks
                            return p
                    except OSError:
                        pass
    return ""


def path_of(model_id: str) -> str:
    m = MODELS[model_id]
    own = os.path.join(models_dir(), m.file)
    return own if os.path.isfile(own) else (find_existing(m.repo, m.file, m.size) or own)


def files(m: Model) -> List[Tuple[str, str, str]]:
    """Files a model needs: (file in the repo, local path, sha256)."""
    main = download_path(m)
    out = [(m.file, main, m.sha256)]
    if m.mmproj:
        proj = os.path.join(os.path.dirname(main), m.mmproj)
        if not os.path.isfile(proj):
            proj = find_existing(m.repo, m.mmproj, m.mmproj_size) or os.path.join(models_dir(), m.mmproj)
        out.append((m.mmproj, proj, m.mmproj_sha256))
    return out


def total_size(m: Model) -> int:
    return m.size + m.mmproj_size


def is_installed(model_id: str) -> bool:
    m = remote(model_id)
    return bool(m) and all(os.path.isfile(p) and os.path.getsize(p) > 0 for _, p, _ in files(m))


def engine_of(model: str) -> Tuple[str, str]:
    """Config model value -> (backend, projector path or ""). Own files: .gguf runs on llama-server
    (a sibling mmproj*.gguf is used for audio models), anything else on whisper-server."""
    m = remote(model)
    if m and m.id in MODELS:
        return m.engine, files(m)[1][1] if m.mmproj else ""
    path = resolve(model)
    if not path.endswith(".gguf"):
        return "whisper", ""
    d = os.path.dirname(path)
    proj = sorted(f for f in (os.listdir(d) if os.path.isdir(d) else []) if f.startswith("mmproj") and f.endswith(".gguf"))
    return "llama", os.path.join(d, proj[0]) if proj else ""


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
    return sum(total_size(MODELS[m]) for m in TIERS[tier] if not is_installed(m))
