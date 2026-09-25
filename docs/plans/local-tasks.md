# 需要在负责人 Mac 上做的事

云端代理已经把能在云端完成的代码、测试和文档都合入 `main`（2026-09-25）。下面这些必须在负责人的 Apple Silicon Mac 上做：要 Apple GPU、本机 oMLX、真实模型、私人样片，或者要负责人拍板。按建议顺序排列，每项写明做什么、怎么做、做完写到哪里。

执行前先读 [计划目录与执行约定](README.md)（隐私：私人样片的文件名、内容、本机路径都不进仓库，计划里只记数字和中性描述）。

## A. 先验证能跑（半天）

1. **编译内置引擎并打包**
   - `packaging/engines/fetch.sh --dev`（给源码运行用），`packaging/macos/build.sh`（打 .app，会先编译到 `build/engines/bin`）。
   - 确认：Metal 版编译通过；`.app/Contents/Frameworks/engines/`（或 Resources）里有 `whisper-server`、`llama-server`；`codesign --verify --deep --strict` 通过；双击打开后子进程不触发额外的 Gatekeeper 提示；用 Homebrew 方式（zip 解压到 libexec）安装后也能启动引擎。
   - 不通过时改 `packaging/engines/fetch.sh`、`polysub.spec`，结果写进 [builtin-engine.md](builtin-engine.md) R1 小节。
2. ~~模型清单核对~~：已在云端通过 GitHub Actions「Pin models」完成，三个文件都存在，大小和 sha256 已写进 `manifest.py`。R0 换模型后再手动运行一次该流程。
3. **新用户流程**（R2 验收）：新建一个 macOS 用户（或临时 `POLYSUB_CONFIG`、`POLYSUB_MODELS` 指到空目录，并暂时移走 `~/.omlx/settings.json`），打开 App → 引导 → 下载 → 拖入一段公开测试片（`scripts/bench/manifest.toml` 里的）→ 拿到字幕。全程不打开设置。另外确认：下载中拖入的视频显示「等待模型下载完成…」，下完自动开始；界面和后台队列同时用时 `polysub engine status` 只有一个实例；空闲 10 分钟后引擎退出。
4. **已有用户不受影响**：用现在的配置文件升级后，识别 / 翻译仍走 oMLX，默认值、行为不变。
5. **新界面走一遍**（真机外观）：侧栏、工具栏（统一标题栏）、深浅色切换、设置页两层、「模型」页、首次引导、任务右键「用其他质量重新翻译」、字幕编辑器 ⌘F / ⌘S、⌘⌫ 只在任务列表里生效。重拍 README 截图 `docs/images/tasks-zh-CN.png`（用公开授权视频，不用私人样片）。

## B. 测数据（每项结果写进 builtin-engine.md 对应小节）

6. **Q0 基线补齐**（[Q0](builtin-engine.md#q0翻译质量评测先建尺子再改)）：用**旧默认**（`--set translate.think=low --set translate.think_budget=1024 --set translate.batch_size=20 --set asr.second_pass=all`，并关掉 Q1 各项：`glossary=false careful_prompt=false continuation_marks=false check_output=false lookahead_lines=0`）跑三段公开片的全流程；`freeze` 冻结识别结果并提交 `scripts/bench/data/<id>/asr.json`（公开授权，可以进仓库）；只重跑翻译的用时；本机 27B 开思考做 LLM 评分（`bench.py judge`），得到错误率基线。日语一段已有部分数据（见 Q0 小节）。
7. **S0 验收**：同一部长片（私人样片，只记数字）新默认下的全流程用时和各步骤占比；第二遍识别「按需」与「全部」的用时和人名修正率；快速档 + 40 行的 Q0 错误率不得高于基线，否则该项退回旧默认。oMLX 并发 2 / 4 的用时与内存。
8. **Q1 逐项对比**：每个开关单独关掉跑一遍 Q0（`--set translate.xxx=false`），记下错误率和用时；`lookahead_lines` 试 0 / 5 / 10 / 20；思考 关 / low 512 / low 1024 / medium。保留有效的，改 `config.py` 里的默认值。
9. **R0 选型**（整节在本机做，见 [R0](builtin-engine.md#r0选型实测)）：whisper.cpp large-v3-turbo 各量化、mlx-whisper 对照；翻译候选（Qwen3 1.7B / 4B / 8B、Hunyuan-MT 等）；llama-server `-np 1/2/4`；8 / 16 GB 机器的档位。结果决定 `manifest.py` 的 `TIERS` 和模型，写进 R0 小节。内置档位的质量要满足计划开头的「质量底线」。
10. **R3 验收**：用一个清单外的 GGUF（「文件…」或 `hf:…`）和 oMLX Qwen3-ASR 各跑通一段样片。

## C. 要负责人决定

11. **发布**：`main` 上已有 U0、S0、Q1、R1–R3、监视文件夹。原计划 0.4.0（U0 + S0）、0.4.x（Q1）、0.5.0（R1 + R2）、0.6.0（R3）分开发；现在代码都在一起，建议 A、B 做完后直接发 0.5.0（或按原计划从某个提交拉分支发 0.4.0）。决定后代理写 `docs/releases/版本号.md` 和版本号改动，负责人打标签。
12. **待定事项 2（免 Key 机翻）**：R4 第 1 项，要不要做 Google / 微软免费机翻作「极速」档（会把字幕文本发给第三方，接口随时可能失效）。
13. **Windows 计划**：原定 R2 完成后恢复，从 W0 开始；W1 的默认接口问题已随内置引擎消失（两个平台默认都是内置）。恢复前先确认 [windows.md](windows.md) 的待定事项 1–4（测试机器、识别定位、签名证书等）。
14. **R4 其余两项**：词级时间戳断句、Windows NVIDIA 的 faster-whisper，都要先有 R0 / W2 的实测数据。
