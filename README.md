# PolySub

视频 → 任意语言字幕。本机或云端的 OpenAI 兼容接口负责语音识别和翻译；输出 `视频名.<语言>.srt`（也可以是 ASS / VTT），放在视频旁边，IINA 等播放器会自动加载。

- 当前版本 0.1.0（P0：核心重构完成，命令行可用；图形界面在 P1）。
- 项目目录可以随意移动，移动后运行一次 `./install.sh`。
- 选型过程和实测数据由维护者另行记录，不在仓库中。

## 用法

### 拖放

把 `PolySub.app`（在本项目目录里）拖进 Dock：

- **把视频或文件夹拖到图标上**：按默认语言加入后台队列。
- **双击图标**：选视频，再选字幕语言。

开始和完成时各有系统通知。已有同语言字幕的跳过；同一个任务不会重复加入；同一时间只处理一个。

### 命令行

```bash
polysub video.mp4                       # 默认：自动识别原语言 → 简体中文
polysub -t zh-Hans,en video.mp4         # 同时出中文和英文（识别只做一次）
polysub --think off video.mp4           # 快速档
polysub --translate-endpoint DeepSeek --translate-model deepseek-flash video.mp4
polysub queue add -t en ~/Movies/某文件夹   # 整个文件夹放进后台队列
polysub queue list                      # 查看队列；queue retry ID 重试，queue clear 清理
polysub endpoints test DeepSeek         # 测试接口：列出模型并实际翻译一句
polysub doctor                          # 环境检查
polysub --help
```

## 配置

配置文件：`~/Library/Application Support/PolySub/config.toml`（`polysub config path` 查看）。第一次运行时自动生成，API Key 明文保存，文件权限为仅本人可读写。

**所有模型服务都是一份「接口配置」**（OpenAI 兼容：Base URL + API Key）。语音识别调用 `/v1/audio/transcriptions`，翻译调用 `/v1/chat/completions`，两步可以各选一个接口：

```toml
[asr]
endpoint = "本机 oMLX"
model = "Qwen3-ASR-1.7B-8bit"

[translate]
endpoint = "本机 oMLX"
model = "qwen3.8-27b-4bit"
think = "low"               # off 快速 | low 标准（默认）| medium 精细
think_budget = 1024         # 每批最多思考多少 token；0 = 不限（更准，约慢 3 倍）
fallback_endpoint = ""      # 被内容审核拒绝时改用的接口，例如 "本机 oMLX"

[[endpoints]]
name = "DeepSeek"
preset = "deepseek"
base_url = "https://api.deepseek.com"
api_key = "sk-..."
thinking = "deepseek"       # 各家关闭思考的参数写法不同，按预设自动填
concurrency = 4
```

预设：本机 oMLX、DeepSeek、阿里云百炼（通义千问）、OpenAI、Ollama、LM Studio、自定义。

### 用云端翻译

1. 申请 Key：[DeepSeek](https://platform.deepseek.com)、[阿里云百炼](https://bailian.console.aliyun.com)。
2. 填进配置文件对应 `[[endpoints]]` 的 `api_key`，运行 `polysub endpoints test DeepSeek` 确认可用。
3. 把 `[translate]` 的 `endpoint` / `model` 改成它（DeepSeek：`deepseek-flash`；百炼：`qwen3.8-max` 或 `qwen3.8-27b`），并把 `fallback_endpoint` 设为 `"本机 oMLX"`。

**注意**：两家都有内容审核，露骨内容大概率被拒；被拒的批次会自动改用备用接口，结束时会提示有几批。价格（2026-09-25 官方页面）：DeepSeek `deepseek-flash` 每百万 token 输入 $0.30、输出 $1.20（高峰价，非高峰半价）；百炼 `qwen3.8-max` 输入 ¥12、输出 ¥36，`qwen3.8-27b` 输入 ¥3、输出 ¥12。一部 2 小时的片子约几万 token。

## 流水线

```
视频 → PyAV 读音轨（16kHz 单声道；读不了时用系统 ffmpeg）
     → Silero VAD 切出说话片段（阈值 0.25，单段最长 8 秒）
     → 原语言设为 auto 时：抽 24 段识别，多数投票定语种，整片固定
     → 第一遍识别（逐段；本机并发 1，云端并发 4）
     → 翻译模型通读全片，写「翻译参考」（场景、人物、术语、同音误识别），并给出 3～6 个人名和称呼
     → 第二遍识别：带人名和称呼作提示；过滤纯语气词、提示词回声
     → 分批翻译：每批 20 行 + 翻译参考 + 前 5 行译文；要求返回 JSON，行数不对就重试，最后逐行补译
     → 超长行按标点折成两行，再长就按字数比例拆成两条；中文统一简体 / 繁体写法
```

识别结果和翻译参考按视频缓存在 `~/Library/Caches/PolySub/`；加一种语言、换翻译接口或档位重跑时直接复用（`--no-cache` 可禁用）。

## 实测（M4 Max 64GB，本机 oMLX，2 小时 17 分日语片）

| 翻译档位 | 全片耗时 | 说明 |
| --- | --- | --- |
| `low` + `think_budget 1024`（默认） | 9.6–11.3 分钟（两次实测，差别主要在翻译参考那一步） | 质量接近不限思考 |
| `low`，不限思考 | 约 28 分钟（按样本外推） | 最自然 |
| `off` | 约 5 分钟（按样本外推） | 新翻译实现下 off 也不再出现明显的反义错译，但个别句子较生硬 |

`think_budget` 低于约 700 时，模型思考被截断、JSON 容易出错，反而更慢（512 实测 318 秒 / 10 分钟样本）。

## 安装

```bash
./install.sh               # Python 环境（.venv）、命令链接、PolySub.app，最后做环境检查
./install.sh --with-model  # 另外下载语音识别模型到本机 oMLX（约 2.5GB）
```

需要 [uv](https://docs.astral.sh/uv/)。用本机模型时需要运行中的 [oMLX](https://github.com/jundot/omlx)，放进 `~/.omlx/models` 的新模型要在它的管理界面点 Reload。不需要 ffmpeg（PyAV 自带解码库），但系统里有 ffmpeg 时会用作兜底。

## 开发

```bash
.venv/bin/python -m unittest discover -s tests   # 单元测试，不需要模型
```

| 模块 | 作用 |
| --- | --- |
| `polysub/pipeline.py` | 主流程、缓存、进度事件 |
| `polysub/media.py` | 读音频（PyAV / ffmpeg 兜底） |
| `polysub/vad.py` | Silero VAD（取自 faster-whisper，MIT） |
| `polysub/asr.py` | 分段识别、语种检测、语气词和回声过滤 |
| `polysub/brief.py` | 翻译参考与识别提示词 |
| `polysub/translate.py` | 分批翻译 |
| `polysub/api.py` | OpenAI 兼容接口客户端：思考开关、重试、内容审核识别、备用接口 |
| `polysub/subtitle.py` | 折行、繁简统一、写 SRT / ASS / VTT |
| `polysub/jobs.py` | 持久化队列（CLI、拖放 App、以后的 GUI 共用） |
| `polysub/config.py` | TOML 配置与接口预设 |

运行时文件：配置 `~/Library/Application Support/PolySub/config.toml`，队列 `~/Library/Application Support/PolySub/queue.json`，缓存 `~/Library/Caches/PolySub/`，日志 `~/Library/Logs/PolySub/PolySub.log`。

## 已知局限

- VAD 会漏掉一些压在背景音乐下、多人同时说话的台词。
- 同音误识别只能部分修正（第二遍识别修人名、称呼；其他靠翻译按上下文猜）。
- 字幕时间轴来自 VAD 片段，不是逐词对齐；抽查在 0.5 秒以内。
- 只喊一个名字的单独一句（例如只有「部長。」）会被当成提示词回声过滤掉。
- 云端接口没有用真实 Key 测过；内容审核被拒时的备用路径已用模拟服务器测过。
