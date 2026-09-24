# PolySub

本机视频 → 任意语言字幕。语音识别全在本机做（Qwen3-ASR，经 oMLX）；翻译可以用本机模型，也可以用 DeepSeek、阿里云百炼（通义千问）的云端 API。输出 `视频名.<语言>.srt`，放在视频旁边，IINA 等播放器会自动加载。

- 项目目录可以随意移动，移动后运行一次 `./install.sh` 即可。
- 选型过程和实测数据由维护者另行记录，不在仓库中。

## 用法

### 拖放（最方便）

把 `PolySub.app`（在本项目目录里）拖进 Dock。以后：

- **把视频或文件夹拖到 PolySub 图标上**：按默认语言（`config.sh` 里的 `POLYSUB_TO`）加入队列。
- **双击 PolySub**：选视频（可多选），再选字幕语言。

之后在后台逐个处理，开始和完成时各有系统通知。已经有同语言字幕的视频会跳过；同一个视频重复拖入不会处理两次；同一时间只处理一个。日志在 `~/Library/Logs/PolySub.log`。

### 命令行

```bash
polysub video.mp4                    # 默认：日语 → 简体中文，本机模型
polysub -t en video.mp4              # 译成英文，输出 video.en.srt
polysub -f ko -t zh-Hans video.mp4   # 韩语片
polysub -p deepseek video.mp4        # 翻译改用 DeepSeek
polysub -p qwen --think medium video.mp4
polysub-queue -t en ~/Movies/某文件夹   # 后台排队，整个文件夹
polysub --help
```

语言代码（BCP 47）：`zh-Hans` 简体、`zh-Hant` 繁体、`en`、`ja`、`ko`、`fr`、`de`、`es`、`ru`、`pt`、`it`、`th`、`vi`、`id`、`ar` 等 38 种，完整列表见 VideoCaptioner 的 `cli/commands/subtitle.py`。

## 流水线

```
视频 → ffmpeg 抽 16kHz 单声道
     → Silero VAD 切出说话片段（阈值 0.25，单段最长 8 秒）
     → 第一遍识别：Qwen3-ASR-1.7B（oMLX，逐段）
     → 翻译模型通读全片，写「翻译参考」（场景、人物、术语、同音误识别），并给出 3～6 个人名和称呼
     → 第二遍识别：带人名和称呼作提示，修正同音误识别
     → VideoCaptioner：断句 + 翻译（带翻译参考），只输出译文
     → 中文目标时用 OpenCC 统一简体 / 繁体写法
```

## 翻译模型怎么选

| provider | 速度（2 小时 17 分日语片，实测） | 费用 | 注意 |
| --- | --- | --- | --- |
| `local`（默认），本机 Qwen3.8-27B，思考 off | 约 10 分钟，其中翻译约 5 分钟 | 免费 | 处理期间别跑本机 Coding Agent，会互相拖慢 |
| `local`，思考 medium | 翻译一步约慢 5 倍 | 免费 | 译文最准 |
| `deepseek`，deepseek-flash | 未实测（没有 Key） | 每百万 token 输入 $0.30、输出 $1.20（高峰价；非高峰半价） | **有内容审核**，露骨内容大概率被拒 |
| `qwen`，qwen3.8-max / qwen3.8-27b | 未实测（没有 Key） | max：每百万 token 输入 ¥12、输出 ¥36；27b：输入 ¥3、输出 ¥12 | **有内容审核**，同上 |

**云端被拒怎么办**：默认 `POLYSUB_FALLBACK_LOCAL=1`，被内容审核拒绝的那一批会自动改用本机模型翻译，不会整片失败。结束时会提示有几批用了本机。已用模拟的拒绝响应测过这条路径；真实云端没有测。

**费用估算**：一部 2 小时的片子，翻译加翻译参考大约用几万 token，按上表是几分钱到一两毛钱人民币。

## 开通云端 API

1. **DeepSeek**：到 <https://platform.deepseek.com> 注册、充值，创建 API Key。
2. **通义千问（阿里云百炼）**：到 <https://bailian.console.aliyun.com> 开通百炼，创建 API Key。
3. 把 Key 写进 `config.sh`（不进 Git，权限 600）：

   ```bash
   DEEPSEEK_API_KEY="sk-..."
   DASHSCOPE_API_KEY="sk-..."
   ```

4. 用 `-p deepseek` / `-p qwen` 运行；想默认用云端，把 `config.sh` 里的 `POLYSUB_PROVIDER` 改成对应值。

新加坡等海外区域的百炼账号，把 `POLYSUB_QWEN_BASE` 改成该区域的地址（例如 `https://dashscope-intl.aliyuncs.com/compatible-mode`）。

## 安装

```bash
./install.sh               # Python 环境、config.sh、命令链接、PolySub.app
./install.sh --with-model  # 另外下载语音识别模型到 oMLX（约 2.5GB）
```

需要：macOS、Homebrew（装 ffmpeg）、[uv](https://docs.astral.sh/uv/)、本机运行的 [oMLX](https://github.com/jundot/omlx)（语音识别用，默认 `http://127.0.0.1:8888`）。模型放进 `~/.omlx/models` 后，要在 oMLX 管理界面点一次 Reload，它才会发现新模型。

`install.sh` 首次运行会从 `~/.omlx/settings.json` 读取 oMLX 的 API Key 写进 `config.sh`。

## 目录

| 路径 | 作用 |
| --- | --- |
| `bin/polysub` | 主流程：单个视频 → 字幕 |
| `bin/polysub-queue` | 后台队列，拖放 App 调用它 |
| `lib/asr_vad.py` | VAD 切分 + 逐段识别，过滤纯语气词和提示词回声 |
| `lib/context_note.py` | 生成翻译参考和识别提示词；各目标语言的翻译要求也在这里 |
| `lib/llm_proxy.py` | 本地转发：填入各家的 Key、模型和思考开关；云端拒绝时改用本机 |
| `app/PolySub.applescript` | 拖放 App 源码，`install.sh` 编译成 `PolySub.app` |
| `config.example.sh` | 配置模板；复制成 `config.sh` 修改 |
| `requirements.txt` | 实测过的依赖版本 |

运行时文件：队列 `~/Library/Application Support/PolySub/`，日志 `~/Library/Logs/PolySub.log`，命令链接 `~/.local/bin/polysub`、`~/.local/bin/polysub-queue`。

## 已知局限

- VAD 会漏掉一些压在背景音乐下、多人同时说话的台词。
- 思考 off 时偶尔有意思翻错的句子（样本里约 47 句错 2～4 句），也偶尔会把翻译参考里的字混进字幕行。重要的片子用 `--think medium`。
- 字幕时间轴来自 VAD 片段（两端各留 0.2 秒余量），不是逐词对齐；抽查 6 句都在 0.5 秒以内。
- VideoCaptioner 有自己的 LLM 缓存（`~/Library/Application Support/videocaptioner/cache`），缓存不区分思考档位，所以每次运行前会清空它。
