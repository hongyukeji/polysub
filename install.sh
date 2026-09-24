#!/bin/bash
# PolySub 安装 / 修复。可以重复运行；把项目目录移动到别处后，再运行一次即可。
#   ./install.sh               安装依赖、生成 config.sh、链接命令、编译拖放 App
#   ./install.sh --with-model  另外下载语音识别模型到 oMLX（约 2.5GB）
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
BIN="$HOME/.local/bin"
APP="$ROOT/PolySub.app"   # App 放在项目目录里，随项目一起移动
say() { printf '\n==> %s\n' "$*"; }

say "检查 ffmpeg / uv"
command -v ffmpeg >/dev/null || { command -v brew >/dev/null && brew install ffmpeg || { echo "请先安装 ffmpeg"; exit 1; }; }
command -v uv >/dev/null || { echo "请先安装 uv: https://docs.astral.sh/uv/"; exit 1; }

say "Python 环境（$ROOT/.venv）"
[ -x "$ROOT/.venv/bin/python" ] || uv venv -q --python 3.12 "$ROOT/.venv"
uv pip install -q --python "$ROOT/.venv/bin/python" -r "$ROOT/requirements.txt"

say "配置文件（$ROOT/config.sh）"
if [ ! -f "$ROOT/config.sh" ]; then
  cp "$ROOT/config.example.sh" "$ROOT/config.sh"
  # 本机装了 oMLX 的话，把它的 API Key 填进去
  KEY="$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.omlx/settings.json")))["auth"]["api_key"])' 2>/dev/null || true)"
  [ -n "$KEY" ] && sed -i '' "s|^POLYSUB_OMLX_KEY=.*|POLYSUB_OMLX_KEY=\"\${POLYSUB_OMLX_KEY:-$KEY}\"            # oMLX 的 API Key|" "$ROOT/config.sh"
  echo "已生成；云端 API Key 等按需修改"
else
  echo "已存在，保留不动"
fi
chmod 600 "$ROOT/config.sh"

say "命令链接（$BIN）"
mkdir -p "$BIN"
ln -sfn "$ROOT/bin/polysub" "$BIN/polysub"
ln -sfn "$ROOT/bin/polysub-queue" "$BIN/polysub-queue"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "提示：把 $BIN 加到 PATH";; esac

say "拖放 App（$APP）"
rm -rf "$APP"
osacompile -o "$APP" "$ROOT/app/PolySub.applescript"

if [ "${1:-}" = "--with-model" ]; then
  say "下载语音识别模型到 oMLX"
  uvx --from huggingface_hub hf download mlx-community/Qwen3-ASR-1.7B-8bit \
    --local-dir "$HOME/.omlx/models/mlx-community/Qwen3-ASR-1.7B-8bit"
  echo "下载完成后，在 oMLX 管理界面点一次 Reload，让它发现新模型"
elif [ ! -d "$HOME/.omlx/models/mlx-community/Qwen3-ASR-1.7B-8bit" ]; then
  echo; echo "注意：没找到语音识别模型，运行 ./install.sh --with-model 下载"
fi

say "完成。试试：polysub --help
    拖放 App：$APP（可以拖进 Dock）"
