# 翻译质量评测（scripts/bench）

[内置引擎计划](../../docs/plans/builtin-engine.md) 的 Q0：先建尺子，再改。给定一组配置，跑出字幕并统计用时、token 数、JSON 失败率、残留原文 / 混入其他语言的行数，输出与参考字幕的逐行对照表，再抽样人工或 LLM 标注，算出错误率。

## 公开评测集

见 [`manifest.toml`](manifest.toml)：日语访谈（ja→zh-Hans）、英语短片 Tears of Steel（en→zh-Hans）、中文翻拍版（zh→en），均为 CC BY / CC BY-SA，附来源与作者。视频由 `fetch` 下载到用户缓存目录并校验 sha256，**不进仓库**。

`data/<id>/` 放按视频许可可以再分发的东西：基线识别结果 `asr.json`（冻结后，只重跑翻译时不需要视频和识别服务）和基线译文的标注稿。

## 用法

```sh
uv run python scripts/bench/bench.py fetch                          # 下载公开评测集
uv run python scripts/bench/bench.py run --item all --asr video     # 全流程（需要识别服务）
uv run python scripts/bench/bench.py freeze RUN/ITEM --item ID      # 把识别结果冻结为 data/ID/asr.json
uv run python scripts/bench/bench.py run --item all                 # 只重跑翻译（用冻结的识别结果）
uv run python scripts/bench/bench.py run --item all --set translate.think=off --set translate.batch_size=40
uv run python scripts/bench/bench.py sheet RUN/ITEM -n 60 -o sheet.csv          # 固定抽样的空白标注表
uv run python scripts/bench/bench.py judge RUN/ITEM --sample sheet.csv --judge-model M --judge-think low
uv run python scripts/bench/bench.py summarize sheet.csv --against other.csv    # 错误率与标注一致率
```

私人视频用 `run --video V --target zh-Hans [--reference R.srt]` 或 `run --asr-json asr.json`；结果、标注只放本机，不进仓库，计划里只记数字。

配置依次叠加：用户 `config.toml` → `--config FILE` → `--set key=value`。结果默认写到 `<用户缓存>/polysub-bench/runs/<时间>-<标签>/<item>/`：`metrics.json`、`lines.json`、`compare.csv` / `compare.md`、输出字幕。

标注分类与计划里「翻译质量实测」的错误类型一致；LLM 评分只作参考，与人工标注的一致率够高后，小改动可只跑自动评分。
