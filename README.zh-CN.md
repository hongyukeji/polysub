# PolySub

[English](README.md)

在自己的 Mac 上，把任何视频变成任意语言的字幕。

PolySub 先识别视频里的语音，再通读一遍全片原文，记住人名和上下文，然后用大模型翻译。模型可以用**本机的**（比如 Apple 芯片上的 [oMLX](https://github.com/jundot/omlx)），也可以用任何 **OpenAI 兼容的云端接口**（DeepSeek、阿里云百炼 / 通义千问、OpenAI 等）。字幕文件放在视频旁边（`电影.zh-Hans.srt`），IINA 等播放器会自动加载。

## 安装

```bash
brew tap hongyukeji/tap
brew install polysub
polysub install-app        # 把 PolySub.app 复制到「应用程序」
```

升级：`brew upgrade polysub`，之后再运行一次 `polysub install-app`。需要 Apple 芯片、macOS 13 以上。

## 使用

- **App**：把视频或文件夹拖进窗口（或拖到 Dock 图标上），选好字幕语言和翻译质量就行。后台逐个处理，每个完成时有系统通知。双击已完成的视频，可以逐行查看、修改字幕。
- **命令行**

  ```bash
  polysub 电影.mp4                  # 自动识别原语言 → 简体中文
  polysub -t zh-Hans,en 电影.mp4    # 同时出两种语言，语音只识别一次
  polysub queue add ~/Movies/某剧   # 整个文件夹放到后台处理
  polysub --help
  ```

## 模型

所有模型服务都是一份 OpenAI 兼容的「接口」（Base URL + API Key），在 App 的「接口」页设置，或者直接改 `~/Library/Application Support/PolySub/config.toml`：

- **语音识别**（`/v1/audio/transcriptions`）：默认用本机 oMLX 上的 `Qwen3-ASR-1.7B-8bit`，App 的「环境」页可以一键下载。
- **翻译**（`/v1/chat/completions`）：默认用本机的 `qwen3.8-27b-4bit`；内置 DeepSeek 和阿里云百炼的预设。云端会审核内容，被拒的批次会自动改用备用接口。

翻译质量：**快速**（不思考）、**标准**（少量思考，默认；M4 Max 用本机模型，一部 2 小时的片子约 10 分钟）、**精细**（不限思考，约慢 3 倍）。

## 原理

读音轨（PyAV）→ Silero VAD 切出说话片段 → 语音识别 → 翻译模型写一份翻译参考（场景、人物、可能的同音误识别）和人名提示 → 带着提示再识别一遍 → 分批翻译（附翻译参考和前文）→ SRT / ASS / VTT。

## 开发

```bash
scripts/install.sh           # uv sync，并把 polysub 命令装到 ~/.local/bin
scripts/install.sh --app     # 另外打包 PolySub.app
uv run python -m unittest discover -s tests
```

目录：`src/polysub/`（Python 包，`gui/` 是 PySide6 界面）、`tests/`、`packaging/`（PyInstaller 配置、macOS 打包脚本、图标）、`scripts/`。发布：推一个和 `pyproject.toml` 版本一致的 `vX.Y.Z` 标签，GitHub Actions 会自动打包并发布 Release，[hongyukeji/homebrew-tap](https://github.com/hongyukeji/homebrew-tap) 随后自动更新 Formula。

## 许可证

MIT，见 [LICENSE](LICENSE)。App 里打包的第三方组件各有各的许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
