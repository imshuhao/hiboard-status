"""全局常量。改动前必读 docs/hiboard-api-spec.md（字段显示映射反直觉）。"""

VERSION = "0.4.1"

DEFAULT_ENDPOINT = ("https://hiboard-claw-drcn.ai.dbankcloud.cn"
                    "/distribution/message/cloud/claw/msg/upload")
CARD_ID = "claude_code_status"

# 长度均按 UTF-16 码元计（服务端 Java String.length 语义，emoji 计 2）
MAX_CARD_UTF16 = 28000        # 正文总量（服务端上限 30720，留余量）
MAX_PROJECT_UTF16 = 3000      # 单项目正文预算（「上轮」行另计 ~300）
MAX_PROMPT_UTF16 = 60
MAX_LAST_SUMMARY_UTF16 = 300  # 「↳ 上轮」行

STALE_SECS = 2 * 3600         # running/waiting 超过此时长降级为「状态未知」
PRUNE_SECS = 7 * 24 * 3600    # 项目条目与 session 映射的保留期

SUMMARY_PLACEHOLDER = "（摘要生成中…）"

DEBOUNCE_SECS = 3          # 后台推送合并窗口（配置 pushDebounce 可覆盖）
FLUSH_CLAIM_SECS = 45      # flusher 认领过期时间（进程被杀不至于永久卡住）
SUMMARY_MIN_UTF16 = 120    # 回复不超过此长度视为琐碎回合，原文即摘要不起 LLM

MAX_CELLS_RENDER = 3       # 每项目最多渲染的会话格子行数
CELL_PRUNE_SECS = 24 * 3600   # 格子超过此龄直接删除（stale 只是不渲染）
REGISTRY_GRACE_SECS = 60   # 注册表活性清理的新格宽限期（防注册竞态误删）
