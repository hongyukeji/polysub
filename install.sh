#!/bin/bash
# PolySub 安装 / 修复。可以重复运行；把项目目录移动到别处后，再运行一次即可。
#   ./install.sh               Python 环境、命令链接、拖放 App
#   ./install.sh --with-model  另外下载语音识别模型到本机 oMLX（约 2.5GB）
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
BIN="$HOME/.local/bin"
say() { printf '\n==> %s\n' "$*"; }

command -v uv >/dev/null || { echo "请先安装 uv：https://docs.astral.sh/uv/"; exit 1; }

say "Python 环境（$ROOT/.venv）"
[ -x "$ROOT/.venv/bin/python" ] || uv venv -q --python 3.12 "$ROOT/.venv"
uv pip install -q --python "$ROOT/.venv/bin/python" -e "$ROOT"

say "命令链接（$BIN）"
mkdir -p "$BIN"
ln -sfn "$ROOT/bin/polysub" "$BIN/polysub"
ln -sfn "$ROOT/bin/polysub-queue" "$BIN/polysub-queue"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "提示：把 $BIN 加到 PATH";; esac

if [ "$(uname)" = Darwin ]; then
  say "拖放 App（$ROOT/PolySub.app）"
  rm -rf "$ROOT/PolySub.app"
  osacompile -o "$ROOT/PolySub.app" "$ROOT/app/PolySub.applescript"
fi

if [ "${1:-}" = "--with-model" ]; then
  say "下载语音识别模型到 oMLX"
  uvx --from huggingface_hub hf download mlx-community/Qwen3-ASR-1.7B-8bit \
    --local-dir "$HOME/.omlx/models/mlx-community/Qwen3-ASR-1.7B-8bit"
  echo "下载完成后，在 oMLX 管理界面点一次 Reload，让它发现新模型"
fi

say "环境检查"
"$ROOT/.venv/bin/python" -m polysub doctor || true
echo; echo "配置文件：$("$ROOT/.venv/bin/python" -m polysub config path)"
