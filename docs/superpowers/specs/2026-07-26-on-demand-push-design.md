# 按需推送（push-to-hiboard skill）· 设计文档

日期：2026-07-26 · 状态：已实现（用户批准跳过独立 plan，直接实现）

## 决策记录

| 决策点 | 结论 | 关键理由 |
|---|---|---|
| 卡片归属 | **3 卡位轮转**（默认），可配 `manualSlots` | 单卡会被连续推送冲掉未读内容；每次新卡永久堆积（无删除接口）；轮转恒定占位且保留最近 3 条 |
| 塞进状态卡方案 | **否决** | 负一屏列表标题即索引，文档塞进状态卡等于降级为不可见附录 |
| 周期性内容 | `topic` 字段逃生门 → 固定 ID `claude_code_topic_<slug>` | 日报被轮转挤掉不合理；文档明确警告主题卡永久存在 |
| 触发形态 | plugin skill（自然语言触发），不做 command | skill 正文可教 Claude 字段映射与写作约束；command 纯冗余 |
| 内容传递 | JSON 文件 + `--push <file>` | 正文是任意 markdown，命令行参数必被 shell 转义破坏（today-task 教训） |
| 退出码 | `--push` 失败 exit 1 | 区别于 hook 恒零：这是 Claude 主动调用的 CLI，失败必须可感知 |

## 字段映射（依据 API 契约 §6.2）

`summary`=列表标题（用户唯一先看到的）、`source`=展开态主标题、
`result`=展开态副标题、`topic`→`scheduleTaskId`（不显示）。

## 轮转算法

`state.json["manual_slots"] = {"1": ts, ...}`，flock 内选 `min(ts)` 槽并盖时间戳，
卡 ID = `claude_code_manual_<slot>`。主题卡完全不触碰轮转状态。

## 顺带修复（同批次）

1. **摘要无头会话无限递归**（线上事故）：`claude -p` 子进程会触发本插件 hooks，
   其 Stop 又 spawn 摘要 → 无限循环烧 token。修复：`llm_summarize` 给子进程注入
   `HIBOARD_SUMMARIZING=1`，hook 入口检测到即刻退出。
2. **未配置零副作用**：`handle_event` 前置 config gate——此前未配置也会 spawn
   `claude -p`，违背原设计 §9。
3. **渲染错位**：「已结束」卡片仍显示「正在处理：…」。改为存原始 prompt，
   渲染层按状态决定正文（running 加前缀 / ended 只显示 summary）。
