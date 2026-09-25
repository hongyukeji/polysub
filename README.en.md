# PolySub

[![CI](https://github.com/hongyukeji/polysub/actions/workflows/ci.yml/badge.svg)](https://github.com/hongyukeji/polysub/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/hongyukeji/polysub?sort=date)](https://github.com/hongyukeji/polysub/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[中文](README.md) | English

A free, open-source subtitle tool for Apple silicon Macs. It recognises the speech in a video, translates it into any language with the whole film as context, and writes the subtitle file next to the video. Models can run locally (for example on [oMLX](https://github.com/jundot/omlx)) or behind any OpenAI-compatible cloud API.

[Install](#install) · [Use](#use) · [Models and endpoints](#models-and-endpoints) · [Update](#update) · [Uninstall](#uninstall) · [Data and cache](#data-and-cache) · [FAQ](#faq) · [Develop](#develop)

## Features

- **Subtitles in any language**: the spoken language is detected automatically; one run can produce several target languages, and speech is recognised only once.
- **Translation with context**: PolySub reads the whole transcript first and notes the setting, characters and how names are written, then translates in batches with those notes and the preceding lines. The name hints also feed a second recognition pass that fixes mis-heard words.
- **Local or cloud models**: speech recognition and translation both use OpenAI-compatible endpoints. Presets for local oMLX, DeepSeek, Alibaba Bailian, OpenAI, Ollama and LM Studio are built in; batches rejected by a cloud provider go to a fallback endpoint.
- **Background queue**: drop videos or folders on the window or the Dock icon. They are processed one by one in the background, even after the window is closed, with pause, cancel, retry and a notification when each finishes.
- **Subtitle editor**: double-click a finished video to review source and translation side by side, search, edit, or re-translate selected lines.
- **Command line**: everything is also available in the terminal for batches and scripts.

Subtitles are saved as `video.<lang>.srt` (ASS and VTT also supported), so players such as IINA pick them up automatically.

The Tasks tab in PolySub 0.3 (the interface is currently in Chinese — tabs 任务 Tasks, 设置 Settings, 接口 Endpoints, 环境 Environment; sample videos are Blender open movies):

<img src="docs/images/tasks-zh-CN.png" alt="PolySub Tasks tab: subtitle language and quality pickers, four videos with status and progress, and Continue, Cancel and Retry buttons" width="800">

## Requirements

- Apple silicon Mac with macOS 13 or later; Intel Macs are not supported.
- A speech-recognition endpoint and a translation endpoint. For local models, [oMLX](https://github.com/jundot/omlx) is recommended; the default translation model needs plenty of memory (tested with 64 GB). Cloud endpoints need an API key from the provider.

Testing so far has been on an M4 Max (64 GB) MacBook Pro with macOS 27; speed differs on other machines.

## Install

Pick **one** of the three methods.

| Method | Best for | Security prompt on first launch |
| --- | --- | --- |
| Homebrew (recommended) | Installing and upgrading with Homebrew | No |
| Release download | Not using Homebrew | Yes, allow it once in System Settings |
| Build from source | Changing code, debugging | No |

### Option 1: Homebrew

Requires [Homebrew](https://brew.sh/). Xcode is not needed.

```bash
brew tap hongyukeji/tap
brew trust hongyukeji/tap
brew install polysub
polysub install
open /Applications/PolySub.app
```

Since Homebrew 7, formulae from third-party taps load only after you trust the tap with `brew trust` (once). `brew install` puts the app and the `polysub` command into Homebrew; `polysub install` copies PolySub.app to /Applications. The formula lives in [hongyukeji/homebrew-tap](https://github.com/hongyukeji/homebrew-tap).

### Option 2: Release download

1. Download `PolySub-<version>-macos-arm64.zip` from [Releases](https://github.com/hongyukeji/polysub/releases/latest) (not the GitHub-generated `Source code` archive).
2. Double-click to unzip and drag `PolySub.app` into Applications.
3. Open PolySub. The download is not notarized by Apple, so the first launch is blocked; see the [FAQ](#macos-says-the-developer-cannot-be-verified).

For the command line, use **文件 → 安装命令行工具 polysub…** (File → Install command line tool) in the app menu.

### Option 3: Build from source

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/hongyukeji/polysub.git
cd polysub
scripts/install.sh --app
```

The script sets up the Python environment, links `polysub` into `~/.local/bin` and builds `PolySub.app` in the project folder. It builds `main`; run `git checkout v<version>` first for a specific release.

### After installing

On first launch, open the **Environment** tab: when audio decoding, endpoints and models all show as OK, you are ready. If a local oMLX lacks the speech-recognition model, you can download it there with one click.

## Use

### App

Drag videos or folders onto the window (or the Dock icon), pick the subtitle languages and a quality level, and that's it.

| Quality | Behaviour |
| --- | --- |
| Fast | No reasoning, fastest |
| Standard (recommended) | Short reasoning per batch; a 2-hour film takes about 10 minutes on an M4 Max with local models |
| Fine | Unlimited reasoning, about 3× slower |

**Settings** covers the source language, subtitle format, bilingual subtitles and what to do when a subtitle already exists; **Endpoints** manages model services; **Environment** runs checks, downloads models and shows file locations.

### Common commands

| Command | What it does |
| --- | --- |
| `polysub movie.mp4` | Make subtitles now (language detected, targets from settings) |
| `polysub -t zh-Hans,en movie.mp4` | Simplified Chinese and English subtitles in one run |
| `polysub -f ja --think off movie.mp4` | Source language Japanese, Fast quality |
| `polysub queue add ~/Movies/Show` | Add a whole folder to the background queue |
| `polysub queue list` | Show the queue |
| `polysub endpoints test NAME` | Check that an endpoint responds |
| `polysub doctor` | Check the environment |
| `polysub gui` | Open the app window |
| `polysub install` | Copy PolySub.app to /Applications |
| `polysub --help` | All commands; add `--help` to any command |

The app and the command line share the same settings and queue.

## Models and endpoints

Each model service is an OpenAI-compatible *endpoint* (base URL + API key), set up in the app's **Endpoints** tab or in `~/Library/Application Support/PolySub/config.toml` (mode 600; keys are stored in plain text).

| Purpose | API | Default |
| --- | --- | --- |
| Speech recognition | `/v1/audio/transcriptions` | `Qwen3-ASR-1.7B-8bit` on a local oMLX |
| Translation | `/v1/chat/completions` | `qwen3.8-27b-4bit` on a local oMLX |

Cloud providers moderate content. Set a *fallback endpoint* (usually a local model) in Settings and rejected batches are translated there instead.

## Update

### Homebrew

Quit PolySub, then run:

```bash
brew update
brew upgrade polysub
polysub install
```

`brew upgrade` updates the files inside Homebrew; `polysub install` copies the new version to /Applications. If Homebrew reports an `untrusted tap`, run `brew trust hongyukeji/tap` once.

### Release download

Download the new version, quit PolySub and replace the old `PolySub.app` in Applications. Settings, queue and cache are kept.

### Source

```bash
git pull --ff-only
scripts/install.sh --app
```

## Uninstall

```bash
rm -rf /Applications/PolySub.app
brew uninstall polysub        # if installed with Homebrew
rm -f ~/.local/bin/polysub    # if you installed the command from the app menu
```

To also remove settings, queue, cache and logs:

```bash
rm -rf ~/Library/Application\ Support/PolySub ~/Library/Caches/PolySub ~/Library/Logs/PolySub
rm -f ~/Library/Preferences/com.polysub.PolySub.plist
```

Subtitles next to your videos are not removed; models downloaded into oMLX are managed by oMLX.

## Data and cache

| Data | Location | Notes |
| --- | --- | --- |
| Settings and endpoints | `~/Library/Application Support/PolySub/config.toml` | Contains API keys, mode 600 |
| Queue | `~/Library/Application Support/PolySub/queue.json` | Unfinished jobs resume after a restart |
| Recognition cache | `~/Library/Caches/PolySub/` | Transcripts, translation notes and editor data, so translating a video again skips recognition. Not pruned automatically; clear it in the Environment tab |
| Log | `~/Library/Logs/PolySub/PolySub.log` | Start, finish, failure and cancellation of each job |
| Subtitles | Next to the video | `video.<lang>.srt` / `.ass` / `.vtt` |

## FAQ

### macOS says the developer cannot be verified

The release download is ad-hoc signed and not notarized by Apple. If you trust the file came from this repository, follow [Apple's instructions](https://support.apple.com/102445): try to open it, then choose **Open Anyway** in **System Settings → Privacy & Security**. Installing with Homebrew avoids this prompt.

Each release includes `SHA256SUMS`. Put it next to the zip and run `shasum -a 256 -c SHA256SUMS` to check the download; a checksum is not a substitute for notarization.

### An endpoint cannot be reached or a model is missing

Open the **Environment** tab or run `polysub doctor` for the status of audio decoding, each endpoint and each model. With a local oMLX, make sure it is running and the model is loaded.

### Some lines were not translated by a cloud provider

Cloud moderation can reject some dialogue. With a fallback endpoint set, rejected batches are translated there; lines that still fail keep the original text, and the job's **Notes** column says how many.

### My player does not load the subtitles

Subtitles share the video's name and folder and carry a language code (such as `movie.zh-Hans.srt`). IINA, VLC and Infuse load them automatically; other players may need you to pick the file.

### Names are translated inconsistently

Keep **two-pass recognition** on (the default). PolySub collects the names in the whole film before translating and passes them to the second recognition pass and every translation batch. Fix any remaining mistakes in the subtitle editor.

## Develop

### Layout

| Path | Contents |
| --- | --- |
| [`src/polysub/`](src/polysub/) | Core: audio, VAD, speech recognition, translation, subtitles, queue, config, CLI |
| [`src/polysub/gui/`](src/polysub/gui/) | PySide6 app: tasks, settings, endpoints, environment, subtitle editor |
| [`tests/`](tests/) | Automated tests |
| [`packaging/`](packaging/) | PyInstaller spec, macOS build script and icon |
| [`scripts/`](scripts/) | Source install script and dev launcher |
| [`docs/releases/`](docs/releases/) | Release notes for each version |

### Build and test

```bash
uv sync --extra gui
uv run python -m unittest discover -s tests
packaging/macos/build.sh      # build and sign PolySub.app
```

When opening a [pull request](https://github.com/hongyukeji/polysub/pulls), describe the problem, the scope of the change and how you verified it.

### Release

1. Bump the version in `pyproject.toml` and `src/polysub/__init__.py`, and describe the changes in [CHANGELOG.md](CHANGELOG.md).
2. Write `docs/releases/<version>.md` following the [release-notes guide](docs/releases/README.md).
3. Push a `v<version>` tag. GitHub Actions tests, builds, signs and creates a draft release.
4. Download the draft's package, verify it, add the results to the release notes and publish. [hongyukeji/homebrew-tap](https://github.com/hongyukeji/homebrew-tap) then updates the formula automatically.

### Reporting issues

Open an [issue](https://github.com/hongyukeji/polysub/issues) with your Mac model, macOS version, PolySub version, the endpoints and models in use, and the output of `polysub doctor` or the relevant log lines.

## Credits and license

PolySub is maintained by [@hongyukeji](https://github.com/hongyukeji) under the [MIT License](LICENSE). The app bundles third-party components (Python, Qt, PyAV/FFmpeg, ONNX Runtime, Silero VAD and others) under their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
