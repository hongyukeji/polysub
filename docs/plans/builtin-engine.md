# 内置引擎（开箱即用）计划

状态：**计划中**（2026-09-25 制定，同日按负责人要求补充需求与实测数据）。开发由 AI 代理执行，开工前先读 [计划目录与执行约定](README.md)（含隐私要求：公开仓库里不写本机路径、私人文件名、测试素材的名称或台词），再读本文件的「执行安排」。

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| U0 | 任务列表全选 / 取消全选（可独立先发） | 未开始 |
| S0 | 现有接口下的提速（不依赖内置引擎） | 未开始 |
| R0 | 选型实测 | 未开始 |
| R1 | 内置引擎运行时 + 模型管理 | 未开始 |
| R2 | 首次启动体验、设置分层 | 未开始 |
| R3 | 高级：自定义模型与调优 | 未开始 |
| R4 | 借鉴项（按收益排序，可选） | 未开始 |

## 负责人需求（2026-09-25）

1. 任务列表增加**全选**和**取消全选**。
2. **开箱即用**：安装后不需要另装 oMLX、Ollama，也不需要 API Key。
3. **速度优先，质量别太差**：默认配置以速度为先；质量底线见下。
4. **用网上成熟、热门的方案**，不自造轮子。
5. **高级用户可调优**：切换识别 / 翻译模型、接自己的接口、调思考与并发等参数。

**质量底线**（R0 用来判断默认档位是否合格，对照现有 oMLX Qwen3-ASR + 27B 基线）：不出现成段编造的字幕；整批 JSON 解析成功率 ≥ 99%（失败行会逐行补译，但不能频繁触发）；人工抽查 30 句，意思错误不超过基线的 2 倍；人名在全片保持一致。

## 现状实测：慢在哪里

负责人本机（M4 Max 64 GB），现有默认配置（oMLX：Qwen3-ASR-1.7B-8bit 识别，qwen3.8-27b-4bit 翻译，「标准」= 思考 low、每批上限 1024 token，并发 1），一部 **4 小时的日语视频，全流程 107.7 分钟**：

| 步骤 | 用时 | 占比 |
| --- | --- | --- |
| 读音轨 + VAD | 40 秒 | 1% |
| 第一遍识别 | 600 秒 | 9% |
| 全片参考（思考 low） | 238 秒 | 4% |
| 第二遍识别（带人名提示） | 632 秒 | 10% |
| **翻译** | **4943 秒（82 分钟）** | **77%** |

翻译共 58 次请求，输出 64,252 token（含思考），约 13 token/秒。结论：

- **大头是翻译，而翻译的大头是思考和大模型本身**。此前 10 分钟样片的数据：同一模型关思考快约 5 倍（224 秒 → 65 秒），代价是少量错句。
- **第二遍识别和第一遍一样贵**，只为修正人名和称呼。
- **本机 oMLX 并发为 1**，分批翻译只能串行；流水线已支持并行分批（`translate.py`，并发 > 1 时各批只带原文上下文），换成支持并发的服务即可直接受益。

## 目标

- **装完打开就能用**：不需要 oMLX、Ollama、API Key。首次使用只需等一次模型下载（带进度，可断点续传，国内走镜像）。
- **默认优先速度**：普通用户不用懂模型，默认配置在 16 GB 的 Apple Silicon 上跑得动、跑得快；8 GB 机器自动换小一档。
- **高级用户自选模型**：现有「接口」体系原样保留（oMLX、DeepSeek、百炼、OpenAI、Ollama、LM Studio、自定义），另外支持直接指定本机的 GGUF / whisper 模型文件。任务页「翻译质量」可以一键切到用户自己的高精度模型。
- **现有用户不受影响**：已有配置文件里的接口和选择保持不变，只改新装用户的默认值。
- **顺带解决 Windows W2**：同一套内置引擎在 Windows 上用 Vulkan / CUDA 版二进制，不再需要单独选型。

不做：视频配音（TTS）、实时字幕、说话人分离（R4 再评估）。

## 现状

- 识别和翻译都只走 OpenAI 兼容 HTTP 接口（`api.py` 的 `AsrClient` / `ChatClient`），默认指向本机 oMLX（`config.py` `default_config`）。没装 oMLX 的用户打开后什么都跑不了，必须先去配云端 Key。
- 「环境」页的模型下载只会往 `~/.omlx/models` 放模型，再让 oMLX 重新加载（`models.py`、`gui/environment.py`）。
- 流水线（VAD 切段 → 语言识别 → 第一遍识别 → 通读全片生成参考 → 带人名提示的第二遍识别 → 分批带上下文翻译）与后端无关，这是 PolySub 相对同类工具的核心优势，重构时要完整保留。

## 同类项目对比与借鉴

| | PolySub | [Buzz](https://github.com/chidiwilliams/buzz) | [pyVideoTrans](https://github.com/jianchang512/pyvideotrans) |
| --- | --- | --- | --- |
| 定位 | 视频 → 任意语言字幕，重翻译质量 | 离线转写 / 翻译工具 | 视频翻译 + 配音全流程 |
| 许可证 | MIT | MIT | **GPL-3.0** |
| 识别 | HTTP 接口（默认 oMLX Qwen3-ASR） | 内置 whisper.cpp（Metal / Vulkan）、faster-whisper、HF Transformers、OpenAI API | 内置 faster-whisper（推荐）、WhisperX、Parakeet；云端 Qwen3-ASR、火山等 |
| 翻译 | LLM，全片参考 + 上下文分批 + 被拒回退 | Whisper 自带翻译（仅译成英文）/ OpenAI 接口 | 渠道很多：Google、微软（免 Key）、DeepL、百度、腾讯、ChatGPT、DeepSeek、Ollama、离线 M2M100… |
| 开箱即用 | 否（要 oMLX 或 Key） | 是（界面里选模型、自动下载） | 是（Windows 整合包，免费翻译渠道默认可用） |
| 分发 | Homebrew、zip | DMG、Windows 安装包、Flatpak/Snap、PyPI | Windows 整合包、源码、Docker |

**可以借鉴的**（只借思路；Buzz 是 MIT，确实要复用代码时在 `THIRD_PARTY_NOTICES.md` 注明来源；**pyVideoTrans 是 GPL-3.0，不复制任何代码**，否则整个项目要改成 GPL）：

1. **内置 whisper.cpp（Buzz）**：Apple Silicon 上走 Metal，Windows 上走 Vulkan / CUDA，体积小、没有 PyTorch。这是本计划的核心。
2. **界面内模型管理（Buzz）**：列出可用模型、大小、是否已下载，一键下载 / 删除，模型放在应用数据目录。
3. **「翻译渠道」抽象与免 Key 机翻（pyVideoTrans）**：Google / 微软这类免费机翻几秒就能翻完一部片子，适合「只想看懂」的用户。代价见待定事项 2。
4. **词级时间戳重新断句（两者都有）**：Whisper 输出词级时间戳，可以按标点和停顿重排字幕行，比单纯按 VAD 片段切更自然。放在 R4。
5. **按硬件选档（pyVideoTrans 的 CUDA 检测、Buzz 的 CUDA 可选安装）**：按内存 / 显卡自动推荐模型档位。
6. **监视文件夹（Buzz）**：往某个文件夹里放视频就自动排队。和现有后台队列天然契合，放在 R4。

**PolySub 要保留的差异点**：全片通读生成参考、两遍识别修正人名、翻译被拒自动换接口、逐行对照的字幕编辑器、按视频缓存识别结果。

## 方案：本机模型服务（旁路进程）

App 里打包两个上游官方的本机服务程序，由 PolySub 在需要时启动，只监听 `127.0.0.1` 的随机端口：

- **`whisper-server`**（[whisper.cpp](https://github.com/ggml-org/whisper.cpp)，MIT）：负责识别。启动参数 `--inference-path /v1/audio/transcriptions`，让它的路径和现有 `AsrClient` 的调用对齐；接受 `prompt`，两遍识别的人名提示可以直接沿用。
- **`llama-server`**（[llama.cpp](https://github.com/ggml-org/llama.cpp)，MIT）：负责翻译，本身就提供 OpenAI 兼容的 `/v1/chat/completions`，现有 `ChatClient` 不用改。

**为什么用旁路进程，不用 Python 绑定**（pywhispercpp、llama-cpp-python、faster-whisper）：

- 现有代码全部面向「OpenAI 兼容接口」，内置引擎只是一个新的接口预设 `builtin`，流水线、缓存、编辑器零改动。
- 模型崩溃或显存不够时只死子进程，不会把界面和后台队列一起带走。
- 上游每周发版并提供 macOS arm64、Windows Vulkan / CUDA / CPU 预编译包，升级只换二进制；Python 绑定通常落后上游，PyInstaller 打包也更麻烦。
- 命令行和后台进程同样能用：谁需要谁启动，按端口文件复用同一个服务。

**为什么不用 faster-whisper 当 macOS 默认**：CTranslate2 在 Mac 上没有 Metal 加速，只能跑 CPU。它更适合作为 Windows NVIDIA 用户的备选，R0 在 Windows 上实测后再决定要不要加。

### 结构

```
src/polysub/
  engine/
    __init__.py
    manifest.py    内置模型清单：档位、下载地址（HF + 镜像）、大小、sha256、推荐内存
    runtime.py     启动 / 复用 / 健康检查 / 空闲退出 whisper-server、llama-server；端口与 PID 写进应用数据目录
    hardware.py    内存、芯片（macOS）/ 显卡（Windows）检测，推荐档位
  models.py        下载函数保留（已支持断点续传），增加 sha256 校验与镜像回退；oMLX 相关函数保留给高级用户
  config.py        新增预设 builtin；新用户默认 asr/translate 都指向「内置」
  api.py           AsrClient 兼容 whisper-server 的返回格式（verbose_json 里的语言字段）
  gui/environment.py → gui/models.py   「环境」页改成「模型」页：内置模型管理 + 环境检查
packaging/
  engines/fetch.sh 按固定版本号下载并校验上游二进制，放进 .app 的 Resources/engines/
```

### 接口与配置

- 新预设 `builtin`（显示名「内置（本机）」）：`base_url` 留空，运行时由 `runtime.ensure()` 填入实际端口；`thinking` 用 `chat_template_kwargs`（llama-server 支持的 Qwen 模板参数）。
- `asr.model` / `translate.model` 写档位名（如 `asr-turbo`、`mt-4b`），由清单映射到文件；高级用户可以写本机文件的绝对路径（R3）。
- 旧配置：`load()` 读到已有文件时不改任何字段。只有新建配置时才用内置默认值；如果检测到 `~/.omlx/settings.json`（说明用户装了 oMLX），新配置仍默认 oMLX，保持现有体验。

### 默认档位（初稿，R0 实测后定）

| 档位 | 识别 | 翻译 | 适合 | 首次下载 |
| --- | --- | --- | --- | --- |
| 轻量 | Whisper large-v3-turbo 量化版 | Qwen 系 1.7B 级指令模型 Q4 | 8 GB 内存 | 约 1.6 GB |
| 标准（默认） | Whisper large-v3-turbo 量化版 | Qwen 系 4B 级指令模型 Q4 | 16 GB 及以上 | 约 3 GB |
| 高精度 | 用户自己的接口或模型（oMLX Qwen3-ASR + 大模型、云端等） | 同左 | 高级用户 | — |

表中模型名和大小只是 R0 的候选范围，不是结论。选型要求：多语言（至少中英日韩及主要欧洲语言）、可商用许可、有官方或社区 GGUF / ggml 量化版。内置档位默认关闭思考（速度优先），任务页「翻译质量」的「标准 / 精细」对内置模型开启少量思考。

## 执行安排

| 阶段 | 分支 | 依赖 | 在哪里做 | 涉及文件（并行时互不重叠） | 发布 |
| --- | --- | --- | --- | --- | --- |
| U0 | `engine/u0` | 无 | 云端或本机 | `gui/tasks.py`、`tests/` | 0.3.3 |
| S0 | `engine/s0` | 无 | 代码云端或本机；用时对比在负责人 Mac 上 | `config.py`、`pipeline.py`、`asr.py`、`translate.py`、`gui/settings.py`、`gui/widgets.py`、`tests/` | 0.4.0（可与 U0 合并发布） |
| R0 | `engine/r0` | 无（可与 U0、S0 同时进行） | **负责人 Mac 本地**（需要 Apple GPU） | 只写 `docs/plans/builtin-engine.md` 的 R0 小节和 `scripts/bench/`（测评脚本，不含素材） | 不发布 |
| R1 | `engine/r1` | R0 定档、S0 已合入 | 代码云端或本机；验收在负责人 Mac 上 | `engine/`、`models.py`、`config.py`、`api.py`、`pipeline.py`、`cli.py`、`packaging/`、`.github/workflows/`、`THIRD_PARTY_NOTICES.md` | 与 R2 一起发 0.5.0 |
| R2 | `engine/r2` | R1 | 同上 | `gui/`、`README.md`、`README.en.md` | 0.5.0 |
| R3 | `engine/r3` | R2 | 同上 | `engine/`、`gui/`、`config.py` | 0.6.0 |
| R4 | 每项一个分支 | R2 | 按项目 | 按项目 | 按项目 |

- U0、S0、R0 可以交给三个代理同时做；R1 起按顺序进行。
- R0 的对照基线：负责人本机的私有样片跑现有默认配置得到的结果，只在本机使用；写进计划的只有数据和中性描述（语言、时长、对白密度），不写文件名和内容。可复现的部分用公开授权视频（多语言，至少含中、英、日对白）。
- 每个阶段合入后由负责人决定是否发布；代理只准备 `docs/releases/版本号.md` 草稿和 CHANGELOG。

## 分阶段计划

### U0：任务列表全选 / 取消全选

- 任务页底部按钮栏最左侧增加「全选」「取消全选」；快捷键 ⌘A / Esc（Windows 为 Ctrl+A / Esc）；右键菜单同样提供。
- 没有选中项时，「取消所选」「重试所选」「移除所选」置灰；列表刷新（每秒轮询）时保持当前选中项不丢失。
- 测试：选择状态在刷新后保持；全选后「移除所选」对正在处理的任务仍按现有规则提示。
- 可以不等后续阶段，单独发一个补丁版本。

### S0：现有接口下的提速（不依赖内置引擎）

先把与引擎无关、立刻见效的提速做掉，同时给 R0 提供对照数据：

- **新用户默认翻译质量改为「快速」（关思考）**；「标准」「精细」保留给要质量的人。已有配置文件里的选择不变。全片参考仍用 low（关思考时会编造，见手册数据）。
- **第二遍识别改为按需**：只重识别第一遍结果里出现人名 / 称呼候选、或疑似同音错误的片段，而不是全部重跑；「快速」档可关闭第二遍。实测对比全重跑与按需的用时和人名修正率。
- **更大的批**：「快速」档每批 20 行 → 40 行（此前实测 VideoCaptioner 下 40 行略快、质量相当），减少请求次数与重复的参考文本。
- **oMLX 并发**：在接口里可设并发数；实测 oMLX 并发 2 / 4 对整片用时和内存的影响（只在 PolySub 的接口配置里改，不改 oMLX 本身的全局设置）。
- 验收：同一部视频在新默认下的全流程用时、各步骤占比，与上表对照，写进本节。

### R0：选型实测

- 用公开授权的测试素材（多语言，含日语对白），对比现有 oMLX Qwen3-ASR + 大模型基线：
  - 识别：whisper.cpp 的 large-v3-turbo（几种量化）、large-v3，看准确率、编造、漏识别、日语效果、速度；确认 `prompt`、指定语言、自动识别语言的返回值在 whisper-server 上的实际行为。
  - 识别对照：Apple Silicon 上热门的 [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper)（同一 turbo 模型），只作速度参照；若比 whisper.cpp Metal 明显更快，再评估 macOS 上改用它。
  - 翻译：2–3 个 1.7B–8B 级候选（通用指令模型如 Qwen 系，以及专门的翻译模型如 Hunyuan-MT 系——此前实测 Hy-MT2-7B 比 27B 快约 20 倍但错译偏多），看 JSON 格式成功率（现有 `_parse` 要求一次返回整批）、人名一致性、速度。
  - 并发：llama-server `--parallel 1/2/4`（连续批处理）下整片翻译用时与内存。
  - 机器：M4 Max 64 GB（开发机），另找或模拟一台 8/16 GB 机器（限制 llama-server 的 `--ctx-size` 与并行度，按内存占用推算）。
- 输出：档位表定稿、各档位 2 小时片子全流程用时、与基线的质量差距，写进本文件 R0 小节。
- 验收：数据齐全，负责人确认默认档位。

### R1：内置引擎运行时 + 模型管理

- `packaging/engines/fetch.sh`：固定上游版本号和 sha256，下载 macOS arm64 的 `llama-server`、编译或下载 `whisper-server`（whisper.cpp 不一定发 macOS 预编译服务端，没有就在 CI 里 cmake 编译并缓存），放进 `.app/Contents/Resources/engines/`；`polysub.spec` 与 `build.sh` 相应修改。开发环境下从 `~/.cache` 或 PATH 查找。
- `engine/runtime.py`：
  - `ensure(kind, model_path) -> base_url`：已有同模型的服务就复用，否则选随机端口启动，等健康检查通过（whisper-server 用一次空请求，llama-server 用 `/health`）。
  - 端口、PID、模型写进 `应用数据目录/engines/<kind>.json`，加 filelock，界面进程和后台队列进程共用一个服务。
  - 空闲 10 分钟退出释放内存；App 退出时不强杀后台队列正在用的服务。
  - 识别和翻译分阶段进行，内存紧张的档位先停识别服务再起翻译服务。
- `manifest.py` + 下载：HF 官方地址，失败或用户选择时走 `hf-mirror.com`（`HF_ENDPOINT` 已支持，改成界面里可选「下载源：自动 / 官方 / 国内镜像」）；下载完校验 sha256。
- `config.py` 增加 `builtin` 预设和新默认值；`pipeline.py` 在建客户端前对 `builtin` 接口调用 `runtime.ensure()`。
- CLI：`polysub models list|download|remove`；`polysub doctor` 显示内置引擎状态。
- 测试：runtime 用假服务脚本测启动、复用、并发加锁、崩溃重启；清单与下载用本地 HTTP 服务测校验和断点续传。
- 验收：删掉 oMLX 配置的新用户，`polysub models download` 后命令行跑通样片；后台队列与界面同时跑时只有一个服务实例。

### R2：首次启动体验、设置分层

- 首次打开（没有模型时）弹出引导：检测内存 → 推荐档位（可改）→ 选择下载源 → 下载进度（可暂停、后台继续）→ 完成后直接进入任务页。拖入视频时模型还没下好，任务排队等待，不报错。
- 「环境」页改为「模型」页，参考 Buzz：内置模型列表（名称、用途、大小、状态、下载 / 删除），下方保留环境检查与文件位置。oMLX 一键下载移到「高级」里。
- 设置分两层：
  - 常用：目标语言、字幕格式、双语、翻译质量（快速 / 标准 / 精细 / 我的模型）。
  - 高级（折叠）：接口列表、识别与翻译分别选接口和模型、思考参数、备用接口、VAD 参数。
- 任务页「翻译质量」增加「我的模型」：切到用户在高级设置里配好的识别 / 翻译组合；没配时提示去设置。
- README（中英）改写「系统要求」「模型与接口」：默认不需要任何额外软件；oMLX、云端改为「进阶：使用自己的模型」。
- 验收：干净 macOS 用户账户下，从 Homebrew / zip 安装到拿到第一份字幕，全程不需要打开设置；已有用户升级后配置和行为不变。

### R3：高级：自定义模型与调优

- 内置引擎可以加载用户自己的文件：翻译选任意 GGUF，识别选任意 ggml whisper 模型（界面「添加本机模型文件…」或填 HF 仓库名自动下载）。
- 每个自定义模型可调：上下文长度、GPU 层数、并行数、思考开关格式。
- 「高级」设置里开放流水线参数：每批行数、上下文行数、翻译并发、思考档位与上限、第二遍识别（关 / 按需 / 全部）、VAD 阈值与单句最长时长；每项给出默认值和一句话说明，提供「恢复默认」。
- 任务页可按任务选择档位（快速 / 标准 / 精细 / 我的模型），同一视频换档位重跑时复用识别缓存，只重做翻译。
- 接口页保持现状，补一个「测试」按钮：实际发一小段音频 / 一批字幕，报告能否解析、速度多少。
- 验收：用一个非清单内的 GGUF 模型和 oMLX Qwen3-ASR 各跑通一次样片。

### R4：借鉴项（可选，逐项评估）

按预期收益排序，每项单独开 PR：

1. **免 Key 机翻渠道**（取决于待定事项 2）：作为「极速」档，不做全片参考和上下文，只做逐批直译；明确标注会把字幕文本发给第三方。
2. **词级时间戳断句**：whisper-server 返回词级时间，按标点和停顿重新切行，限制每行字数与时长。需要对比 VAD 切段的效果再决定是否默认开启。
3. **监视文件夹**：设置里选一个文件夹，新出现的视频自动进后台队列。
4. **Windows NVIDIA 用户的 faster-whisper**：只有 R0 / W2 数据证明明显更快时才加。

## 对 Windows 计划的影响

- W2「内置本机识别」直接用本方案：同一套 `engine/`，Windows 上打包 `llama-server` / `whisper-server` 的 Vulkan 版（覆盖 NVIDIA、AMD、Intel 显卡），CUDA 版作为可选下载。原方案 A/B/C 的对比缩减为「Vulkan vs CUDA vs CPU」的速度实测。
- W1 的默认接口问题随之消失：两个平台默认都是「内置」。
- 建议顺序：先做 R0–R2（macOS），再恢复 Windows 计划，W1 与 W2 合并。

## 风险

- **质量下降**：默认从 Qwen3-ASR + 27B 大模型换成 Whisper + 4B 级小模型，质量肯定不如现在的本机高配方案。缓解：默认档位定位是「快、够看」，任务页一键切「我的模型」；R0 用数据说话，差距过大就把默认改成 8B 级。
- **Whisper 编造内容**：静音和音乐段容易出现「谢谢观看」之类的幻觉。现有 VAD 切段已经规避了大部分，回声过滤可以扩展为 Whisper 常见幻觉句过滤，R0 专门统计。
- **小模型 JSON 不稳**：批量翻译要求一次返回整批 JSON。缓解：llama-server 支持 JSON 模式 / 语法约束（`response_format` 或 `grammar`），R1 对内置接口打开；批大小按档位调小。
- **首次下载量**：约 1.6–3 GB，国内直连 HF 很慢。缓解：镜像、断点续传、下载期间允许排队。
- **包体积与签名**：二进制约几十 MB；打进 .app 后要一起签名（ad-hoc），并确认 Homebrew 解压后仍能运行；首次启动子进程不能触发额外的 Gatekeeper 提示。
- **端口与多进程**：界面、后台队列、命令行可能同时启动服务。用端口文件 + 文件锁串行化，R1 测试覆盖。
- **许可证**：whisper.cpp、llama.cpp 为 MIT；默认模型必须选可商用许可，写进 `THIRD_PARTY_NOTICES.md`（模型不打进包，但清单里默认下载的也要注明）。

## 待定事项

负责人未另行指示前，代理按每项的「建议」执行（第 1 项已定）。


1. **默认档位**：负责人已定方向——**速度优先、开箱即用，接受默认质量低于现在的 oMLX 高配方案**，底线见「负责人需求」。标准档用 4B 级还是 8B 级，由 R0 数据按底线决定。
2. **免 Key 机翻**：是否提供 Google / 微软免费机翻作为「极速」档？优点是秒出、不用下载；缺点是非官方接口随时可能失效、没有上下文（人名、语气不一致）、字幕文本会发到第三方服务。建议：不做默认，只作为可选渠道放在 R4。
3. **首次启动是否自动开始下载**：自动下载（最省事）还是先展示档位让用户点「开始」（更透明，推荐）。
4. **已装 oMLX 的新用户**：默认用内置还是 oMLX？建议沿用 oMLX（质量更高，且用户显然有意为之），内置作为备选。
