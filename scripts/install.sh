#!/bin/bash
# Install PolySub from source (developers). Safe to re-run; run again after moving the folder.
#   scripts/install.sh               .venv (uv sync) and the `polysub` command in ~/.local/bin
#   scripts/install.sh --app         also build PolySub.app into the project folder
#   scripts/install.sh --with-model  also download the speech-recognition model into a local oMLX
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
BIN="$HOME/.local/bin"
say() { printf '\n==> %s\n' "$*"; }
command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }

say "Python environment ($ROOT/.venv)"
(cd "$ROOT" && uv sync --frozen --extra gui)

say "Command line tool ($BIN/polysub)"
mkdir -p "$BIN"
ln -sfn "$ROOT/scripts/polysub" "$BIN/polysub"
rm -f "$BIN/polysub-queue"   # replaced by `polysub queue add`
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: add $BIN to your PATH";; esac

for arg in "$@"; do
  case "$arg" in
    --app)
      say "Building PolySub.app"
      "$ROOT/packaging/macos/build.sh" ;;
    --with-model)
      say "Downloading the speech-recognition model into oMLX"
      "$ROOT/.venv/bin/python" -c 'import os; from polysub import models; d = os.path.join(models.omlx_models_dir(), models.DEFAULT_ASR_REPO); models.download(models.DEFAULT_ASR_REPO, d); print(d)'
      echo "Then click Reload in the oMLX admin page (or use the Environment tab in PolySub)." ;;
  esac
done

say "Environment check"
"$ROOT/.venv/bin/python" -m polysub doctor || true
