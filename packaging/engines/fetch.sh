#!/bin/bash
# Build the built-in engine servers from pinned upstream sources (engines.lock):
#   whisper-server (whisper.cpp, MIT) and llama-server (llama.cpp, MIT)
# as static binaries with Metal on Apple Silicon (CPU elsewhere).
#
#   packaging/engines/fetch.sh            -> build/engines/bin (used by the PyInstaller spec)
#   packaging/engines/fetch.sh --dev      -> the per-user cache, where PolySub looks when run from source
#   packaging/engines/fetch.sh DIR        -> DIR
# Needs git, cmake and a C++ compiler (Xcode command line tools on macOS).
# Behind a slow or blocked connection to github.com, set POLYSUB_GITHUB to a mirror prefix
# (default https://github.com); downloads are retried 3 times.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# macOS ships bash 3.2, where `source <(...)` reads nothing: eval the plain NAME=value lines instead
eval "$(grep -E '^[A-Z_]+=[A-Za-z0-9._-]+$' "$ROOT/packaging/engines/engines.lock")"

case "${1:-}" in
  --dev) if [ "$(uname)" = Darwin ]; then OUT="$HOME/Library/Caches/PolySub/engines"; else OUT="${XDG_CACHE_HOME:-$HOME/.cache}/PolySub/engines"; fi ;;
  "") OUT="$ROOT/build/engines/bin" ;;
  *) OUT="$1" ;;
esac
GITHUB="${POLYSUB_GITHUB:-https://github.com}"

missing=()
command -v git >/dev/null || missing+=("git")
command -v cmake >/dev/null || missing+=("cmake")
command -v c++ >/dev/null || missing+=("C++ 编译器")
if [ ${#missing[@]} -gt 0 ]; then
  echo "编译内置引擎缺少：${missing[*]}" >&2
  if [ "$(uname)" = Darwin ]; then
    echo "  安装：xcode-select --install   （git 和编译器）" >&2
    echo "        brew install cmake" >&2
  fi
  exit 2
fi

WORK="$ROOT/build/engines/src"
mkdir -p "$OUT" "$WORK"
JOBS=$( (sysctl -n hw.ncpu 2>/dev/null || nproc) | head -1)
FLAGS=(-DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF)
if [ "$(uname)" = Darwin ]; then
  FLAGS+=(-DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0 -DCMAKE_OSX_ARCHITECTURES=arm64)
fi

build() {  # name repo tag commit target extra-cmake-flags...
  local name=$1 repo=$2 tag=$3 commit=$4 target=$5; shift 5
  local stamp="$OUT/.$target.commit"
  if [ -x "$OUT/$target" ] && [ "$(cat "$stamp" 2>/dev/null)" = "$commit" ]; then
    echo "$target: up to date ($tag)"; return
  fi
  local src="$WORK/$name-$tag"
  if [ "$(git -C "$src" rev-parse HEAD 2>/dev/null)" != "$commit" ]; then
    rm -rf "$src"   # missing, or left half-done by an interrupted download
    mkdir -p "$src"
    git -C "$src" init -q
    local try
    for try in 1 2 3; do
      if git -C "$src" fetch -q --depth 1 "$GITHUB/ggml-org/$repo" "$commit"; then break; fi
      [ "$try" = 3 ] && { echo "下载 $repo 失败（网络连不上 $GITHUB？可设置 POLYSUB_GITHUB 用镜像后重试）" >&2; exit 1; }
      echo "下载 $repo 失败，$((try * 5)) 秒后重试…" >&2
      sleep $((try * 5))
    done
    git -C "$src" -c advice.detachedHead=false checkout -q FETCH_HEAD
  fi
  echo "$target: building $tag (a few minutes)…"
  cmake -S "$src" -B "$src/build" "${FLAGS[@]}" "$@" >/dev/null
  cmake --build "$src/build" -j "$JOBS" --target "$target" >/dev/null
  cp "$src/build/bin/$target" "$OUT/$target"
  echo "$commit" > "$stamp"
  echo "$target: built $tag"
}

build whisper whisper.cpp "$WHISPER_TAG" "$WHISPER_COMMIT" whisper-server -DWHISPER_BUILD_TESTS=OFF -DWHISPER_SDL2=OFF
build llama llama.cpp "$LLAMA_TAG" "$LLAMA_COMMIT" llama-server -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF
ls -l "$OUT"
