#!/bin/bash
# 打包独立的 PolySub.app（自带 Python 与依赖），输出到项目根目录的 PolySub.app
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "先运行 $ROOT/install.sh"; exit 1; }
uv pip install -q --python "$PY" "pyinstaller>=6.10"
[ -f "$ROOT/packaging/macos/PolySub.icns" ] || "$PY" "$ROOT/packaging/make_icon.py" "$ROOT/packaging/macos/PolySub.icns"
cd "$ROOT"
"$PY" -m PyInstaller --noconfirm --clean --log-level WARN \
  --distpath "$ROOT/build/dist" --workpath "$ROOT/build/work" "$ROOT/packaging/macos/PolySub.spec"
rm -rf "$ROOT/PolySub.app"
ditto "$ROOT/build/dist/PolySub.app" "$ROOT/PolySub.app"
codesign --force --deep --sign - "$ROOT/PolySub.app" >/dev/null 2>&1 || true
du -sh "$ROOT/PolySub.app"
echo "完成：$ROOT/PolySub.app"
