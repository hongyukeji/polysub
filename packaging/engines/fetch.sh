#!/bin/bash
# Build the built-in engine servers from pinned upstream sources (engines.lock):
#   whisper-server (whisper.cpp, MIT) and llama-server (llama.cpp, MIT)
# as static binaries with Metal on Apple Silicon (CPU elsewhere).
#
#   packaging/engines/fetch.sh            -> build/engines/bin (used by the PyInstaller spec)
#   packaging/engines/fetch.sh --dev      -> the per-user cache, where PolySub looks when run from source
#   packaging/engines/fetch.sh DIR        -> DIR
# Needs git, cmake and a C++ compiler (Xcode command line tools on macOS).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# macOS ships bash 3.2, where `source <(...)` reads nothing: eval the plain NAME=value lines instead
eval "$(grep -E '^[A-Z_]+=[A-Za-z0-9._-]+$' "$ROOT/packaging/engines/engines.lock")"

case "${1:-}" in
  --dev) if [ "$(uname)" = Darwin ]; then OUT="$HOME/Library/Caches/PolySub/engines"; else OUT="${XDG_CACHE_HOME:-$HOME/.cache}/PolySub/engines"; fi ;;
  "") OUT="$ROOT/build/engines/bin" ;;
  *) OUT="$1" ;;
esac
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
  if [ ! -d "$src/.git" ]; then
    rm -rf "$src"
    git -c advice.detachedHead=false clone -q --depth 1 --branch "$tag" "https://github.com/ggml-org/$repo" "$src"
  fi
  local head; head=$(git -C "$src" rev-parse HEAD)
  [ "$head" = "$commit" ] || { echo "$repo $tag is $head, engines.lock expects $commit" >&2; exit 1; }
  cmake -S "$src" -B "$src/build" "${FLAGS[@]}" "$@" >/dev/null
  cmake --build "$src/build" -j "$JOBS" --target "$target" >/dev/null
  cp "$src/build/bin/$target" "$OUT/$target"
  echo "$commit" > "$stamp"
  echo "$target: built $tag"
}

build whisper whisper.cpp "$WHISPER_TAG" "$WHISPER_COMMIT" whisper-server -DWHISPER_BUILD_TESTS=OFF -DWHISPER_SDL2=OFF
build llama llama.cpp "$LLAMA_TAG" "$LLAMA_COMMIT" llama-server -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF
ls -l "$OUT"
