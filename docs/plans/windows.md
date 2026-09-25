# Windows 版计划

状态：**计划中，未开工**（2026-09-25 制定）。开发由 AI 代理执行，开工前先读文末「给执行代理的说明」；「待定事项」中与当前阶段相关的项要先由项目负责人确认。

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| W0 | Windows CI | 未开始 |
| W1 | Windows 版可用（云端 / 外部接口） | 未开始 |
| W2 | 内置本机识别 | 未开始，依赖待定事项 2 |
| W3 | Scoop 分发 | 未开始 |

## 目标

- Windows 10/11 x64 用户装完就能用：拖入视频，得到任意语言字幕，体验与 macOS 版一致。
- 同一套源码、同一个版本号、同一次打标签发布出 macOS 和 Windows 两个发行包。
- 不动 macOS 版的现有行为；两个平台共用的逻辑只维护一份。

不做：Windows ARM64（用户少，先看需求）、Windows 7/8、Linux 桌面版（顺带能跑命令行，但不单独打包）。

## 现状评估

核心约 3300 行 Python，开发时已按跨平台写：

| 部分 | Windows 上 | 说明 |
| --- | --- | --- |
| 配置、队列、缓存、日志路径 | 可用 | platformdirs 自动落到 `%APPDATA%`、`%LOCALAPPDATA%` |
| 音频解码 PyAV、VAD（onnxruntime）、pysubs2、OpenCC | 可用 | 都有 Windows x64 wheel |
| 翻译、分批、重试、备用接口、思考参数 | 可用 | 纯 HTTP，与平台无关 |
| 后台进程、队列锁、取消与暂停 | 基本可用 | `start_background` 已有 `DETACHED_PROCESS` 分支；filelock 跨平台；需在真机验证 |
| 打开文件、在资源管理器中显示 | 可用 | `widgets.open_file` / `reveal` 已有 win32 分支 |
| 系统通知 | **要改** | `jobs.notify` 只有 osascript |
| 界面程序兼作命令行 | **要改** | Windows 的窗口程序（`console=False`）不能向终端输出 |
| `polysub install`、菜单「安装命令行工具」 | **要改** | 复制 .app 与符号链接是 macOS 做法；Windows 由安装包负责 |
| 打包 | **要新写** | 现有 spec 只打 .app |
| 「环境」页 | **要改** | 检查项和模型下载围绕 oMLX |
| 默认接口 | **要改** | 默认本机 oMLX；oMLX 基于 Apple MLX，Windows 上没有 |
| 本机语音识别 | **关键难点** | 见下一节 |

## 关键难点：Windows 上的语音识别

macOS 上识别走本机 oMLX 的 `/v1/audio/transcriptions`（Qwen3-ASR-1.7B）。Windows 上：

- **翻译**没有问题：Ollama、LM Studio 有 Windows 版且兼容 OpenAI，预设已内置；显存不够的用户用 DeepSeek、百炼。
- **识别**没有现成的「装完即用」服务：Ollama、LM Studio 不提供转写接口。

候选方案（W2 阶段实测后再定，不凭印象选）：

| 方案 | 做法 | 优点 | 代价与风险 |
| --- | --- | --- | --- |
| A. 内置 faster-whisper | 进程内跑 CTranslate2；有 NVIDIA 显卡用 CUDA，否则 CPU int8 | 装完即用；Windows 上成熟；CPU 也能跑 | CUDA 运行库让安装包大幅变大（可做成按需下载）；Whisper 易编造内容，依赖现有 VAD 切段和两遍识别抑制，需实测 |
| B. 内置 Qwen3-ASR（PyTorch） | 进程内跑与 macOS 相同的模型 | 两个平台识别质量一致，调好的参数可直接沿用 | PyTorch + CUDA 体积最大（GB 级）；CPU 速度可能不可用；需确认 Windows 上的依赖是否齐全 |
| C. 内置 sherpa-onnx | ONNX 模型，CPU/DirectML | 体积小、不依赖 CUDA | 可用模型与质量待评估 |
| D. 外部服务 | 用户自己跑兼容服务（如 speaches）或用云端（OpenAI、Groq 等的 Whisper 接口） | 代码几乎不用改，现有接口配置直接支持 | 本机方案门槛高；云端要 Key、要花钱、音频要上传 |

D 在 W1 就天然支持（只是接口配置）。W2 从 A、B、C 里按实测数据选一个作为默认内置方案。

实现上把识别抽象成两类后端：现有的「HTTP 接口」和新增的「内置引擎」，`asr.py` 对上层接口不变，两遍识别、回声过滤、繁简转换照旧复用。

## 分阶段计划

### W0：准备

- 确认测试环境（见待定事项 1）。
- GitHub Actions 加 `windows-latest` 任务：`uv sync` + 单元测试，先看哪些测试在 Windows 上失败（路径分隔符、文件锁、编码）。
- 验收：Windows 上单元测试全绿。

### W1：Windows 版可用（云端 / 外部接口）

代码：
- **通知**：`jobs.notify` 改为平台分派；Windows 用 PowerShell 调系统 Toast，失败时静默（后台进程没有界面，不能依赖 Qt 托盘）。
- **两个可执行文件**：同一份代码打出 `PolySub.exe`（窗口程序，无参数开界面）和 `polysub.exe`（控制台程序，命令行与后台进程）。后台进程由 `polysub.exe` 以隐藏窗口方式启动。`entry.py` 按可执行文件名决定默认行为。
- **安装相关命令**：`polysub install` 与菜单「安装命令行工具」只在 macOS 显示；Windows 上由安装包处理。
- **默认配置**：`default_config` 按平台选默认接口；Windows 上默认翻译接口 Ollama（可在「接口」页切换云端），识别接口待 W2 定默认，W1 期间引导用户选云端或外部服务。
- **「环境」页**：oMLX 相关检查与下载只在 macOS 显示；Windows 显示各接口连通性、显卡信息（有无 NVIDIA、显存）。
- **路径与编码**：检查中文路径、长路径、带空格路径的视频；日志和配置统一 UTF-8。
- **界面**：Windows 默认字体（Microsoft YaHei UI / Segoe UI）、深浅色模式、控件间距；Esc、Ctrl 快捷键与 macOS 的 Cmd 对应。

打包：
- `packaging/windows/polysub.spec`（PyInstaller，x64，两个 EXE 共用一个 COLLECT 目录）、`packaging/windows/PolySub.ico`（由现有图标生成）。
- `packaging/windows/installer.iss`（Inno Setup）：装到 `%LOCALAPPDATA%\Programs\PolySub`（无需管理员）、开始菜单快捷方式、把 `polysub.exe` 所在目录加入用户 PATH、资源管理器右键「用 PolySub 生成字幕」（视频文件和文件夹）、卸载时可选保留设置与缓存。
- 发行包：`PolySub-版本-windows-x64-setup.exe` 与免安装的 `PolySub-版本-windows-x64.zip`，写入同一个 `SHA256SUMS`。

发布：
- `release.yml` 拆为 macOS、Windows 两个构建任务，最后一个任务汇总产物、生成 `SHA256SUMS`、创建草稿。
- `docs/releases/TEMPLATE.md`、README 增加 Windows 下载与安装说明；「常见问题」增加 SmartScreen 提示的处理。

验收：
- GitHub Windows 服务器上：单元测试、打包、`polysub --version`、`polysub doctor`、用 mock 接口跑通一个短视频。
- 真机上：安装、开始菜单启动、拖入视频、后台处理、关窗后继续、取消（1 秒内生效）、暂停、通知、字幕编辑器、右键菜单、卸载；中文路径视频；用云端接口完整跑一部片子。

### W2：内置本机识别

- 在同一批测试片段（公开授权的素材）上实测 A/B/C：识别准确率（对照现有 Qwen3-ASR 结果）、编造与漏识别、时间轴偏移、速度（CPU、NVIDIA 各一组）、安装体积、首次下载量。
- 选定默认方案后实现「内置引擎」后端；大文件（模型、CUDA 运行库）不进安装包，由「环境」页一键下载（沿用 macOS 已有的断点续传），无 NVIDIA 显卡时自动用 CPU。
- 按实测重新调 VAD 阈值、单句时长、两遍识别的提示方式（Whisper 系用 `initial_prompt`）。
- 验收：与 macOS 版同一部片子对比，结果写进本文件；默认参数下全片用时、质量有数据支撑。

### W3：分发与自动更新

- Scoop 安装源 `hongyukeji/scoop-bucket`：和 Homebrew tap 相同思路，定时读取最新已公开 Release 更新清单，不需要 token。用户命令 `scoop bucket add hongyukeji https://github.com/hongyukeji/scoop-bucket` → `scoop install polysub`。
- 视情况提交到 winget（需要审核，时间不可控，放最后）。
- 代码签名：不签名时 SmartScreen 会提示，文档说明「更多信息 → 仍要运行」；是否购买签名证书另行决定（有持续费用）。
- 验收：Scoop 安装、升级、卸载在真机跑通；发布流程文档更新。

## 目录与文件变化（预计）

```
packaging/
  macos/…                  不变
  pyinstaller/entry.py     按可执行文件名分派界面 / 命令行
  windows/
    polysub.spec           新增
    installer.iss          新增
    PolySub.ico            新增
    build.ps1              新增：构建 + 打安装包
src/polysub/
  system.py                新增：通知、默认接口、显卡检测等平台差异集中在这里（不叫 platform.py，避免和标准库同名）
  asr.py                   增加内置引擎后端（W2）
  engines/                 新增（W2）：选定的内置识别引擎
  jobs.py / cli.py / config.py / gui/app.py / gui/environment.py   按平台分派
.github/workflows/
  ci.yml                   增加 windows-latest
  release.yml              macOS + Windows 构建，汇总发布
```

## 风险

- **识别质量**：若 Windows 默认识别换成 Whisper 系，质量可能不如 macOS 版；W2 用数据决定，不达标就优先方案 B。
- **安装体积**：CUDA 相关文件以 GB 计，必须按需下载，否则安装包不可接受。
- **SmartScreen / 杀毒软件误报**：PyInstaller 打包的未签名程序偶尔被误报；需要在真机和 VirusTotal 上检查。
- **无法在本机验证**：开发机是 Mac，界面、显卡与安装流程必须在 Windows 真机或虚拟机上验收，GitHub 服务器只能覆盖测试、打包和命令行。
- **两个平台的回归**：每次改动都要在两个平台跑 CI；共享逻辑保持在平台无关的模块里。

## 待定事项

1. **测试环境**：有没有 Windows 电脑？是否 NVIDIA 显卡、多少显存？没有的话用 UTM 装 Windows 11 虚拟机验收界面，显卡相关只能靠用户反馈。
2. **识别定位**：Windows 版是否必须「装完即可本机识别」（做 W2），还是先只支持云端与外部服务（W1 即发布）。
3. **默认翻译接口**：Windows 上默认 Ollama 还是云端（DeepSeek / 百炼）。
4. **代码签名**：是否购买证书消除 SmartScreen 提示。

## 给执行代理的说明

项目负责人：[@hongyukeji](https://github.com/hongyukeji)。以下是本仓库的约定，执行本计划时照做。

**仓库与分支**
- 源码 `hongyukeji/polysub`。每个阶段一个分支（`windows/w0`、`windows/w1`…），通过 PR 合入 `main`。
- 提交身份用仓库/全局 git config（鸿宇 `<hongyusvip@gmail.com>`），不要用 `-c user.email=…` 覆盖成别的邮箱。
- **不要**自行推送 `v*` 标签、公开 Release、新建 GitHub 仓库（如 scoop-bucket）、购买任何服务；这些要负责人确认。

**开发**
- 环境：`uv sync --extra gui`；测试：`uv run python -m unittest discover -s tests`；macOS 打包：`packaging/macos/build.sh`。
- 代码风格跟现有代码一致：界面文案中文（经 `tr()`），代码注释英文，注释密度与周围一致；平台差异集中到 `src/polysub/system.py`，不要在各处散落 `sys.platform` 判断。
- **不改变 macOS 版的行为**。macOS 的 CI、打包、Homebrew 安装（配方在 `hongyukeji/homebrew-tap`，把 .app 以 zip 放在 libexec、安装后解压以保持签名）都必须保持可用。
- 新增依赖要写进 `pyproject.toml` 并更新 `uv.lock`；打进发行包的第三方组件写进 `THIRD_PARTY_NOTICES.md`（许可证、来源）。

**实测优先**
- 关于速度、质量、体积的结论只认实际跑出来的数据，不引用估算或上游宣传数字；写结论时注明机器、样片、参数。
- 基线数据：macOS 版在 M4 Max 上，默认参数下一部 2 小时 17 分的视频全流程 9.6–11.3 分钟。
- 测试素材用公开授权的视频（例如 Blender 开放电影）或测试中临时生成的音频；需要模拟云端接口时自己写一个小的模拟服务。**不要把视频提交进仓库。**
- **隐私**：本仓库是公开的。不要在代码、测试、文档、提交说明里写入本机路径、私人文件名、测试素材的名称或台词、任何密钥。

**文档与发布**
- 用户可见的变化写进 `CHANGELOG.md`（英文）和 README（中文 `README.md` 与英文 `README.en.md` 同步修改）。
- 发布遵循 [`docs/releases/README.md`](../releases/README.md)：先写 `docs/releases/版本号.md`，打标签生成草稿，验证后由负责人公开。
- 每完成一个阶段，更新本文件顶部的状态表，并在对应阶段下写明实际做法与计划的偏差、验收结果（通过/未覆盖项）。
