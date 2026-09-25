# 更新记录

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
