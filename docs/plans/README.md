# 开发计划

| 计划 | 内容 | 状态 |
| --- | --- | --- |
| [内置引擎（开箱即用）](builtin-engine.md) | 全选、提速、内置 whisper.cpp / llama.cpp、模型管理、高级调优 | 代码已合入；验收与选型见本机任务 |
| [Windows 版](windows.md) | Windows 打包、安装包、Scoop | 暂缓，等内置引擎 R2 完成后恢复 |
| [本机任务](local-tasks.md) | 需要在负责人 Mac 上做的验证、实测和决定 | 待办 |

## 给执行代理的说明


项目负责人：[@hongyukeji](https://github.com/hongyukeji)。以下是本仓库的约定，执行本计划时照做。

**仓库与分支**
- 源码 `hongyukeji/polysub`。每个阶段一个分支，命名见各计划（如 `engine/u0`、`windows/w1`），通过 PR 合入 `main`；一个 PR 只做一个阶段。开工前先 `git pull`，多个代理并行时只改本阶段「涉及文件」里列出的文件，需要改别的文件先在 PR 里说明。
- 提交身份用仓库/全局 git config（鸿宇 `<hongyusvip@gmail.com>`），不要用 `-c user.email=…` 覆盖成别的邮箱。
- **不要**自行推送 `v*` 标签、公开 Release、新建 GitHub 仓库（如 scoop-bucket）、购买任何服务；这些要负责人确认。

**开发**
- 环境：`uv sync --extra gui`；测试：`uv run python -m unittest discover -s tests`；macOS 打包：`packaging/macos/build.sh`。
- 代码风格跟现有代码一致：界面文案中文（经 `tr()`），代码注释英文，注释密度与周围一致；平台差异集中到 `src/polysub/system.py`，不要在各处散落 `sys.platform` 判断。
- **不改变 macOS 版的行为**。macOS 的 CI、打包、Homebrew 安装（配方在 `hongyukeji/homebrew-tap`，把 .app 以 zip 放在 libexec、安装后解压以保持签名）都必须保持可用。
- 新增依赖要写进 `pyproject.toml` 并更新 `uv.lock`；打进发行包的第三方组件写进 `THIRD_PARTY_NOTICES.md`（许可证、来源）。

**实测优先**
- 关于速度、质量、体积的结论只认实际跑出来的数据，不引用估算或上游宣传数字；写结论时注明机器、样片、参数。
- 基线数据：macOS 版在 M4 Max 上，默认参数下一部 2 小时 17 分的视频全流程 9.6–11.3 分钟；一部 4 小时的视频 107.7 分钟（各步骤占比见 [内置引擎计划](builtin-engine.md)「现状实测」）。
- **需要真机的工作**：GitHub 的 macOS 服务器是没有 Apple GPU 加速的虚拟机，速度与质量对比（如内置引擎 R0）必须在负责人的 Apple Silicon Mac 上本地运行；云端代理只做代码、单元测试和打包。
- 测试素材用公开授权的视频（例如 Blender 开放电影）或测试中临时生成的音频；需要模拟云端接口时自己写一个小的模拟服务。**不要把视频提交进仓库。**
- **隐私**：本仓库是公开的。不要在代码、测试、文档、提交说明里写入本机路径、私人文件名、测试素材的名称或台词、任何密钥。

**文档与发布**
- 用户可见的变化写进 `CHANGELOG.md`（英文）和 README（中文 `README.md` 与英文 `README.en.md` 同步修改）。
- 发布遵循 [`docs/releases/README.md`](../releases/README.md)：先写 `docs/releases/版本号.md`，打标签生成草稿，验证后由负责人公开。
- 每完成一个阶段，更新**所属计划文件**顶部的状态表，并在对应阶段下写明实际做法与计划的偏差、验收结果（通过/未覆盖项）。
- 计划里标为「待定」的事项，负责人没有另行指示前按计划里的建议执行，并在 PR 描述里写明「按建议执行了待定事项 N」，方便负责人否决。
