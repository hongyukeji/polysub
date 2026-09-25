# Changelog

## 0.3.0 — 2026-09-25

First public release.

- Install with Homebrew: `brew tap hongyukeji/tap && brew install polysub`, then `polysub install-app`.
- Standalone PolySub.app (own Python, Qt, PyAV, onnxruntime; no ffmpeg needed); drop videos on the window or the Dock icon.
- Subtitle editor, environment page with one-click speech-model download, endpoints for local and cloud (DeepSeek, Bailian, OpenAI, Ollama, LM Studio) models.
- Project layout: `src/polysub/`, `packaging/`, `scripts/`, `uv.lock`; GitHub Actions for CI and releases.

以下为此前本地开发阶段的中文记录：

### 0.3.0 开发记录（P2：独立 App 与完善）

- 独立的 `PolySub.app`（PyInstaller，约 200MB，自带 Python、Qt、PyAV、onnxruntime；不需要 ffmpeg）；App 名称和图标；Dock 图标可接收拖入的视频和文件夹。同一个可执行文件无参数时开界面、有参数时走命令行，后台处理进程也由它启动。
- 字幕预览和编辑：原文和译文逐行对照、搜索、手改、选中行重新翻译、保存。
- 环境页：音频解码、接口和模型检查；本机 oMLX 缺语音识别模型时一键下载（断点续传）并让 oMLX 加载；文件位置与清空缓存。
- 菜单「安装命令行工具」：把 `polysub` 链接到 App 里的可执行文件。
- 去掉 AppleScript 拖放小程序（由正式 App 取代）；`install.sh --app` 打包。
- 英文等字幕折行更均衡。

## 0.2.0 — 2026-09-25（P1：macOS 图形界面）

- PySide6 图形界面（`polysub gui`，或双击 `PolySub.app`）：任务页（拖放、多语言、翻译质量、实时进度、暂停 / 取消 / 重试 / 移除、在 Finder 中显示）、设置页、接口页（预设、测试连接：模型列表 + 翻译一句 + 语音识别）。
- 处理放在独立的后台进程，关掉窗口不影响；队列里新增进度、当前步骤、说明字段。
- 取消即时生效：请求改为流式，取消时断开连接，本机模型也随即停止生成（实测 1 秒；之前要等当前请求结束，最长约 4 分钟）。
- 暂停：当前视频做完后停止，继续后接着处理。

## 0.1.0 — 2026-09-25（P0：核心重构）

- 改写为 Python 包 `polysub`，命令行保留（`polysub`、`polysub-queue`、`PolySub.app` 拖放）。
- 所有模型服务统一为 OpenAI 兼容「接口配置」（TOML，Key 明文，权限 600），预设：本机 oMLX、DeepSeek、阿里云百炼、OpenAI、Ollama、LM Studio、自定义。
- 自己实现分批翻译，替换 VideoCaptioner：JSON 返回、行数校验、重试、逐行补译、带前文译文作上下文、进度与取消；被内容审核拒绝时改用备用接口。
- 原语言可设为自动：整片抽样投票识别一次，然后固定。
- 音频改用 PyAV 读取，不再需要 ffmpeg（有则作兜底）；VAD 改为直接用 onnxruntime 跑 Silero 模型，去掉 faster-whisper 与 VideoCaptioner 依赖（环境从 510MB 降到 149MB）。
- 默认翻译档位 low + 每批思考上限 1024 token：全片 9.6–11.3 分钟（两次实测），质量接近不限思考。
- 识别结果与翻译参考缓存；多目标语言只识别一次。
- 输出 SRT / ASS / VTT，单语或双语；已存在时跳过 / 覆盖 / 重命名。
- 持久化队列：崩溃后自动恢复未完成任务。
- 修正：提示词回声用异体字（如 部长 / 部長）时漏过滤；英文折行优先在标点处断开。

## 0.0 — `v0.0-bash` 标签

- bash 版：两遍 Qwen3-ASR 识别 + VideoCaptioner 翻译，本地转发加思考开关。
