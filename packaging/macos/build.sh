#!/bin/bash
# Build the standalone PolySub.app (own Python + dependencies) into build/dist/,
# ad-hoc sign it and copy it to the project root.
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
cd "$ROOT"
command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
uv sync --frozen --extra gui --group dev
[ -f packaging/macos/PolySub.icns ] || uv run --frozen python packaging/macos/make_icon.py packaging/macos/PolySub.icns
# whisper-server + llama-server -> build/engines/bin (cached by commit). Without them the app still
# works with oMLX or cloud endpoints, so a local build goes on; releases set POLYSUB_REQUIRE_ENGINES=1.
if ! packaging/engines/fetch.sh; then
  [ -n "${POLYSUB_REQUIRE_ENGINES:-}" ] && exit 1
  echo "警告：内置引擎没有编译成功，这次打出的 App 不带内置引擎（oMLX、云端接口照常可用）。" >&2
  echo "      按上面的提示解决后重新运行本脚本即可补上。" >&2
fi
uv run --frozen pyinstaller --noconfirm --clean --log-level WARN \
  --distpath build/dist --workpath build/work packaging/pyinstaller/polysub.spec
codesign --force --deep --sign - build/dist/PolySub.app
codesign --verify --deep --strict build/dist/PolySub.app
rm -rf PolySub.app
ditto build/dist/PolySub.app PolySub.app
du -sh PolySub.app
echo "Built: $ROOT/PolySub.app"
