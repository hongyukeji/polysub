# PolySub

[中文说明](README.zh-CN.md)

Turn any video into subtitles in any language — on your own Mac.

PolySub listens to the video (speech recognition), reads the whole transcript once to learn names and context, then translates it with a large language model. It works with a **local model** (for example [oMLX](https://github.com/jundot/omlx) on Apple silicon) or any **OpenAI-compatible cloud API** (DeepSeek, Alibaba Bailian / Qwen, OpenAI, …). The subtitle file is written next to the video (`movie.zh-Hans.srt`), so players such as IINA load it automatically.

## Install

```bash
brew tap hongyukeji/tap
brew trust hongyukeji/tap   # once per tap (Homebrew 7+)
brew install polysub
polysub install            # copies PolySub.app to /Applications
```

Upgrade with `brew upgrade polysub` (then `polysub install` again). Requires macOS 13+ on Apple silicon.

## Use

- **App** — drag videos or folders into the window (or onto the Dock icon), pick the subtitle languages and a quality level, done. Jobs run in the background; you get a notification when each one finishes. Double-click a finished video to review and edit the subtitles line by line.
- **Command line**

  ```bash
  polysub movie.mp4                  # auto-detect the spoken language → Simplified Chinese
  polysub -t zh-Hans,en movie.mp4    # two languages, speech recognised once
  polysub queue add ~/Movies/Show    # a whole folder, in the background
  polysub --help
  ```

## Models

Every model service is an OpenAI-compatible *endpoint* (base URL + API key), configured in the app's **Endpoints** tab or in `~/Library/Application Support/PolySub/config.toml`:

- **Speech recognition** (`/v1/audio/transcriptions`) — default `Qwen3-ASR-1.7B-8bit` on a local oMLX. The **Environment** tab can download it for you.
- **Translation** (`/v1/chat/completions`) — default the local `qwen3.8-27b-4bit`; DeepSeek and Bailian presets are built in. Cloud providers moderate content; a rejected batch is retried on a fallback endpoint.

Quality levels: **Fast** (no reasoning), **Standard** (short reasoning, default — a 2-hour film takes about 10 minutes on an M4 Max with local models), **Fine** (unlimited reasoning, about 3× slower).

## How it works

Audio (PyAV) → Silero VAD speech segments → speech recognition → the translation model writes a short brief (setting, characters, likely mis-hearings) and name hints → second recognition pass with those hints → batched translation with the brief and previous lines as context → SRT / ASS / VTT.

## Develop

```bash
scripts/install.sh           # uv sync + `polysub` command in ~/.local/bin
scripts/install.sh --app     # also build PolySub.app
uv run python -m unittest discover -s tests
```

Layout: `src/polysub/` (package; `gui/` is the PySide6 app), `tests/`, `packaging/` (PyInstaller spec, macOS build script, icon), `scripts/`. Releases: push a `vX.Y.Z` tag matching `pyproject.toml`; GitHub Actions builds the app and publishes the release, and [hongyukeji/homebrew-tap](https://github.com/hongyukeji/homebrew-tap) updates the formula automatically.

## License

MIT — see [LICENSE](LICENSE). The app bundles third-party components under their own licenses: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
