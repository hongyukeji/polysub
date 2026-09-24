# PolySub 配置。复制为 config.sh 后按需修改（config.sh 不进 Git，里面可以放 API Key）：
#   cp config.example.sh config.sh && chmod 600 config.sh
# 所有项都能被同名环境变量或命令行参数覆盖。

# ---- 默认语言 ----
POLYSUB_FROM="${POLYSUB_FROM:-ja}"        # 视频原语言；auto = 自动识别（短句容易判错，建议写明）
POLYSUB_TO="${POLYSUB_TO:-zh-Hans}"       # 字幕目标语言（BCP 47）：zh-Hans zh-Hant en ja ko fr de es ...

# ---- 翻译用哪家模型 ----
# local    = 本机 oMLX（免费、离线、不审查内容）
# deepseek = DeepSeek 官方 API
# qwen     = 阿里云百炼（通义千问官方 API）
POLYSUB_PROVIDER="${POLYSUB_PROVIDER:-local}"
POLYSUB_THINK="${POLYSUB_THINK:-off}"     # 翻译思考档位：off（快） | low | medium（最准，慢）

# ---- 本机 oMLX（语音识别永远走这里；翻译 provider=local 时也走这里） ----
POLYSUB_OMLX="${POLYSUB_OMLX:-http://127.0.0.1:8888}"
POLYSUB_OMLX_KEY="${POLYSUB_OMLX_KEY:-}"            # oMLX 的 API Key
POLYSUB_ASR_MODEL="${POLYSUB_ASR_MODEL:-Qwen3-ASR-1.7B-8bit}"
POLYSUB_LOCAL_MODEL="${POLYSUB_LOCAL_MODEL:-qwen3.8-27b-4bit}"

# ---- DeepSeek（https://platform.deepseek.com 申请 Key） ----
DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"
POLYSUB_DEEPSEEK_BASE="${POLYSUB_DEEPSEEK_BASE:-https://api.deepseek.com}"
POLYSUB_DEEPSEEK_MODEL="${POLYSUB_DEEPSEEK_MODEL:-deepseek-flash}"

# ---- 阿里云百炼 / 通义千问（https://bailian.console.aliyun.com 申请 Key） ----
DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-}"
POLYSUB_QWEN_BASE="${POLYSUB_QWEN_BASE:-https://dashscope.aliyuncs.com/compatible-mode}"
POLYSUB_QWEN_MODEL="${POLYSUB_QWEN_MODEL:-qwen3.8-max}"   # 便宜的选择：qwen3.8-27b（与本机同款）

# ---- 云端被内容审核拒绝时，是否自动改用本机模型翻译该批 ----
POLYSUB_FALLBACK_LOCAL="${POLYSUB_FALLBACK_LOCAL:-1}"
