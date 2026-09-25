#!/bin/bash
# Build the standalone PolySub.app (own Python + dependencies) into build/dist/,
# ad-hoc sign it and copy it to the project root.
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
cd "$ROOT"
command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
uv sync --frozen --extra gui --group dev
[ -f packaging/macos/PolySub.icns ] || uv run --frozen python packaging/macos/make_icon.py packaging/macos/PolySub.icns
packaging/engines/fetch.sh   # whisper-server + llama-server -> build/engines/bin (cached by commit)
uv run --frozen pyinstaller --noconfirm --clean --log-level WARN \
  --distpath build/dist --workpath build/work packaging/pyinstaller/polysub.spec
codesign --force --deep --sign - build/dist/PolySub.app
codesign --verify --deep --strict build/dist/PolySub.app
rm -rf PolySub.app
ditto build/dist/PolySub.app PolySub.app
du -sh PolySub.app
echo "Built: $ROOT/PolySub.app"
